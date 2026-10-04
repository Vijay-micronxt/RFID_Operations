"""Back-office mapping station (desktop reader) and detacher API."""

import frappe
from frappe import _
from frappe.utils import cint

from rfid_retail import rfid
from rfid_retail.settings import require_station
from rfid_retail.api.pos import _load

@frappe.whitelist()
def pending_units(item_code=None, warehouse=None, limit=100):
	"""Units that still need tags: new inward stock and verified returns."""
	require_station("Tag Mapping")
	filters = {"custom_rfid_status": ["in", [rfid.PENDING, rfid.RETURNED]]}
	if item_code:
		filters["item_code"] = item_code
	if warehouse:
		filters["warehouse"] = warehouse
	rows = frappe.get_all("Serial No", filters=filters,
		fields=["name", "item_code", "item_name", "warehouse", "custom_rfid_status", "custom_is_garment_set"],
		order_by="creation asc", limit=cint(limit))
	# Units that carried tags before (sold and returned) need re-tagging, not first tagging.
	tagged_before = set(frappe.get_all("RFID Tag Assignment",
		{"serial_no": ["in", [r.name for r in rows] or [""]]}, pluck="serial_no", distinct=True))
	for r in rows:
		r.retag = r.name in tagged_before
	return rows


@frappe.whitelist()
def unit_detail(serial_no):
	require_station("Tag Mapping", "Tag Repair")
	return _unit_detail(serial_no)


def _unit_detail(serial_no):
	serial_no = rfid.normalize(serial_no)
	res = rfid.resolve_code(serial_no)
	if res.serial_no:
		serial_no = res.serial_no
	sn = rfid.serial_info(serial_no)
	if not sn:
		frappe.throw(_("{0} is not a serial number, piece label or mapped tag.").format(serial_no))
	rfid.ensure_serial_setup(serial_no)
	sn = rfid.serial_info(serial_no)
	out = dict(sn)
	out["is_rfid_item"] = rfid.is_rfid_item(sn.item_code)
	if cint(sn.custom_is_garment_set):
		out["components"] = rfid.get_components(serial_no)
	else:
		asg = rfid.active_assignment_for(serial_no=serial_no)
		out["tag"] = asg and frappe.db.get_value("RFID Tag Assignment", asg, "tag")
	out["history"] = frappe.get_all("RFID Tag Assignment", {"serial_no": serial_no},
		["name", "tag", "role", "status", "assigned_on", "closed_on", "close_reason", "sales_invoice"],
		order_by="assigned_on desc")
	return out


@frappe.whitelist(methods=["POST"])
def map_tag(epc, serial_no, component=None, device=None):
	"""Bind one tag, read at the desktop reader, to a unit or to one piece of a set."""
	require_station("Tag Mapping")
	rfid.assign_tag(epc, rfid.normalize(serial_no), rfid.normalize(component) or None, device=device)
	return _unit_detail(serial_no)


@frappe.whitelist(methods=["POST"])
def replace_tag(old_epc, new_epc, reason="Tag Damaged", device=None):
	"""Damaged/unreadable tag: retire the old tag and give the same identity a new one."""
	require_station("Tag Repair")
	old = frappe.get_doc("RFID Tag", rfid.normalize(old_epc))
	if not old.serial_no:
		frappe.throw(_("Tag {0} is not mapped.").format(old.name))
	serial_no, component, prior = old.serial_no, old.component, old.status
	rfid.close_assignment(old.name, "Tag Replaced", new_tag_status=rfid.TAG_DAMAGED, remarks=reason)
	if component:
		frappe.db.set_value("Garment Component", component, "status", "Pending Tag")
	rfid.assign_tag(new_epc, serial_no, component, device=device, remarks=_("Replaces {0}").format(old.name))
	if prior == rfid.TAG_SOLD:
		frappe.db.set_value("RFID Tag", rfid.normalize(new_epc), "status", rfid.TAG_SOLD)
	rfid.log_exception("Tag Replaced", _("{0} replaced by {1} on {2}: {3}").format(
		old.name, rfid.normalize(new_epc), component or serial_no, reason), severity="Low",
		epc=old.name, serial_no=serial_no, component=component, device=device)
	return _unit_detail(serial_no)


