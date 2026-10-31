import frappe

from rfid_retail.settings import DEFAULT_STATION_ROLES


def execute():
	"""New Unread Sale Approval station: give it the default role on sites whose station table is already set up."""
	from rfid_retail.setup.install import add_station_roles, make_roles

	make_roles()
	stations = frappe.get_all("RFID Station Role", {"parent": "RFID Settings", "parentfield": "station_roles"},
		pluck="station")
	if not stations or "Unread Sale Approval" in stations:
		return
	add_station_roles([("Unread Sale Approval", role) for role in DEFAULT_STATION_ROLES["Unread Sale Approval"]])
