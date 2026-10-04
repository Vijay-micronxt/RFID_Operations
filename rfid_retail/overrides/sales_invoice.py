"""RFID controls on Sales Invoice / POS Invoice.

These hooks run for every entry path - RFID counter, POS Next, desk form,
REST - so a set can never be finalised unless all its components were verified
on the counter's basket, and a garment is only released once the sale is
submitted. Rules come from RFID Settings; with RFID switched off they do nothing.
"""

import json

import frappe
from frappe import _
from frappe.utils import cint

from rfid_retail import rfid
from rfid_retail.settings import get as setting, is_enabled


def get_row_serials(row):
	# ERPNext may create the bundle during submit and write it to the DB only,
	# so the in-memory row can still be blank here.
	bundle = row.get("serial_and_batch_bundle") or (
		row.name and frappe.db.get_value(row.doctype, row.name, "serial_and_batch_bundle")
	)
	if bundle:
		from erpnext.stock.serial_batch_bundle import get_serial_nos as bundle_serials

		serials = bundle_serials(bundle)
		if serials:
			return serials
	from erpnext.stock.doctype.serial_no.serial_no import get_serial_nos

	return get_serial_nos(row.get("serial_no") or "")


def rfid_rows(doc):
	for row in doc.get("items") or []:
		if rfid.is_rfid_item(row.item_code):
			yield row


def before_submit(doc, method=None):
	if not is_enabled() or cint(doc.get("is_return")):
		return
	if cint(setting("block_untracked_items")):
		for row in doc.get("items") or []:
			if not rfid.is_rfid_item(row.item_code) and frappe.get_cached_value("Item", row.item_code, "is_stock_item"):
				frappe.throw(_("Row {0}: {1} has no RFID identity. Only RFID-tracked items can be sold here.").format(
					row.idx, row.item_code))
	basket_reads = {}
	if doc.get("custom_rfid_basket"):
		basket = frappe.get_doc("RFID Basket", doc.custom_rfid_basket)
		if basket.status == "Cancelled":
			frappe.throw(_("RFID basket {0} is cancelled.").format(basket.name))
		if basket.status == "Billed" and basket.sales_invoice not in (None, "", doc.name):
			frappe.throw(_("RFID basket {0} is already billed on {1}.").format(basket.name, basket.sales_invoice))
		for read in basket.reads:
			if read.serial_no:
				basket_reads.setdefault(read.serial_no, set()).add(read.component or read.serial_no)

	seen = set()
	for row in rfid_rows(doc):
		serials = get_row_serials(row)
		if len(serials) != abs(cint(row.qty)):
			frappe.throw(_("Row {0}: {1} is RFID tracked; pick exactly one serial per unit.").format(
				row.idx, row.item_code))
		manifest = []
		for serial_no in serials:
			if serial_no in seen:
				frappe.throw(_("{0} appears twice on this invoice.").format(serial_no))
			seen.add(serial_no)
			manifest.append(_check_serial(doc, row, serial_no, basket_reads))
		row.custom_rfid_manifest = json.dumps(manifest, separators=(",", ":"))


def _check_serial(doc, row, serial_no, basket_reads):
	sn = rfid.serial_info(serial_no)
	if not sn or sn.item_code != row.item_code:
		frappe.throw(_("Row {0}: {1} is not a unit of {2}.").format(row.idx, serial_no, row.item_code))
	if sn.custom_rfid_status != rfid.READY:
		frappe.throw(_("Row {0}: {1} is '{2}', not Ready for sale. Finish tag mapping / return checks first.").format(
			row.idx, serial_no, sn.custom_rfid_status or rfid.PENDING))

	other = sn.custom_rfid_basket
	if other and other != doc.get("custom_rfid_basket") and frappe.db.get_value("RFID Basket", other, "status") == "Open":
		counter = frappe.db.get_value("RFID Basket", other, "pos_profile")
		rfid.log_exception_on_failure("Counter Conflict",
			_("{0} is on open basket {1} at {2} while {3} tried to bill it.").format(
				serial_no, other, counter, doc.get("pos_profile") or _("desk")),
			severity="High", serial_no=serial_no, pos_profile=doc.get("pos_profile"),
			reference_doctype="RFID Basket", reference_name=other)
		frappe.throw(_("{0} is being billed on another counter ({1}).").format(serial_no, counter))

	entry = {"serial_no": serial_no, "item_code": sn.item_code}
	if not cint(sn.custom_is_garment_set):
		if cint(setting("require_scan_for_sale")) and serial_no not in basket_reads:
			rfid.log_exception_on_failure("Manual Override",
				_("{0} billed without an RFID counter read ({1}).").format(serial_no, doc.get("pos_profile") or _("desk")),
				severity="Medium", serial_no=serial_no, pos_profile=doc.get("pos_profile"),
				reference_doctype=doc.doctype, reference_name=doc.name)
			frappe.throw(_("Row {0}: {1} must be read at an RFID counter before it can be billed.").format(
				row.idx, serial_no))
		assignment = rfid.active_assignment_for(serial_no=serial_no)
		entry["tag"] = assignment and frappe.db.get_value("RFID Tag Assignment", assignment, "tag")
		return entry

	# Sets: only a basket that physically read every component can release the parent.
	comps = rfid.get_components(serial_no)
	verified = basket_reads.get(serial_no, set())
	missing = [c.role for c in comps if c.name not in verified]
	if missing:
		rfid.log_exception_on_failure("Incomplete Set",
			_("Billing of {0} blocked; not verified: {1}.").format(serial_no, ", ".join(missing)),
			severity="High", serial_no=serial_no, pos_profile=doc.get("pos_profile"),
			reference_doctype=doc.doctype, reference_name=doc.name)
		if not doc.get("custom_rfid_basket"):
			frappe.throw(_("{0} is a set. It can only be billed after every piece is read on an RFID counter.").format(
				serial_no))
		frappe.throw(_("{0} is incomplete. Missing pieces: {1}.").format(serial_no, ", ".join(missing)))
	entry["components"] = [{"component": c.name, "role": c.role, "tag": c.rfid_tag} for c in comps]
	return entry


