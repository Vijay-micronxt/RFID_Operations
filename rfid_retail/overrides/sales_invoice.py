"""RFID controls on Sales Invoice / POS Invoice.

These hooks run for every entry path - RFID counter, POS Next, desk form,
REST - so a set can never be finalised unless all its components were verified
on the counter's basket, and a tag is only released once the sale is submitted
(never on a failed payment or a failed submit, which roll back). The exact tags
sold stay on the invoice row (custom_rfid_manifest) and in the tag history.
Rules come from RFID Settings; with RFID switched off they do nothing.
"""

import json

import frappe
from frappe import _
from frappe.utils import cint, flt, now_datetime

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
	basket_reads, basket_tags, approved = {}, {}, {}
	if doc.get("custom_rfid_basket"):
		basket = frappe.get_doc("RFID Basket", doc.custom_rfid_basket)
		if basket.status == "Cancelled":
			frappe.throw(_("RFID basket {0} is cancelled.").format(basket.name))
		if basket.status == "Billed" and basket.sales_invoice not in (None, "", doc.name):
			frappe.throw(_("RFID basket {0} is already billed on {1}.").format(basket.name, basket.sales_invoice))
		for read in basket.reads:
			if read.serial_no:
				basket_reads.setdefault(read.serial_no, set()).add(read.component or read.serial_no)
			elif read.item_code:
				basket_tags.setdefault(read.item_code, []).append(read.epc)
		approved = _approved_unread(basket)

	seen = set()
	_check_untagged_stock(doc, basket_tags, approved)
	approved_left = dict(approved)
	for row in rfid_rows(doc):
		if not rfid.has_serial(row.item_code):
			row.custom_rfid_manifest = json.dumps(_item_rows_tags(doc, row, basket_tags, approved_left),
				separators=(",", ":"))
			continue
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


def _approved_unread(basket):
	"""Pieces per item the counter approved to sell although their tag will not read."""
	from rfid_retail import unread

	if not unread.allowed():
		return {}
	out = {}
	for r in basket.get("unread") or []:
		out[r.item_code] = out.get(r.item_code, 0) + cint(r.qty)
	return out


def _check_untagged_stock(doc, basket_tags, approved=None):
	"""Units billed without a tag read must be units that have no tag. Selling more than the
	untagged stock means a tagged piece went out without its tag being read, unless the counter
	approved it as a piece whose tag will not read. RFID Settings > Check RFID items picked by hand."""
	from rfid_retail.settings import hand_pick_check

	if not hand_pick_check():
		return
	billed = {}
	for row in rfid_rows(doc):
		if rfid.has_serial(row.item_code):
			continue
		key = (row.item_code, row.warehouse)
		billed[key] = billed.get(key, 0) + abs(cint(flt(row.get("stock_qty") or row.qty)))
	for (item_code, warehouse), qty in billed.items():
		short = qty - len(basket_tags.get(item_code, []))
		if short <= 0:
			continue
		free = rfid.untagged_qty(item_code, warehouse)
		if short > free + (approved or {}).get(item_code, 0):
			rfid.log_exception_on_failure("Manual Override",
				_("{0} x {1} billed by hand on {2} but only {3} piece(s) in {4} have no tag.").format(
					short, item_code, doc.get("pos_profile") or _("desk"), free, warehouse),
				severity="Medium", pos_profile=doc.get("pos_profile"), reference_doctype=doc.doctype,
				reference_name=doc.name)
			frappe.throw(_("{0}: {1} piece(s) billed without reading a tag, but only {2} piece(s) in {3} have no "
				"tag. Put the tagged pieces on the RFID tray.").format(item_code, short, free, warehouse),
				title=_("Read the RFID tag"))


