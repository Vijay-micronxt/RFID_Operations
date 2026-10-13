"""RFID tags are read on Purchase Receipt / Stock Entry only, not on Purchase Invoice."""

import frappe


def execute():
	for fieldname in ("custom_rfid_tags", "custom_rfid_tag_count"):
		name = frappe.db.get_value("Custom Field", {"dt": "Purchase Invoice Item", "fieldname": fieldname})
		if name:
			frappe.delete_doc("Custom Field", name, ignore_permissions=True)
	# Tags held by Purchase Invoice drafts would stay reserved forever.
	from rfid_retail import rfid

	for tag in frappe.get_all("RFID Tag", {"status": "Reserved", "reserved_doctype": "Purchase Invoice"}, pluck="name"):
		rfid.release_reservation(tag, "Tags are read on the Purchase Receipt now")