@frappe.whitelist(methods=["POST"])
def detach(codes, device=None):
	"""Detacher station: release tags of paid garments back to the reusable pool.

	Only tags whose sale is complete can be released. Anything else is refused and logged.
	"""
	require_station("Detach")
	out = []
	for code in _load(codes):
		code = rfid.normalize(code)
		tag = frappe.db.get_value("RFID Tag", code, ["status", "serial_no", "component"], as_dict=True)
		if not tag:
			out.append({"epc": code, "ok": False, "message": _("Unknown tag")})
			continue
		if tag.status == rfid.TAG_AVAILABLE:
			out.append({"epc": code, "ok": True, "message": _("Already free")})
			continue
		if tag.status != rfid.TAG_SOLD:
			rfid.log_exception("Unconfirmed Detach",
				_("Detach attempted on {0} ({1}) which is {2}, not sold.").format(code, tag.serial_no, tag.status),
				severity="High", epc=code, serial_no=tag.serial_no, component=tag.component, device=device)
			out.append({"epc": code, "ok": False, "message": _("Not sold - do not detach ({0})").format(tag.status)})
			continue
		rfid.close_assignment(code, "Detached After Sale")
		out.append({"epc": code, "ok": True, "message": _("Released from {0}").format(tag.component or tag.serial_no)})
	return out


@frappe.whitelist(methods=["POST"])
def release_return(serial_no):
	"""After a return is physically checked: keep pieces that still carry tags, others go back to mapping."""
	require_station("Tag Repair")
	sn = rfid.serial_info(serial_no)
	if sn.custom_rfid_status != rfid.RETURNED:
		frappe.throw(_("{0} is not a held return.").format(serial_no))
	for asg in frappe.get_all("RFID Tag Assignment", {"serial_no": serial_no, "status": "Active"}, ["tag", "component"]):
		frappe.db.set_value("RFID Tag", asg.tag, "status", rfid.TAG_ASSIGNED)
		if asg.component:
			frappe.db.set_value("Garment Component", asg.component, {"status": "Tagged", "sales_invoice": None})
	for comp in rfid.get_components(serial_no):
		if not comp.rfid_tag:
			frappe.db.set_value("Garment Component", comp.name, {"status": "Pending Tag", "sales_invoice": None})
	frappe.db.set_value("Serial No", serial_no, {"custom_rfid_status": rfid.PENDING, "custom_sales_invoice": None},
		update_modified=False)
	rfid.refresh_serial_readiness(serial_no)
	return _unit_detail(serial_no)


@frappe.whitelist(methods=["POST"])
def set_alteration(component, out=1, custodian=None, expected_return=None):
	"""Piece sent to / back from alteration. A set with a piece away cannot be billed."""
	require_station("Tag Mapping")
	comp = frappe.get_doc("Garment Component", component)
	if cint(out):
		comp.update({"status": "Out for Alteration", "custodian": custodian,
			"outward_on": frappe.utils.now_datetime(), "expected_return": expected_return})
		frappe.db.set_value("Serial No", comp.serial_no, "custom_rfid_status", rfid.HOLD, update_modified=False)
	else:
		comp.update({"status": "Tagged" if comp.rfid_tag else "Pending Tag", "custodian": None,
			"outward_on": None, "expected_return": None})
		frappe.db.set_value("Serial No", comp.serial_no, "custom_rfid_status", rfid.PENDING, update_modified=False)
	comp.save(ignore_permissions=True)
	if not cint(out):
		rfid.refresh_serial_readiness(comp.serial_no)
	return _unit_detail(comp.serial_no)


@frappe.whitelist()
def identify(codes):
	"""What is this tag / label? Used by the handheld search and the verification step."""
	require_station("Tag Mapping", "Detach", "Billing", "Stock Audit")
	out = []
	for code in _load(codes):
		res = rfid.resolve_code(code)
		if res.serial_no:
			res.update(rfid.serial_info(res.serial_no))
		out.append(res)
	return out
