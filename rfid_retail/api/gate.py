"""Exit gate and device health API."""

import frappe
from frappe import _
from frappe.utils import add_to_date, cint, get_datetime, now_datetime

from rfid_retail import rfid
from rfid_retail.settings import get as setting, is_enabled, require_station
from rfid_retail.api.pos import _load

def _touch(device, status="ok"):
	if device and frappe.db.exists("RFID Device", device):
		frappe.db.set_value("RFID Device", device, {"last_seen": now_datetime(), "last_status": status,
			"health": "Online"}, update_modified=False)


@frappe.whitelist(methods=["POST"])
def gate_read(codes, device=None):
	"""Evaluate reads at the exit against each tag's CURRENT assignment.

	Returns alarm=True when an unpaid garment is leaving. The portal is an aid to
	store security, so every alarm is also recorded for staff follow-up.
	"""
	require_station("Exit Gate")
	_touch(device)
	events, alarm = [], False
	for code in {rfid.normalize(c) for c in _load(codes)}:
		res = rfid.resolve_code(code)
		if res.kind != "tag":
			# Unknown or free tags (e.g. loose tag storage near the gate) are logged, not alarmed.
			events.append({"epc": code, "alarm": False, "message": _("Unmapped tag")})
			continue
		sn = rfid.serial_info(res.serial_no)
		if res.tag_status == rfid.TAG_SOLD and sn.custom_rfid_status == rfid.SOLD:
			rfid.log_exception("Unconfirmed Detach",
				_("Paid garment {0} left with tag {1} still attached ({2}).").format(
					res.component or res.serial_no, code, sn.custom_sales_invoice),
				severity="Low", epc=code, serial_no=res.serial_no, component=res.component, device=device)
			events.append({"epc": code, "alarm": False, "message": _("Paid ({0}) - tag not detached").format(
				sn.custom_sales_invoice)})
			continue
		alarm = True
		rfid.log_exception("Exit Alert",
			_("UNPAID {0} ({1}) read at exit. Status: {2}.").format(
				res.component or res.serial_no, sn.item_name, sn.custom_rfid_status),
			severity="Critical", epc=code, serial_no=res.serial_no, component=res.component, device=device)
		events.append({"epc": code, "alarm": True, "serial_no": res.serial_no, "item_name": sn.item_name,
			"message": _("UNPAID: {0}").format(res.component or res.serial_no)})
	return {"alarm": alarm, "events": events}


@frappe.whitelist(methods=["POST"])
def heartbeat(device, status="ok"):
	"""Reader bridge calls this every minute or so."""
	require_station("Device API")
	if not frappe.db.exists("RFID Device", device):
		frappe.throw(_("Unknown device {0}").format(device))
	_touch(device, status)
	return "ok"


def mark_stale_devices():
	"""Scheduler: a device that stopped reporting shows as Offline and raises an exception."""
	if not is_enabled():
		return
	default_minutes = cint(setting("device_offline_minutes")) or 15
	for d in frappe.get_all("RFID Device", {"is_active": 1},
		["name", "device_type", "last_seen", "stale_after_minutes", "health"]):
		limit = add_to_date(now_datetime(), minutes=-(cint(d.stale_after_minutes) or default_minutes))
		stale = not d.last_seen or get_datetime(d.last_seen) < limit
		if stale and d.health != "Offline":
			frappe.db.set_value("RFID Device", d.name, "health", "Offline", update_modified=False)
			rfid.log_exception("Device Offline",
				_("{0} ({1}) has not reported since {2}.").format(d.name, d.device_type, d.last_seen or _("never")),
				severity="Critical" if d.device_type == "Exit Gate" else "High", device=d.name)