def _item_rows_tags(doc, row, basket_tags, approved_left=None):
	"""Tags read on the counter for this row of a non-serial item. Each tag counts once on the invoice."""
	qty = abs(cint(flt(row.get("stock_qty") or row.qty)))
	pool = basket_tags.get(row.item_code, [])
	tags, basket_tags[row.item_code] = pool[:qty], pool[qty:]
	for code in tags:
		res = rfid.resolve_code(code)
		if res.kind != "tag" or res.item_code != row.item_code:
			frappe.throw(_("Row {0}: tag {1} is no longer on a unit of {2}. Read the tray again.").format(
				row.idx, code, row.item_code))
		if res.tag_status != rfid.TAG_ASSIGNED:
			frappe.throw(_("Row {0}: tag {1} is {2}.").format(row.idx, code, res.tag_status))
		if res.warehouse != row.warehouse:
			frappe.throw(_("Row {0}: tag {1} is recorded in {2}, not {3}.").format(row.idx, code, res.warehouse,
				row.warehouse))
		if res.basket and res.basket != doc.get("custom_rfid_basket"):
			frappe.throw(_("Tag {0} is being billed on another counter ({1}).").format(code, res.basket))
	short = qty - len(tags)
	if short > 0:
		where = doc.get("pos_profile") or _("desk")
		approved_left = approved_left if approved_left is not None else {}
		excused = min(short, approved_left.get(row.item_code, 0))
		approved_left[row.item_code] = approved_left.get(row.item_code, 0) - excused
		if short > excused and cint(setting("require_scan_for_sale")) and not cint(setting("allow_label_fallback")):
			rfid.log_exception_on_failure("Manual Override",
				_("{0} x {1} billed without RFID reads ({2}).").format(short, row.item_code, where),
				severity="Medium", pos_profile=doc.get("pos_profile"), reference_doctype=doc.doctype,
				reference_name=doc.name)
			frappe.throw(_("Row {0}: read the RFID tags of {1} - {2} of {3} read.").format(
				row.idx, row.item_code, len(tags), qty))
		doc.flags.rfid_untagged_sales = (doc.flags.rfid_untagged_sales or []) + [(row.item_code, short, row.warehouse)]
	return [{"tag": code, "item_code": row.item_code} for code in tags]


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
		if rfid.has_serial(row.item_code):
			for serial_no in get_row_serials(row):
				_mark_sold(doc, row, serial_no)
		else:
			for entry in _manifest(row):
				_sell_tag(doc, row, entry["tag"])
	_record_untagged_sales(doc)
	if doc.get("custom_rfid_basket"):
		frappe.db.set_value("RFID Basket", doc.custom_rfid_basket, {"status": "Billed", "sales_invoice": doc.name, "customer": doc.customer})


def _record_untagged_sales(doc):
	from rfid_retail import unread

	untagged = {}
	for item_code, short, warehouse in doc.flags.rfid_untagged_sales or []:
		untagged[(item_code, warehouse)] = untagged.get((item_code, warehouse), 0) + short
	approvals = []
	if untagged and doc.get("custom_rfid_basket"):
		approvals = frappe.get_doc("RFID Basket", doc.custom_rfid_basket).get("unread") or []
	for (item_code, warehouse), short in untagged.items():
		# Stock is already reduced here: more tags than stock means tagged pieces went out unread.
		extra = rfid.count_item_tags(item_code, warehouse) - cint(flt(frappe.db.get_value("Bin",
			{"item_code": item_code, "warehouse": warehouse}, "actual_qty")))
		# tags already waiting on earlier unread sales are not counted twice
		pending = min(short, extra - unread.outstanding(item_code, warehouse))
		urs = pending > 0 and unread.record(doc, item_code, warehouse, pending,
			[a for a in approvals if a.item_code == item_code])
		if urs:
			message = _("{0} x {1} sold on {2} without reading their tags ({3}). {4} tag(s) of {1} in {5} stand for "
				"pieces that are gone ({6}); a stock count of {1} settles them.").format(
				short, item_code, doc.name, urs.reason, pending, warehouse, urs.name)
		elif extra > 0:
			message = _("{0} x {1} sold on {2} without reading their tags. {3} tag(s) of {1} in {4} now stand for "
				"stock that is gone; remove those tags or mark them lost after the stock count.").format(
				short, item_code, doc.name, extra, warehouse)
		else:
			message = _("{0} x {1} sold on {2} without reading tags (untagged stock in {3}).").format(
				short, item_code, doc.name, warehouse)
		rfid.log_exception("Barcode Fallback", message, severity="Medium" if extra > 0 else "Low",
			pos_profile=doc.get("pos_profile"), reference_doctype=doc.doctype, reference_name=doc.name)


