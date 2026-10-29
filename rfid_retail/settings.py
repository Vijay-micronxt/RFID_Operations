"""RFID Settings access: master switch, sale rules and station -> role mapping.

Every hook, scheduler job and API goes through here, so a site with RFID
switched off behaves exactly as if the app were not installed.
"""

import frappe
from frappe import _
from frappe.utils import cint

# Used when a site has not configured a station yet (fresh install).
DEFAULT_STATION_ROLES = {
	"Billing": ("RFID Cashier", "RFID Store Manager"),
	"Tag Mapping": ("RFID Stock Operator", "RFID Store Manager"),
	"Tag Repair": ("RFID Store Manager",),
	"Tag Removal": ("RFID Store Manager",),
	"Detach": ("RFID Cashier", "RFID Stock Operator", "RFID Store Manager"),
	"Exit Gate": ("RFID Device", "RFID Store Manager"),
	"Device API": ("RFID Device", "RFID Store Manager"),
	"Stock Audit": ("RFID Stock Operator", "RFID Store Manager"),
	"Audit Review": ("RFID Owner", "RFID Store Manager"),
	"Manager Override": ("RFID Store Manager",),
	# approves selling a piece whose tag will not read (gets the PIN by WhatsApp / notification)
	"Unread Sale Approval": ("RFID Approver",),
}
STATIONS = tuple(DEFAULT_STATION_ROLES)


def get_settings():
	return frappe.get_cached_doc("RFID Settings")


def is_enabled():
	return cint(get_settings().enabled)


def get(fieldname):
	return get_settings().get(fieldname)


def hand_pick_check():
	"""Hand-picked RFID items checked against untagged stock (on unless switched off)."""
	value = get("check_hand_picked_items")
	return is_enabled() and (value is None or cint(value))


def station_roles(station):
	rows = [r.role for r in get_settings().station_roles if r.station == station]
	return set(rows) | {"System Manager"}


def has_station(station, user=None):
	user = user or frappe.session.user
	if user == "Administrator":
		return True
	return bool(station_roles(station) & set(frappe.get_roles(user)))


def allowed_stations(user=None):
	return [s for s in STATIONS if has_station(s, user)]


def require_enabled():
	if not is_enabled():
		frappe.throw(_("RFID is switched off on this site (RFID Settings)."), title=_("RFID disabled"))


def can_remove_unsold_tags(user=None):
	"""Taking a tag off unsold stock: switched on in RFID Settings and the user holds the Tag Removal station."""
	return is_enabled() and cint(get("allow_unsold_tag_removal")) and has_station("Tag Removal", user)


def require_station(*stations):
	"""API guard: RFID on, and the user holds a role mapped to at least one of the stations."""
	require_enabled()
	if not any(has_station(s) for s in stations):
		frappe.throw(_("You are not allowed to use the {0} station.").format(" / ".join(stations)),
			frappe.PermissionError)