def on_submit(doc, method=None):
	if not is_enabled():
		return
	if cint(doc.get("is_return")):
		_on_return(doc)
		return
	for row in rfid_rows(doc):
		for serial_no in get_row_serials(row):
			_mark_sold(doc, serial_no)
	if doc.get("custom_rfid_basket"):
		frappe.db.set_value("RFID Basket", doc.custom_rfid_basket, {"status": "Billed", "sales_invoice": doc.name, "customer": doc.customer})


def _mark_sold(doc, serial_no):
	frappe.db.set_value("Serial No", serial_no,
		{"custom_rfid_status": rfid.SOLD, "custom_sales_invoice": doc.name, "custom_rfid_basket": None},
		update_modified=False)
	for comp in rfid.get_components(serial_no):
		frappe.db.set_value("Garment Component", comp.name, {"status": "Sold", "sales_invoice": doc.name})
	for asg in frappe.get_all("RFID Tag Assignment", {"serial_no": serial_no, "status": "Active"}, ["name", "tag"]):
		frappe.db.set_value("RFID Tag Assignment", asg.name, "sales_invoice", doc.name)
		frappe.db.set_value("RFID Tag", asg.tag, "status", rfid.TAG_SOLD)


def _on_return(doc):
	"""Returned units go on hold. They are re-verified and re-tagged before resale."""
	for row in rfid_rows(doc):
		for serial_no in get_row_serials(row):
			frappe.db.set_value("Serial No", serial_no, "custom_rfid_status", rfid.RETURNED, update_modified=False)
			for comp in rfid.get_components(serial_no):
				frappe.db.set_value("Garment Component", comp.name,
					{"status": "Returned - Hold" if not comp.rfid_tag else "Tagged"})
			rfid.log_exception("Manual Override",
				_("{0} returned on {1}. Verify all pieces and re-tag at the mapping station before resale.").format(
					serial_no, doc.name),
				severity="Low", serial_no=serial_no, reference_doctype=doc.doctype, reference_name=doc.name)


def on_cancel(doc, method=None):
	if not is_enabled():
		return
	if cint(doc.get("is_return")):
		for row in rfid_rows(doc):
			for serial_no in get_row_serials(row):
				sold_on = frappe.db.get_value("Serial No", serial_no, "custom_sales_invoice")
				frappe.db.set_value("Serial No", serial_no,
					"custom_rfid_status", rfid.SOLD if sold_on else rfid.PENDING, update_modified=False)
		return
	for row in rfid_rows(doc):
		for serial_no in get_row_serials(row):
			if frappe.db.get_value("Serial No", serial_no, "custom_sales_invoice") != doc.name:
				continue
			for asg in frappe.get_all("RFID Tag Assignment",
				{"serial_no": serial_no, "status": "Active"}, ["name", "tag"]):
				frappe.db.set_value("RFID Tag Assignment", asg.name, "sales_invoice", None)
				frappe.db.set_value("RFID Tag", asg.tag, "status", rfid.TAG_ASSIGNED)
			for comp in rfid.get_components(serial_no):
				frappe.db.set_value("Garment Component", comp.name,
					{"status": "Tagged" if comp.rfid_tag else "Pending Tag", "sales_invoice": None})
			# Back to Pending, then Ready again only if every piece still carries its tag.
			frappe.db.set_value("Serial No", serial_no,
				{"custom_sales_invoice": None, "custom_rfid_status": rfid.PENDING}, update_modified=False)
			rfid.refresh_serial_readiness(serial_no)
	if doc.get("custom_rfid_basket"):
		frappe.db.set_value("RFID Basket", doc.custom_rfid_basket, "status", "Cancelled")