def _manifest(row):
	try:
		data = json.loads(row.get("custom_rfid_manifest") or "[]")
	except ValueError:
		return []
	return [e for e in data if isinstance(e, dict) and e.get("tag")]


def free_at_once():
	return not (setting("tag_release_mode") or "").startswith("Free the tag after")


def _sell_tag(doc, row, code):
	"""Record the sale on the tag's current use, then free the tag (or keep it until detached)."""
	tag = frappe.get_doc("RFID Tag", code, for_update=True)
	if not tag.current_assignment:
		return
	frappe.db.set_value("RFID Tag Assignment", tag.current_assignment,
		{"sales_invoice": doc.name, "sold_rate": row.rate, "sold_by": frappe.session.user, "sold_on": now_datetime()})
	if free_at_once():
		rfid.close_assignment(code, "Sold", action="Sold", reference_doctype=doc.doctype, reference_name=doc.name,
			amount=row.rate, remarks=_("Sold and freed for reuse"))
		return
	previous = tag.status
	tag.update({"status": rfid.TAG_SOLD, "basket": None})
	tag.save(ignore_permissions=True)
	rfid.log_tag_event(tag, "Sold", previous_status=previous, reference_doctype=doc.doctype,
		reference_name=doc.name, amount=row.rate)


def _mark_sold(doc, row, serial_no):
	frappe.db.set_value("Serial No", serial_no,
		{"custom_rfid_status": rfid.SOLD, "custom_sales_invoice": doc.name, "custom_rfid_basket": None},
		update_modified=False)
	for comp in rfid.get_components(serial_no):
		frappe.db.set_value("Garment Component", comp.name, {"status": "Sold", "sales_invoice": doc.name})
	for asg in frappe.get_all("RFID Tag Assignment", {"serial_no": serial_no, "status": "Active"}, ["name", "tag"]):
		_sell_tag(doc, row, asg.tag)


def _on_return(doc):
	"""Returned units go on hold (serials) or wait for a new tag (non-serial).

	A tag that was kept on the sold garment (detach mode) and comes back with it is
	put back to work on the same unit.
	"""
	for row in rfid_rows(doc):
		if not rfid.has_serial(row.item_code):
			_return_item_tags(doc, row)
			continue
		for serial_no in get_row_serials(row):
			frappe.db.set_value("Serial No", serial_no, "custom_rfid_status", rfid.RETURNED, update_modified=False)
			for comp in rfid.get_components(serial_no):
				frappe.db.set_value("Garment Component", comp.name,
					{"status": "Returned - Hold" if not comp.rfid_tag else "Tagged"})
			rfid.log_exception("Manual Override",
				_("{0} returned on {1}. Verify all pieces and re-tag at the mapping station before resale.").format(
					serial_no, doc.name),
				severity="Low", serial_no=serial_no, reference_doctype=doc.doctype, reference_name=doc.name)


def _return_item_tags(doc, row):
	qty = abs(cint(flt(row.get("stock_qty") or row.qty)))
	original = doc.get("return_against")
	still_on = []
	if original:
		still_on = frappe.get_all("RFID Tag", {"status": rfid.TAG_SOLD, "item_code": row.item_code,
			"current_assignment": ["in", frappe.get_all("RFID Tag Assignment",
				{"sales_invoice": original, "status": "Active", "item_code": row.item_code}, pluck="name") or [""]]},
			pluck="name", limit=qty)
	for code in still_on:
		tag = frappe.get_doc("RFID Tag", code, for_update=True)
		tag.update({"status": rfid.TAG_ASSIGNED, "warehouse": row.warehouse})
		tag.save(ignore_permissions=True)
		frappe.db.set_value("RFID Tag Assignment", tag.current_assignment,
			{"sales_invoice": None, "sold_rate": 0, "sold_by": None, "sold_on": None, "warehouse": row.warehouse})
		rfid.log_tag_event(tag, "Returned", previous_status=rfid.TAG_SOLD, reference_doctype=doc.doctype,
			reference_name=doc.name)
	if qty > len(still_on):
		rfid.log_exception("Untagged Stock",
			_("{0} x {1} returned on {2} without a tag. Tag them at the Tag garments station before resale.").format(
				qty - len(still_on), row.item_code, doc.name),
			severity="Low", reference_doctype=doc.doctype, reference_name=doc.name)


