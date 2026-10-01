"""Inward documents: every new RFID-tracked unit starts as Pending Mapping."""

import frappe

from rfid_retail import rfid
from rfid_retail.settings import is_enabled, require_station
from rfid_retail.overrides.sales_invoice import get_row_serials


def on_submit(doc, method=None):
	if not is_enabled():
		return
	for row in doc.get("items") or []:
		if not rfid.is_rfid_item(row.item_code):
			continue
		if doc.doctype == "Stock Entry" and not row.get("t_warehouse"):
			continue
		for serial_no in get_row_serials(row):
			rfid.ensure_serial_setup(serial_no)


@frappe.whitelist()
def setup_existing_serials(item_code=None):
	"""Backfill for serials created before the item was flagged RFID tracked."""
	require_station("Tag Repair")
	filters = {"status": "Active"}
	if item_code:
		filters["item_code"] = item_code
	count = 0
	for serial_no in frappe.get_all("Serial No", filters=filters, pluck="name"):
		item = frappe.db.get_value("Serial No", serial_no, "item_code")
		if rfid.is_rfid_item(item):
			rfid.ensure_serial_setup(serial_no)
			count += 1
	return count
