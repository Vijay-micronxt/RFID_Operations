import frappe

from rfid_retail.settings import DEFAULT_STATION_ROLES


def execute():
	"""New Unread Sale Approval station: give it the default role on sites whose station table is already set up."""
	from rfid_retail.setup.install import make_roles

	make_roles()
	settings = frappe.get_single("RFID Settings")
	if not settings.station_roles or any(r.station == "Unread Sale Approval" for r in settings.station_roles):
		return
	for role in DEFAULT_STATION_ROLES["Unread Sale Approval"]:
		settings.append("station_roles", {"station": "Unread Sale Approval", "role": role})
	settings.flags.ignore_permissions = True
	settings.flags.ignore_mandatory = True
	settings.save()
