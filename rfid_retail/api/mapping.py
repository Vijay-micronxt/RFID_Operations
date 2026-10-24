"""Back-office tagging station, tag repair, tag history and detacher API.

Bulk tagging: the operator picks one item (identical units: same style, colour,
size), puts all its tags on the station reader at once and saves. Which tag ends
up on which piece does not matter because the units are interchangeable. Items
that differ are tagged one item at a time; sets are still tagged piece by piece.
"""

import frappe
from frappe import _
from frappe.utils import cint

from rfid_retail import rfid
from rfid_retail.settings import can_remove_unsold_tags, get as setting, require_station
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
	"""Damaged/unreadable tag: retire the old tag and give the same unit a new one."""
	require_station("Tag Repair")
	old = frappe.get_doc("RFID Tag", rfid.normalize(old_epc))
	if not old.current_assignment:
		frappe.throw(_("Tag {0} is not on any garment.").format(old.name))
	serial_no, component, prior = old.serial_no, old.component, old.status
	item_code, warehouse = old.item_code, old.warehouse
	# the new tag belongs to the same inward row as the old one
	source = frappe.db.get_value("RFID Tag Assignment", old.current_assignment,
		["source_doctype", "source_name", "source_row"])
	rfid.close_assignment(old.name, "Tag Replaced", new_tag_status=rfid.TAG_DAMAGED, remarks=reason,
		action="Replaced")
	if component:
		frappe.db.set_value("Garment Component", component, "status", "Pending Tag")
	new = rfid.normalize(new_epc)
	asg = rfid.assign_tag(new, serial_no, component, device=device, item_code=item_code, warehouse=warehouse,
		remarks=_("Replaces {0}").format(old.name), source=source if source and source[0] else None)
	rfid.add_to_source_row(asg, new, _("RFID tag {0} replaced by {1}.").format(old.name, new))
	if prior == rfid.TAG_SOLD:
		frappe.db.set_value("RFID Tag", new, "status", rfid.TAG_SOLD)
	rfid.log_exception("Tag Replaced", _("{0} replaced by {1} on {2}: {3}").format(
		old.name, new, component or serial_no or item_code, reason), severity="Low",
		epc=old.name, serial_no=serial_no, component=component, device=device)
	return _unit_detail(serial_no) if serial_no else tag_story(new)


@frappe.whitelist(methods=["POST"])
def detach(codes, device=None):
	"""Detacher station: release tags of paid garments back to the reusable pool.

	Only tags whose sale is complete are released here. A tag still on unsold stock
	is refused and logged, unless RFID Settings allows removing tags from unsold
	items and the user holds the Tag Removal station: then they choose what to do
	(remove_tags).
	"""
	require_station("Detach")
	can_remove = can_remove_unsold_tags()
	out = []
	for code in _load(codes):
		code = rfid.normalize(code)
		tag = frappe.db.get_value("RFID Tag", code, ["status", "serial_no", "component", "item_code"], as_dict=True)
		if not tag:
			out.append({"epc": code, "ok": False, "message": _("Unknown tag")})
			continue
		if tag.status == rfid.TAG_AVAILABLE:
			out.append({"epc": code, "ok": True, "message": _("Already free")})
			continue
		if tag.status == rfid.TAG_ASSIGNED and can_remove:
			out.append({"epc": code, "ok": False, "unsold": True, "item_code": tag.item_code,
				"item_name": frappe.get_cached_value("Item", tag.item_code, "item_name") if tag.item_code else None,
				"serial_no": tag.serial_no, "component": tag.component,
				"message": _("Not sold - choose what to do")})
			continue
		if tag.status != rfid.TAG_SOLD:
			msg = _("Detach attempted on {0} ({1}) which is {2}, not sold.").format(
				code, tag.serial_no or tag.item_code, tag.status)
			rfid.log_exception("Unconfirmed Detach", msg, severity="High", epc=code, serial_no=tag.serial_no,
				component=tag.component, device=device)
			rfid.log_event(code, "Rejected", reason=msg, device=device)
			hint = _(" Ask a user with Tag Removal access.") if tag.status == rfid.TAG_ASSIGNED and cint(
				setting("allow_unsold_tag_removal")) else ""
			out.append({"epc": code, "ok": False, "message": _("Not sold - do not detach ({0}).").format(tag.status) + hint})
			continue
		rfid.close_assignment(code, "Detached After Sale", action="Detached")
		out.append({"epc": code, "ok": True, "message": _("Released from {0}").format(
			tag.component or tag.serial_no or tag.item_code)})
	return out


