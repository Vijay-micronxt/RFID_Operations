"""Pieces sold without their tag read (tag damaged or missing).

Every piece of the item in stock carries a tag, so the sale leaves one tag too
many in the warehouse: it stands for a piece that is gone, and nobody can tell
which tag it is. An RFID Unread Sale keeps that count open. The next stock
count of the item does not read the tag, and settling it there frees exactly
that tag against the original invoice.
"""

import frappe
from frappe import _
from frappe.utils import cint, now_datetime

from rfid_retail import rfid
from rfid_retail.settings import get as setting, has_station, hand_pick_check

REASONS = ("Tag Damaged", "Tag Missing")


def allowed():
	"""RFID Settings > Allow sale when the tag will not read (on unless switched off)."""
	value = setting("allow_unread_tag_sale")
	return bool(hand_pick_check() and (value is None or cint(value)))


def needs_manager(user=None):
	value = setting("unread_sale_needs_manager")
	return (value is None or cint(value)) and not has_station("Manager Override", user)


def check_approver(manager=None, password=None):
	"""The user who approves an unread-tag sale: the cashier when allowed, else a manager who signs in here."""
	if not needs_manager():
		return frappe.session.user
	if not manager or not password:
		frappe.throw(_("A manager has to approve this sale. Enter the manager's login and password."),
			title=_("Manager approval"))
	from frappe.utils.password import check_password

	user = frappe.db.get_value("User", {"name": manager, "enabled": 1}) or frappe.db.get_value(
		"User", {"username": manager, "enabled": 1}) or frappe.db.get_value("User", {"mobile_no": manager, "enabled": 1})
	try:
		if not user:
			raise frappe.AuthenticationError
		check_password(user, password)
	except frappe.AuthenticationError:
		frappe.clear_last_message()
		frappe.throw(_("Wrong manager login or password."), title=_("Manager approval"))
	if not has_station("Manager Override", user):
		frappe.throw(_("{0} cannot approve this. Ask a store manager.").format(user), title=_("Manager approval"))
	return user


def outstanding(item_code, warehouse):
	"""Tags of the item in the warehouse that still stand for pieces sold unread."""
	rows = frappe.get_all("RFID Unread Sale", {"item_code": item_code, "warehouse": warehouse, "status": "Open"},
		["qty", "settled_qty"])
	return sum(max(cint(r.qty) - cint(r.settled_qty), 0) for r in rows)


def open_sales(item_code, warehouse):
	"""Open records, oldest first, with how many tags each still waits for."""
	rows = frappe.get_all("RFID Unread Sale", {"item_code": item_code, "warehouse": warehouse, "status": "Open"},
		["name", "qty", "settled_qty", "reference_name", "reason", "sold_on"], order_by="creation asc")
	for r in rows:
		r.waiting = max(cint(r.qty) - cint(r.settled_qty), 0)
	return [r for r in rows if r.waiting]


def record(doc, item_code, warehouse, qty, approvals):
	"""After the sale: one open record for the pieces whose tags stayed behind."""
	approvals = approvals or []
	reasons = sorted({a.reason for a in approvals if a.reason})
	notes = [a.note for a in approvals if a.note]
	approvers = sorted({a.approved_by for a in approvals if a.approved_by})
	urs = frappe.get_doc({
		"doctype": "RFID Unread Sale",
		"status": "Open",
		"item_code": item_code,
		"warehouse": warehouse,
		"qty": qty,
		"reason": ", ".join(reasons) or _("Billed without reading the tag"),
		"note": "\n".join(notes) or None,
		"approved_by": approvers[0] if approvers else None,
		"reference_doctype": doc.doctype,
		"reference_name": doc.name,
		"pos_profile": doc.get("pos_profile"),
		"basket": doc.get("custom_rfid_basket"),
		"sold_by": frappe.session.user,
		"sold_on": now_datetime(),
	})
	urs.insert(ignore_permissions=True)
	return urs


def settle(name, epc, audit_session=None):
	"""Free the tag a stock count did not find, as the tag of the piece this record sold."""
	urs = frappe.get_doc("RFID Unread Sale", name, for_update=True)
	if urs.status != "Open" or cint(urs.settled_qty) >= cint(urs.qty):
		frappe.throw(_("{0} is {1}; it waits for no more tags.").format(urs.name, urs.status))
	tag = frappe.get_doc("RFID Tag", rfid.normalize(epc), for_update=True)
	if tag.item_code != urs.item_code or tag.warehouse != urs.warehouse or tag.serial_no \
		or tag.status not in rfid.IN_STOCK_TAG or not tag.current_assignment:
		frappe.throw(_("Tag {0} is not an in-stock tag of {1} in {2}.").format(tag.name, urs.item_code, urs.warehouse))
	if tag.basket and frappe.db.get_value("RFID Basket", tag.basket, "status") == "Open":
		frappe.throw(_("Tag {0} is on an open bill at a counter ({1}).").format(tag.name, tag.basket))
	if urs.reference_doctype == "Sales Invoice":
		frappe.db.set_value("RFID Tag Assignment", tag.current_assignment, {"sales_invoice": urs.reference_name,
			"sold_by": urs.sold_by, "sold_on": urs.sold_on})
	# The tag left with the garment (or is dead): it is not put back to use.
	rfid.close_assignment(tag.name, "Sold", new_tag_status=rfid.TAG_LOST, action="Sold",
		reference_doctype=urs.reference_doctype, reference_name=urs.reference_name,
		remarks=_("Sold on {0} without its tag read ({1}); not found in stock count {2}").format(
			urs.reference_name, urs.name, audit_session or "-"))
	urs.settled_qty = cint(urs.settled_qty) + 1
	urs.settled_tags = "\n".join([t for t in (urs.settled_tags or "").split("\n") if t] + [tag.name])
	urs.audit_session = audit_session or urs.audit_session
	urs.settled_by, urs.settled_on = frappe.session.user, now_datetime()
	if urs.settled_qty >= cint(urs.qty):
		urs.status = "Settled"
	urs.save(ignore_permissions=True)
	return urs


def on_invoice_cancel(doc):
	"""The pieces come back into stock: nothing is waiting for a tag any more."""
	for name in frappe.get_all("RFID Unread Sale", {"reference_doctype": doc.doctype, "reference_name": doc.name,
		"status": ["in", ("Open", "Settled")]}, pluck="name"):
		urs = frappe.get_doc("RFID Unread Sale", name)
		urs.status = "Cancelled"
		urs.add_comment("Comment", _("{0} cancelled.").format(doc.name))
		urs.save(ignore_permissions=True)
