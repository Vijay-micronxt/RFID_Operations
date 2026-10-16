import frappe

from rfid_retail.settings import DEFAULT_STATION_ROLES


def execute():
	"""New Tag Removal station: give it the default roles on sites whose station table is already set up."""
	settings = frappe.get_single("RFID Settings")
	if not settings.station_roles or any(r.station == "Tag Removal" for r in settings.station_roles):
		return
	for role in DEFAULT_STATION_ROLES["Tag Removal"]:
		if frappe.db.exists("Role", role):
			settings.append("station_roles", {"station": "Tag Removal", "role": role})
	settings.flags.ignore_permissions = True
	settings.flags.ignore_mandatory = True
	settings.save()