def on_cancel(doc, method=None):
	if not is_enabled():
		return
	from rfid_retail.overrides.stock import restore_tags

	if cint(doc.get("is_return")):
		for row in rfid_rows(doc):
			for serial_no in get_row_serials(row) if rfid.has_serial(row.item_code) else []:
				sold_on = frappe.db.get_value("Serial No", serial_no, "custom_sales_invoice")
				frappe.db.set_value("Serial No", serial_no,
					"custom_rfid_status", rfid.SOLD if sold_on else rfid.PENDING, update_modified=False)
		# Tags put back to work by the return go back to the sold state.
		for ev in frappe.get_all("RFID Tag Event", {"reference_doctype": doc.doctype, "reference_name": doc.name,
			"action": "Returned"}, ["tag", "cycle"]):
			tag = frappe.get_doc("RFID Tag", ev.tag, for_update=True)
			if cint(tag.cycle) == cint(ev.cycle) and tag.status == rfid.TAG_ASSIGNED:
				tag.status = rfid.TAG_SOLD
				tag.save(ignore_permissions=True)
				frappe.db.set_value("RFID Tag Assignment", tag.current_assignment, "sales_invoice", doc.return_against)
				rfid.log_tag_event(tag, "Correction", previous_status=rfid.TAG_ASSIGNED,
					reference_doctype=doc.doctype, reference_name=doc.name, reason=_("Return cancelled"))
		return

	for row in rfid_rows(doc):
		for serial_no in get_row_serials(row) if rfid.has_serial(row.item_code) else []:
			if frappe.db.get_value("Serial No", serial_no, "custom_sales_invoice") != doc.name:
				continue
			for comp in rfid.get_components(serial_no):
				frappe.db.set_value("Garment Component", comp.name,
					{"status": "Tagged" if comp.rfid_tag else "Pending Tag", "sales_invoice": None})
			# Back to Pending, then Ready again only if every piece still carries its tag.
			frappe.db.set_value("Serial No", serial_no,
				{"custom_sales_invoice": None, "custom_rfid_status": rfid.PENDING}, update_modified=False)
	# Tags kept on the sold garments (detach mode) simply go back to work.
	for asg in frappe.get_all("RFID Tag Assignment", {"sales_invoice": doc.name, "status": "Active"},
		["name", "tag", "serial_no"]):
		frappe.db.set_value("RFID Tag Assignment", asg.name,
			{"sales_invoice": None, "sold_rate": 0, "sold_by": None, "sold_on": None})
		tag = frappe.get_doc("RFID Tag", asg.tag, for_update=True)
		previous, tag.status = tag.status, rfid.TAG_ASSIGNED
		tag.save(ignore_permissions=True)
		rfid.log_tag_event(tag, "Restored", previous_status=previous, reference_doctype=doc.doctype,
			reference_name=doc.name, reason=_("Sale cancelled"))
	# Tags freed at the sale: back on the same unit if nobody reused them, else flag for a new tag.
	restore_tags(doc, ("Sold",))
	from rfid_retail import unread

	unread.on_invoice_cancel(doc)
	for row in rfid_rows(doc):
		for serial_no in get_row_serials(row) if rfid.has_serial(row.item_code) else []:
			rfid.refresh_serial_readiness(serial_no)
	if doc.get("custom_rfid_basket"):
		frappe.db.set_value("RFID Basket", doc.custom_rfid_basket, "status", "Cancelled")
