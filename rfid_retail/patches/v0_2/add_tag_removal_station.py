import frappe

from rfid_retail.settings import DEFAULT_STATION_ROLES


def execute():
	"""New Tag Removal station: give it the default roles on sites whose station table is already set up."""
	from rfid_retail.setup.install import add_station_roles

	stations = frappe.get_all("RFID Station Role", {"parent": "RFID Settings", "parentfield": "station_roles"},
		pluck="station")
	if not stations or "Tag Removal" in stations:
		return
	add_station_roles([("Tag Removal", role) for role in DEFAULT_STATION_ROLES["Tag Removal"]
		if frappe.db.exists("Role", role)])
