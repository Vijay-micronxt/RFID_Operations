"""Read-only data for a handheld that counts on its own.

`audit_snapshot` gives the phone what the warehouse is expected to hold (the same identities a
count freezes at its start), so the phone can compare its reads locally. It creates no session
and writes nothing.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt, now_datetime

from rfid_retail import rfid
from rfid_retail.api.audit import IN_STORE
from rfid_retail.settings import require_station

MAX_PAGE = 5000


@frappe.whitelist(methods=["GET"])
def audit_warehouses():
	"""Warehouses a handheld can count, for its warehouse dropdown: `[{name, tags}]`.

	Only real stock locations (not group folders, not disabled). `tags` is how many tags stand for stock
	still in the warehouse, so the app can offer the busiest one first.
	"""
	require_station("Stock Audit")
	tags = dict(frappe.get_all("RFID Tag",
		filters={"status": ["in", rfid.IN_STOCK_TAG], "warehouse": ["is", "set"]},
		fields=["warehouse", "count(name) as n"], group_by="warehouse", as_list=True))
	names = frappe.get_all("Warehouse", filters={"is_group": 0, "disabled": 0}, pluck="name", order_by="name asc")
	return [{"name": n, "tags": cint(tags.get(n))} for n in names]


@frappe.whitelist(methods=["GET"])
def audit_snapshot(warehouse, start=0, page_length=2000):
	"""What `warehouse` is expected to hold, for an offline compare on the handheld.

	tags:  one row per expected tag, `{epc, item_code, serial_no, component}`, paged by start / page_length.
	items: every item with expected tags, `{item_code, item_name, uom, tag_qty, stock_qty}`.
	       tag_qty counts units (a set is one unit however many pieces it has); stock_qty is
	       ERPNext's own quantity for the item in this warehouse.
	"""
	require_station("Stock Audit")
	if not frappe.db.exists("Warehouse", warehouse):
		frappe.throw(_("Warehouse {0} not found.").format(warehouse), frappe.DoesNotExistError)
	start = max(cint(start), 0)
	page_length = min(max(cint(page_length), 1), MAX_PAGE)

	tags, units = _expected(warehouse)
	items = _items(warehouse, units)
	return {
		"warehouse": warehouse,
		"generated_at": now_datetime(),
		"total_tags": len(tags),
		"start": start,
		"page_length": page_length,
		"tags": tags[start : start + page_length],
		"items": items,
	}


def _expected(warehouse):
	"""Expected tags and, per item, the units they stand for. Same rules as RFID Audit Session.begin."""
	tags, units = [], {}
	serials = frappe.get_all("Serial No",
		filters={"warehouse": warehouse, "status": "Active", "custom_rfid_status": ["in", IN_STORE]},
		fields=["name", "item_code", "custom_is_garment_set"], order_by="name asc")
	for sn in serials:
		if not rfid.is_rfid_item(sn.item_code):
			continue
		found = 0
		if cint(sn.custom_is_garment_set):
			for c in rfid.get_components(sn.name):
				if c.rfid_tag:
					tags.append({"epc": c.rfid_tag, "item_code": sn.item_code, "serial_no": sn.name, "component": c.name})
					found += 1
		else:
			assignment = rfid.active_assignment_for(serial_no=sn.name)
			tag = assignment and frappe.db.get_value("RFID Tag Assignment", assignment, "tag")
			if tag:
				tags.append({"epc": tag, "item_code": sn.item_code, "serial_no": sn.name, "component": None})
				found += 1
		# A piece whose tag was lost has none to scan, so it is not expected (else it is counted missing every time).
		if found:
			units[sn.item_code] = units.get(sn.item_code, 0) + 1

	for t in frappe.get_all("RFID Tag",
		filters={"warehouse": warehouse, "status": ["in", rfid.IN_STOCK_TAG], "serial_no": ("is", "not set")},
		fields=["name", "item_code"], order_by="name asc"):
		tags.append({"epc": t.name, "item_code": t.item_code, "serial_no": None, "component": None})
		units[t.item_code] = units.get(t.item_code, 0) + 1
	return tags, units


def _items(warehouse, units):
	codes = list(units)
	if not codes:
		return []
	info = {i.name: i for i in frappe.get_all("Item", {"name": ["in", codes]}, ["name", "item_name", "stock_uom"])}
	stock = dict(frappe.get_all("Bin", {"warehouse": warehouse, "item_code": ["in", codes]},
		["item_code", "actual_qty"], as_list=True))
	return sorted(
		({"item_code": c, "item_name": info[c].item_name if c in info else c,
			"uom": info[c].stock_uom if c in info else None,
			"tag_qty": units[c], "stock_qty": flt(stock.get(c))} for c in codes),
		key=lambda i: (i["item_name"] or "").lower())