REMOVE_ACTIONS = ("Remove", rfid.TAG_DAMAGED, rfid.TAG_LOST)


@frappe.whitelist(methods=["POST"])
def remove_tags(codes, action="Remove", reason=None, device=None):
	"""Tags on unsold stock, many at once: Remove (free the tag for another item), Damaged or Lost.

	The reason is optional and shared by the whole batch. One bad tag does not stop the rest.
	"""
	require_station("Detach", "Tag Repair")
	if not can_remove_unsold_tags():
		frappe.throw(_("Removing tags from unsold items is switched off or not allowed for you (RFID Settings)."),
			frappe.PermissionError)
	if action not in REMOVE_ACTIONS:
		frappe.throw(_("Unknown action {0}").format(action))
	reason = (reason or "").strip()
	out = []
	for code in _load(codes):
		code = rfid.normalize(code)
		frappe.db.savepoint("rfid_remove_tag")
		try:
			if action == "Remove":
				rfid.remove_unsold_tag(code, reason, device=device)
				message = _("Removed - free to use again")
			else:
				rfid.set_tag_condition(code, action, reason or _("Marked at the Remove tags screen"), device=device)
				rfid.log_exception("Tag Lost" if action == rfid.TAG_LOST else "Tag Replaced",
					_("Tag {0} marked {1}: {2}").format(code, action, reason or _("no reason given")),
					severity="Low", epc=code, device=device)
				message = _("Marked {0} - item goes back to tagging").format(_(action).lower())
			out.append({"epc": code, "ok": True, "action": action, "message": message})
		except frappe.ValidationError as e:
			frappe.db.rollback(save_point="rfid_remove_tag")
			frappe.clear_messages()
			out.append({"epc": code, "ok": False, "action": action, "message": str(e)})
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


# ---------------------------------------------------------------- bulk tagging


def _find_item(code):
	code = rfid.normalize(code)
	return (frappe.db.get_value("Item Barcode", {"barcode": code}, "parent")
		or frappe.db.get_value("Item", code, "name")
		or frappe.db.get_value("Item", {"item_code": code}, "name"))


@frappe.whitelist()
def pending_groups(warehouse=None):
	"""What still needs tags, grouped so identical units are tagged in one go.

	kind "item": non-serial item, N untagged units in a warehouse
	kind "serials": serial item, N new units waiting for tags (bulk too)
	kind "unit": a set or a customer return, handled one unit at a time
	"""
	require_station("Tag Mapping")
	out = []
	for r in rfid.untagged_items(warehouse=warehouse):
		out.append({"kind": "item", "item_code": r.item_code, "item_name": r.item_name, "warehouse": r.warehouse,
			"to_tag": cint(r.actual_qty) - cint(r.tagged), "in_stock": cint(r.actual_qty)})
	filters = {"custom_rfid_status": ["in", [rfid.PENDING, rfid.RETURNED]], "status": "Active"}
	if warehouse:
		filters["warehouse"] = warehouse
	units = frappe.get_all("Serial No", filters=filters,
		fields=["name", "item_code", "item_name", "warehouse", "custom_rfid_status", "custom_is_garment_set"],
		order_by="creation asc", limit=500)
	tagged_before = set(frappe.get_all("RFID Tag Assignment",
		{"serial_no": ["in", [u.name for u in units] or [""]]}, pluck="serial_no", distinct=True))
	groups = {}
	for u in units:
		if cint(u.custom_is_garment_set) or u.custom_rfid_status == rfid.RETURNED:
			out.append({"kind": "unit", "serial_no": u.name, "item_code": u.item_code, "item_name": u.item_name,
				"warehouse": u.warehouse, "to_tag": 1, "is_set": cint(u.custom_is_garment_set),
				"returned": u.custom_rfid_status == rfid.RETURNED, "retag": u.name in tagged_before})
			continue
		key = (u.item_code, u.warehouse)
		if key not in groups:
			groups[key] = {"kind": "serials", "item_code": u.item_code, "item_name": u.item_name,
				"warehouse": u.warehouse, "to_tag": 0}
			out.append(groups[key])
		groups[key]["to_tag"] += 1
	return out


