"""A physical count sent from the handheld app.

`push` turns what the phone counted into a Physical Audit in Draft. It changes no stock and no tag:
the record only shows what the phone saw, for a manager to look at.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt, get_datetime

from rfid_retail import rfid
from rfid_retail.api.pos import _load
from rfid_retail.settings import require_station

MAX_ITEMS = 20000
MAX_TAGS = 100000
RESULTS = ("Matched", "Missing", "Unknown")


@frappe.whitelist(methods=["POST"])
def push(client_id=None, warehouse=None, downloaded_at=None, items=None, tags=None, device_info=None):
	"""Creates a Physical Audit (Draft) and returns `{name, duplicate}`.

	client_id: an ID the app makes up for this push. Sending the same one again (a retry after a lost
	reply) returns the record it already made instead of a second one; a new push has a new ID.
	items: [{item_code, system_qty, scanned_qty, erp_stock_qty}]
	tags:  [{epc, item_code?, result}] with result Matched / Missing / Unknown
	"""
	require_station("Stock Audit")

	client_id = (client_id or "").strip()
	if not client_id or len(client_id) > 64:
		frappe.throw(_("The push needs an ID (up to 64 characters)."))
	existing = frappe.db.get_value("Physical Audit", {"client_id": client_id}, ["name", "operator"], as_dict=True)
	if existing:
		if existing.operator != frappe.session.user:
			frappe.throw(_("This push ID is already used."))
		return {"name": existing.name, "duplicate": True}

	if not warehouse or not frappe.db.exists("Warehouse", warehouse):
		frappe.throw(_("Warehouse {0} not found.").format(warehouse or ""), frappe.DoesNotExistError)

	item_rows = _item_rows(_load(items))
	tag_rows = _tag_rows(_load(tags))
	if not item_rows and not tag_rows:
		frappe.throw(_("There is nothing to send: no items and no tags."))

	doc = frappe.get_doc({
		"doctype": "Physical Audit",
		"warehouse": warehouse,
		"client_id": client_id,
		"downloaded_at": _when(downloaded_at),
		"device_info": (device_info or "")[:140] or None,
		"items": item_rows,
		"tags": tag_rows,
	})
	doc.insert()  # as the caller, so the doctype's own permissions apply as well as the station rule
	return {"name": doc.name, "duplicate": False}


def _when(value):
	if not value:
		return None
	try:
		return get_datetime(value)
	except Exception:
		return None


def _item_rows(rows):
	if len(rows) > MAX_ITEMS:
		frappe.throw(_("Too many items in one push (limit {0}).").format(MAX_ITEMS))
	out = []
	for r in rows:
		code = (r.get("item_code") or "").strip()
		if not code:
			frappe.throw(_("An item row has no item code."))
		out.append({
			"item_code": code,
			"system_qty": cint(r.get("system_qty")),
			"scanned_qty": cint(r.get("scanned_qty")),
			"erp_stock_qty": flt(r.get("erp_stock_qty")),
		})
	return out


def _tag_rows(rows):
	if len(rows) > MAX_TAGS:
		frappe.throw(_("Too many tags in one push (limit {0}).").format(MAX_TAGS))
	out = []
	for r in rows:
		epc = rfid.normalize(r.get("epc"))
		result = r.get("result")
		if not epc:
			continue
		if result not in RESULTS:
			frappe.throw(_("Tag {0}: result must be one of {1}.").format(epc, ", ".join(RESULTS)))
		out.append({"epc": epc, "item_code": (r.get("item_code") or "").strip() or None, "result": result})
	return out
