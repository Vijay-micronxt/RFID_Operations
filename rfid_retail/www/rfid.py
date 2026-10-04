import frappe
from frappe import _

from rfid_retail.settings import allowed_stations, is_enabled

no_cache = 1

# Page tab -> the stations that unlock it
TABS = {
	"counter": ("Billing",),
	"mapping": ("Tag Mapping", "Tag Repair"),
	"detach": ("Detach",),
	"gate": ("Exit Gate",),
	"audit": ("Stock Audit", "Audit Review"),
}


def get_context(context):
	if frappe.session.user == "Guest":
		frappe.local.flags.redirect_location = "/login?redirect-to=/rfid"
		raise frappe.Redirect
	context.enabled = is_enabled()
	stations = set(allowed_stations()) if context.enabled else set()
	context.tabs = [t for t, need in TABS.items() if stations & set(need)]
	if context.enabled and not context.tabs:
		frappe.throw(_("No RFID station is assigned to your roles. Ask your manager (RFID Settings)."),
			frappe.PermissionError)
	context.stations = sorted(stations)
	context.csrf_token = frappe.sessions.get_csrf_token()
	context.user_fullname = frappe.utils.get_fullname(frappe.session.user)
	context.company = frappe.defaults.get_user_default("Company") or frappe.db.get_single_value(
		"Global Defaults", "default_company") or ""
	# Billing runs in POS Next's RFID counter mode when that app is present.
	context.pos_url = "/pos" if "pos_next" in frappe.get_installed_apps() else None
	if context.pos_url is None and "counter" in context.tabs:
		context.tabs.remove("counter")
	context.devices = frappe.get_all("RFID Device", {"is_active": 1}, ["name", "device_type", "pos_profile", "warehouse"])
	# Warehouses holding RFID stock first (most units on top), so the count defaults to the real floor.
	rfid_units = dict(frappe.db.sql("""select warehouse, count(*) from `tabSerial No`
		where status='Active' and ifnull(custom_rfid_status, '') != '' group by warehouse"""))
	context.warehouses = sorted(
		frappe.get_all("Warehouse", {"is_group": 0, "company": context.company}, pluck="name"),
		key=lambda w: (-rfid_units.get(w, 0), w))
	return context
