import frappe
from frappe.utils import cint

# Default 1, but add_unread_approval_station saved RFID Settings in the same migrate that added them,
# and a save stores a Check field with no value as 0.
FIELDS = ("allow_unread_tag_sale", "unread_sale_needs_manager")


def execute():
	"""Put the unread-sale switches back on, unless a user has switched them off since."""
	for field in FIELDS:
		value = frappe.db.get_single_value("RFID Settings", field)
		if value is None or cint(value) or _changed_by_user(field):
			continue
		frappe.db.set_single_value("RFID Settings", field, 1)
	frappe.clear_document_cache("RFID Settings", "RFID Settings")


def _changed_by_user(field):
	versions = frappe.get_all("Version", {"ref_doctype": "RFID Settings", "data": ["like", f'%"{field}"%']},
		["owner", "data"])
	for v in versions:
		if v.owner == "Administrator":
			continue
		if any(row[0] == field for row in (frappe.parse_json(v.data).get("changed") or [])):
			return True
	return False