@frappe.whitelist()
def open_code(code, warehouse=None):
	"""Scanned at the station: an item barcode / item code opens that item's group, a label opens the unit."""
	require_station("Tag Mapping", "Tag Repair")
	code = rfid.normalize(code)
	res = rfid.resolve_code(code)
	if res.kind in ("serial", "component") or (res.kind == "tag" and res.serial_no):
		return {"kind": "unit", "unit": _unit_detail(res.serial_no)}
	item_code = _find_item(code)
	if not item_code:
		if res.kind in ("tag", "unmapped"):
			frappe.throw(_("{0} is an RFID tag. Pick or scan the item first, then read its tags.").format(code))
		frappe.throw(_("{0} is not an item barcode, item code or garment label.").format(code))
	if not rfid.is_rfid_item(item_code):
		frappe.throw(_("{0} is not an RFID item. Tick RFID Tracked Garment on the item.").format(item_code))
	groups = [g for g in pending_groups(warehouse) if g["item_code"] == item_code and g["kind"] != "unit"]
	if not groups:
		return {"kind": "none", "item_code": item_code,
			"item_name": frappe.get_cached_value("Item", item_code, "item_name")}
	return {"kind": "group", "groups": groups}


@frappe.whitelist()
def check_tags(codes, item_code=None):
	"""Live verdict for each tag on the reader before bulk saving."""
	require_station("Tag Mapping", "Tag Repair")
	out = []
	for code in dict.fromkeys(rfid.normalize(c) for c in _load(codes) if c):
		entry = {"code": code, "ok": True, "message": _("New tag")}
		try:
			rfid.check_tag_code(code)
		except frappe.ValidationError as e:
			frappe.clear_last_message()
			entry.update(ok=False, message=str(e))
			out.append(entry)
			continue
		tag = frappe.db.get_value("RFID Tag", code, ["name", "status", "reserved_doctype", "reserved_for",
			"item_code", "serial_no", "component", "current_assignment"], as_dict=True)
		if tag:
			problem = rfid.tag_problem(tag)
			entry.update(ok=not problem, message=problem or _("Free tag"), status=tag.status)
			if tag.current_assignment and tag.item_code == item_code:
				entry["message"] = _("Already on this item")
		out.append(entry)
	return out


def _not_too_many(codes, need, item_code, warehouse):
	"""Fewer tags than waiting pieces is fine (the rest stay untagged); more is never fine."""
	if len(codes) <= need:
		return
	frappe.throw(_("{0} tags read but only {1} pieces of {2} in {3} wait for a tag ({4} too many). "
		"Nothing was tagged.").format(len(codes), need, item_code, warehouse, len(codes) - need),
		title=_("Too many tags"))


@frappe.whitelist(methods=["POST"])
def bulk_assign(item_code, warehouse, codes, device=None):
	"""Tag up to N identical waiting units in one go: every tag usable and no more than N, or nothing is saved."""
	require_station("Tag Mapping")
	codes = list(dict.fromkeys(rfid.normalize(c) for c in _load(codes) if c))
	if not codes:
		frappe.throw(_("No tags read."))
	if not rfid.is_rfid_item(item_code):
		frappe.throw(_("{0} is not an RFID item.").format(item_code))
	if rfid.get_set_definition(item_code):
		frappe.throw(_("{0} is a set. Tag its pieces one by one.").format(item_code))
	# Bad tags first: "this tag is lost / already used" helps more than a count mismatch.
	for code in codes:
		rfid.check_tag_code(code)
		tag = frappe.db.get_value("RFID Tag", code, ["name", "status", "reserved_doctype", "reserved_for",
			"item_code", "serial_no", "component", "current_assignment"], as_dict=True)
		problem = tag and rfid.tag_problem(tag)
		if problem:
			frappe.throw(problem)
	if rfid.has_serial(item_code):
		serials = frappe.get_all("Serial No", {"item_code": item_code, "warehouse": warehouse, "status": "Active",
			"custom_rfid_status": rfid.PENDING}, pluck="name", order_by="creation asc")
		serials = [s for s in serials if not rfid.active_assignment_for(serial_no=s)]
		_not_too_many(codes, len(serials), item_code, warehouse)
		for code, serial_no in zip(codes, serials):
			rfid.assign_tag(code, serial_no, device=device, remarks=_("Bulk tagging"))
	else:
		_not_too_many(codes, rfid.untagged_qty(item_code, warehouse), item_code, warehouse)
		for code in codes:
			rfid.assign_tag(code, item_code=item_code, warehouse=warehouse, device=device, remarks=_("Bulk tagging"))
	return {"assigned": len(codes), "item_code": item_code, "warehouse": warehouse}


