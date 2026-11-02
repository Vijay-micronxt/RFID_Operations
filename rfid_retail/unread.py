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
# A piece out of the untagged stock (a return, a piece never tagged): sold by hand, no tag left behind
NO_TAG = "Piece has no tag"
NO_TAG_STATUS = "No Tag"


def allowed():
	"""RFID Settings > Allow sale when the tag will not read (on unless switched off)."""
	value = setting("allow_unread_tag_sale")
	return bool(hand_pick_check() and (value is None or cint(value)))


APPROVER_STATION = "Unread Sale Approval"


def needs_manager(user=None):
	"""An approver other than the cashier must approve (the cashier does not hold the approval station)."""
	value = setting("unread_sale_needs_manager")
	return bool((value is None or cint(value)) and not has_station(APPROVER_STATION, user))


def approval_mode(user=None):
	"""self: the cashier approves; pin / password / pin_or_password: someone else does (RFID Settings)."""
	if not needs_manager(user):
		return "self"
	return {"Approver login and password": "password", "PIN or approver login": "pin_or_password"}.get(
		setting("unread_approval_mode") or "", "pin")


def check_approver(manager=None, password=None):
	"""The user who approves an unread-tag sale: the cashier when allowed, else an approver who signs in here."""
	mode = approval_mode()
	if mode == "self":
		return frappe.session.user
	if mode == "pin":
		frappe.throw(_("Ask an approver for a PIN."), title=_("Approval"))
	if not manager or not password:
		frappe.throw(_("An approver has to approve this sale. Enter the approver's login and password."),
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
	if not has_station(APPROVER_STATION, user) or user == frappe.session.user:
		frappe.throw(_("{0} cannot approve this. Ask an approver (RFID Approver).").format(user), title=_("Manager approval"))
	return user


def outstanding(item_code, warehouse):
	"""Tags of the item in the warehouse that still stand for pieces sold unread."""
	rows = frappe.get_all("RFID Unread Sale", {"item_code": item_code, "warehouse": warehouse, "status": "Open"},
		["qty", "settled_qty"])
	return sum(max(cint(r.qty) - cint(r.settled_qty), 0) for r in rows)


def open_sales(item_code, warehouse):
	"""Open records, oldest first, with how many tags each still waits for."""
	rows = frappe.get_all("RFID Unread Sale", {"item_code": item_code, "warehouse": warehouse, "status": "Open"},
		["name", "qty", "settled_qty", "reference_doctype", "reference_name", "reason", "sold_by", "sold_on",
			"approved_by"], order_by="creation asc")
	for r in rows:
		r.waiting = max(cint(r.qty) - cint(r.settled_qty), 0)
	return [r for r in rows if r.waiting]


def ghost_story(item_code, warehouse):
	"""Open unread sales of the item, in words for the people who tag or count it."""
	rows = open_sales(item_code, warehouse)
	for r in rows:
		r.sold_by_name = r.sold_by and frappe.utils.get_fullname(r.sold_by)
		r.text = _("{0} piece(s) on {1}, sold by {2} on {3} ({4}, {5})").format(
			r.waiting, r.reference_name, r.sold_by_name or r.sold_by or "-",
			frappe.utils.format_datetime(r.sold_on, "dd-MM-yyyy HH:mm") if r.sold_on else "-", r.reason, r.name)
	return rows


def note_ghost_tagging(item_code, warehouse, count, ref=None):
	"""Tags went on pieces while some tags of the item still stand for pieces sold unread: tags now
	outnumber stock until a stock count of the item finds which tags left with the customers."""
	rows = open_sales(item_code, warehouse)
	if not rows or count < 1:
		return
	user = frappe.utils.get_fullname()
	for r in rows:
		frappe.get_doc("RFID Unread Sale", r.name).add_comment("Comment", _(
			"{0} new tag(s) put on {1} in {2} by {3}{4} while this sale's tag is still counted. Tags of {1} now "
			"outnumber its stock; count {1} to settle.").format(count, item_code, warehouse, user,
			_(" ({0})").format(ref) if ref else ""))
	rfid.log_exception("Untagged Stock", _(
		"{0} tag(s) put on {1} in {2} by {3} although {4} tag(s) of it still stand for pieces sold without a tag "
		"read ({5}). Count {1} to settle.").format(count, item_code, warehouse, user, sum(r.waiting for r in rows),
		", ".join(r.name for r in rows)), severity="Medium")


def record_no_tag(doc, item_code, warehouse, qty):
	"""Pieces sold by hand out of the untagged stock: nothing is left behind, but who sold what stays on record."""
	urs = frappe.get_doc({
		"doctype": "RFID Unread Sale",
		"status": NO_TAG_STATUS,
		"item_code": item_code,
		"warehouse": warehouse,
		"qty": qty,
		"reason": NO_TAG,
		"reference_doctype": doc.doctype,
		"reference_name": doc.name,
		"pos_profile": doc.get("pos_profile"),
		"basket": doc.get("custom_rfid_basket"),
		"sold_by": frappe.session.user,
		"sold_on": now_datetime(),
	})
	urs.insert(ignore_permissions=True)
	return urs


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
		"status": ["in", ("Open", "Settled", NO_TAG_STATUS)]}, pluck="name"):
		urs = frappe.get_doc("RFID Unread Sale", name)
		urs.status = "Cancelled"
		urs.add_comment("Comment", _("{0} cancelled.").format(doc.name))
		urs.save(ignore_permissions=True)