# ---------------------------------------------------------------- tag history and repair


@frappe.whitelist()
def tag_story(code):
	"""Everything that ever happened to a tag (or to the tags of one serial): who, when, which garment, which bill."""
	require_station("Tag Mapping", "Tag Repair", "Detach", "Stock Audit", "Audit Review")
	code = rfid.normalize(code)
	filters = None
	tag = None
	if frappe.db.exists("RFID Tag", code):
		tag = frappe.db.get_value("RFID Tag", code, ["name", "status", "item_code", "serial_no", "component",
			"warehouse", "cycle", "basket", "reserved_doctype", "reserved_for", "last_read_on", "current_assignment"],
			as_dict=True)
		tag.item_name = tag.item_code and frappe.get_cached_value("Item", tag.item_code, "item_name")
		filters = {"tag": code}
	elif frappe.db.exists("Serial No", code):
		filters = {"serial_no": code}
	elif frappe.db.exists("Garment Component", code):
		filters = {"component": code}
	else:
		frappe.throw(_("{0} is not a known tag or garment label.").format(code))
	events = frappe.get_all("RFID Tag Event", filters, ["name", "tag", "action", "event_time", "performed_by",
		"previous_status", "new_status", "cycle", "item_code", "item_name", "serial_no", "component", "warehouse",
		"to_warehouse", "amount", "reference_doctype", "reference_name", "device", "reason"],
		order_by="creation desc", limit=500)
	users = {e.performed_by for e in events if e.performed_by}
	names = {u: frappe.utils.get_fullname(u) for u in users}
	for e in events:
		e.by = names.get(e.performed_by, e.performed_by)
	uses = frappe.get_all("RFID Tag Assignment", filters, ["name", "tag", "cycle", "status", "item_code", "serial_no",
		"component", "warehouse", "assigned_on", "assigned_by", "source_doctype", "source_name", "sales_invoice",
		"sold_rate", "sold_by", "sold_on", "closed_on", "close_reason"], order_by="creation desc", limit=200)
	for u in uses:
		u.item_name = u.item_code and frappe.get_cached_value("Item", u.item_code, "item_name")
		u.sold_by_name = u.sold_by and frappe.utils.get_fullname(u.sold_by)
	return {"code": code, "tag": tag, "events": events, "uses": uses}


@frappe.whitelist(methods=["POST"])
def set_condition(epc, status, reason, device=None):
	"""Mark a tag Damaged / Lost / Retired, or Found again. Its garment goes back to the to-tag list."""
	require_station("Tag Repair")
	rfid.set_tag_condition(epc, status, reason, device=device)
	if status in rfid.UNUSABLE:
		rfid.log_exception("Tag Lost" if status == rfid.TAG_LOST else "Tag Replaced",
			_("Tag {0} marked {1}: {2}").format(rfid.normalize(epc), status, reason), severity="Low",
			epc=rfid.normalize(epc), device=device)
	return tag_story(epc)


@frappe.whitelist()
def item_tags(item_code, warehouse=None):
	"""Tags currently standing for units of an item - to find the one to mark lost or damaged."""
	require_station("Tag Repair", "Stock Audit")
	filters = {"item_code": item_code, "status": ["in", list(rfid.IN_STOCK_TAG) + [rfid.TAG_SOLD]]}
	if warehouse:
		filters["warehouse"] = warehouse
	return frappe.get_all("RFID Tag", filters, ["name", "status", "serial_no", "component", "warehouse",
		"last_read_on", "cycle"], order_by="last_read_on asc", limit=500)
