"""Stock documents and RFID tags.

Purchase Receipt and Stock Entry rows (where stock comes in, goes out or moves)
carry the tags read for them in `custom_rfid_tags`, usually by bulk-reading every tag of
one item at the station (identical units, so which tag goes on which piece does
not matter).

- Draft save: tags are checked and reserved for this document.
- Submit: inward rows activate the tags on the new units, outward rows free them,
  transfers move them to the target warehouse.
- Cancel: undone where the tag was not reused since; otherwise the unit is
  flagged for a new tag.

Units received without tags are not lost: they show up in the Tag garments
station until someone tags them.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt

from rfid_retail import rfid
from rfid_retail.settings import get as setting, is_enabled, require_station
from rfid_retail.overrides.sales_invoice import get_row_serials

TAGGED_DOCTYPES = ("Purchase Receipt", "Stock Entry")
IN, OUT, TRANSFER = "in", "out", "transfer"


def row_tags(row):
	seen = []
	for code in (row.get("custom_rfid_tags") or "").replace(",", "\n").split():
		code = rfid.normalize(code)
		if code and code not in seen:
			seen.append(code)
	return seen


def direction(doc, row):
	if doc.doctype == "Stock Entry":
		if row.get("s_warehouse") and row.get("t_warehouse"):
			return TRANSFER
		return IN if row.get("t_warehouse") else OUT
	if doc.doctype == "Purchase Invoice" and not cint(doc.get("update_stock")):
		return None
	if doc.doctype == "Stock Reconciliation":
		return IN
	return OUT if cint(doc.get("is_return")) else IN


def target_warehouse(row):
	return row.get("t_warehouse") or row.get("warehouse")


def source_warehouse(row):
	return row.get("s_warehouse") or row.get("warehouse")


def unit_qty(row):
	"""Whole stock units on the row; one tag per stock unit."""
	return abs(cint(flt(row.get("transfer_qty") or row.get("stock_qty") or row.get("qty"))))


# ---------------------------------------------------------------- draft


def validate(doc, method=None):
	if not is_enabled() or doc.doctype not in TAGGED_DOCTYPES or doc.docstatus != 0:
		return
	keep = set()
	used = {}
	for row in doc.get("items") or []:
		tags = row_tags(row)
		row.custom_rfid_tag_count = len(tags)
		if not tags:
			row.custom_rfid_tags = None
			continue
		row.custom_rfid_tags = "\n".join(tags)
		if not rfid.is_rfid_item(row.item_code):
			frappe.throw(_("Row {0}: {1} is not an RFID item. Remove its tags.").format(row.idx, row.item_code))
		way = direction(doc, row)
		if not way:
			frappe.throw(_("Tick Update Stock to tag the items on this invoice."))
		if rfid.get_set_definition(row.item_code):
			frappe.throw(_("Row {0}: {1} is a set. Tag its pieces one by one at the Tag garments station.").format(
				row.idx, row.item_code))
		if len(tags) > unit_qty(row):
			frappe.throw(_("Row {0}: {1} tags read for {2} units of {3}. Take out the extra tags.").format(
				row.idx, len(tags), unit_qty(row), row.item_code))
		for code in tags:
			if code in used:
				frappe.throw(_("Tag {0} is on row {1} and row {2}.").format(code, used[code], row.idx))
			used[code] = row.idx
			rfid.check_tag_code(code)
			if way == IN:
				rfid.reserve_tag(code, doc.doctype, doc.name, row.name, row.item_code)
				keep.add(code)
			elif not rfid.has_serial(row.item_code):
				_check_outgoing(row, code)
	for name in frappe.get_all("RFID Tag", {"reserved_doctype": doc.doctype, "reserved_for": doc.name}, pluck="name"):
		if name not in keep:
			rfid.release_reservation(name, _("Removed from the draft"))


def _check_outgoing(row, code):
	res = rfid.resolve_code(code)
	wh = source_warehouse(row)
	if res.kind != "tag" or res.serial_no:
		frappe.throw(_("Row {0}: tag {1} is not on a unit of {2}.").format(row.idx, code, row.item_code))
	if res.item_code != row.item_code:
		frappe.throw(_("Row {0}: tag {1} belongs to {2}, not {3}.").format(row.idx, code, res.item_code, row.item_code))
	if res.warehouse != wh:
		frappe.throw(_("Row {0}: tag {1} is recorded in {2}, not {3}.").format(row.idx, code, res.warehouse, wh))


# ---------------------------------------------------------------- submit / cancel


def on_submit(doc, method=None):
	if not is_enabled():
		return
	ref = (doc.doctype, doc.name)
	for row in doc.get("items") or []:
		if not rfid.is_rfid_item(row.item_code):
			continue
		way = direction(doc, row)
		serials = get_row_serials(row) if rfid.has_serial(row.item_code) else []
		tags = row_tags(row) if doc.doctype in TAGGED_DOCTYPES else []
		if way == IN:
			for serial_no in serials:
				rfid.ensure_serial_setup(serial_no)
			_activate(row, tags, serials, ref)
		elif way == OUT:
			reason = "Returned To Supplier" if cint(doc.get("is_return")) else "Issued Out"
			for code in tags if not serials else _serial_tags(serials):
				rfid.close_assignment(code, reason, action="Issued Out", reference_doctype=doc.doctype,
					reference_name=doc.name)
		elif way == TRANSFER:
			for code in tags if not serials else _serial_tags(serials):
				_move(code, source_warehouse(row), target_warehouse(row), ref)


def _activate(row, tags, serials, ref):
	source = ref + (row.name,)
	if serials:
		free = [s for s in serials if not rfid.active_assignment_for(serial_no=s)]
		for code, serial_no in zip(tags, free):
			rfid.assign_tag(code, serial_no, source=source)
		return
	for code in tags:
		rfid.assign_tag(code, item_code=row.item_code, warehouse=target_warehouse(row), source=source)


def _serial_tags(serials):
	return frappe.get_all("RFID Tag Assignment", {"serial_no": ["in", serials], "status": "Active"}, pluck="tag")


def _move(code, from_wh, to_wh, ref):
	tag = frappe.get_doc("RFID Tag", code, for_update=True)
	tag.warehouse = to_wh
	tag.save(ignore_permissions=True)
	if tag.current_assignment:
		frappe.db.set_value("RFID Tag Assignment", tag.current_assignment, "warehouse", to_wh)
	rfid.log_tag_event(tag, "Transferred", previous_status=tag.status, warehouse=from_wh, to_warehouse=to_wh,
		reference_doctype=ref[0], reference_name=ref[1])


def on_cancel(doc, method=None):
	if not is_enabled():
		return
	# Tags this document put on garments: free them (the stock they stood for is gone again).
	for asg in frappe.get_all("RFID Tag Assignment",
		{"source_doctype": doc.doctype, "source_name": doc.name, "status": "Active"}, ["tag"]):
		rfid.close_assignment(asg.tag, "Inward Cancelled", action="Correction", reference_doctype=doc.doctype,
			reference_name=doc.name, remarks=_("{0} cancelled").format(doc.name))
	restore_tags(doc, ("Issued Out",))
	for ev in _events(doc, ("Transferred",)):
		tag = frappe.get_doc("RFID Tag", ev.tag, for_update=True)
		if cint(tag.cycle) == cint(ev.cycle) and tag.current_assignment:
			_move(ev.tag, ev.to_warehouse, ev.warehouse, (doc.doctype, doc.name))


def on_trash(doc, method=None):
	for name in frappe.get_all("RFID Tag", {"reserved_doctype": doc.doctype, "reserved_for": doc.name}, pluck="name"):
		rfid.release_reservation(name, _("{0} deleted").format(doc.name))


def _events(doc, actions):
	return frappe.get_all("RFID Tag Event",
		{"reference_doctype": doc.doctype, "reference_name": doc.name, "action": ["in", actions]},
		["tag", "action", "cycle", "item_code", "serial_no", "component", "warehouse", "to_warehouse"],
		order_by="creation asc")


def restore_tags(doc, actions):
	"""Undo tag releases made by a cancelled document, as a new use of each tag.

	Only when the tag was not put on another garment since; otherwise the unit is
	flagged for a new tag and an exception is raised.
	"""
	for ev in _events(doc, actions):
		tag = frappe.db.get_value("RFID Tag", ev.tag, ["status", "cycle"], as_dict=True)
		reusable = tag.status == rfid.TAG_AVAILABLE and cint(tag.cycle) == cint(ev.cycle)
		label = ev.component or ev.serial_no or ev.item_code
		if reusable:
			frappe.db.savepoint("rfid_restore")
			try:
				rfid.assign_tag(ev.tag, ev.serial_no, ev.component, item_code=ev.item_code,
					warehouse=ev.warehouse, source=(doc.doctype, doc.name),
					remarks=_("Restored: {0} cancelled").format(doc.name))
				continue
			except frappe.ValidationError:
				frappe.db.rollback(save_point="rfid_restore")
				frappe.clear_last_message()
		rfid.log_exception("Untagged Stock",
			_("{0} cancelled. {1} is back in stock but tag {2} is now {3}. Put a new tag on it.").format(
				doc.name, label, ev.tag, tag.status),
			severity="Medium", epc=ev.tag, serial_no=ev.serial_no, component=ev.component,
			reference_doctype=doc.doctype, reference_name=doc.name)


# ---------------------------------------------------------------- desk helpers


def _row_doc(doctype, name, row):
	from rfid_retail.settings import require_enabled

	require_enabled()
	if doctype not in TAGGED_DOCTYPES:
		frappe.throw(_("RFID tags are read on {0}.").format(_(" / ").join(TAGGED_DOCTYPES)))
	doc = frappe.get_doc(doctype, name)
	doc.check_permission("submit" if doc.meta.is_submittable else "write")
	if doc.docstatus != 1:
		frappe.throw(_("Submit {0} first. Tags are read for stock that has come in.").format(name))
	child = next((r for r in doc.items if r.name == row), None)
	if not child:
		frappe.throw(_("Row not found."))
	if not rfid.is_rfid_item(child.item_code):
		frappe.throw(_("{0} is not an RFID item.").format(child.item_code))
	if rfid.get_set_definition(child.item_code):
		frappe.throw(_("{0} is a set. Tag its pieces one by one at the Tag garments station.").format(child.item_code))
	return doc, child


def _row_state(doc, row):
	"""How many tags this row still needs, and which units they go on."""
	way = direction(doc, row)
	qty = unit_qty(row)
	done = row_tags(row)
	out = {"row": row.name, "idx": row.idx, "item_code": row.item_code, "item_name": row.item_name,
		"direction": way, "qty": qty, "done": len(done), "warehouse": target_warehouse(row),
		"from_warehouse": source_warehouse(row) if way != IN else None}
	if way == IN and rfid.has_serial(row.item_code):
		serials = [s for s in get_row_serials(row) if not rfid.active_assignment_for(serial_no=s)
			and frappe.db.get_value("Serial No", s, "status") == "Active"]
		out["serials"] = serials
		out["to_tag"] = len(serials)
	elif way == IN:
		tagged = frappe.db.count("RFID Tag Assignment", {"source_doctype": doc.doctype, "source_name": doc.name,
			"source_row": row.name})
		out["to_tag"] = max(min(qty - tagged, rfid.untagged_qty(row.item_code, target_warehouse(row))), 0)
	else:
		out["to_tag"] = max(qty - len(done), 0)
	return out


@frappe.whitelist()
def row_tag_rows(doctype, name):
	"""RFID rows of a submitted document with what each still needs."""
	from rfid_retail.settings import require_enabled

	require_enabled()
	doc = frappe.get_doc(doctype, name)
	doc.check_permission("read")
	return [_row_state(doc, r) for r in doc.items
		if rfid.is_rfid_item(r.item_code) and not rfid.get_set_definition(r.item_code) and direction(doc, r)]


@frappe.whitelist()
def check_row_tags(doctype, name, row, codes):
	"""Live verdict for each tag on the reader, before anything is saved."""
	from rfid_retail.api.pos import _load

	doc, child = _row_doc(doctype, name, row)
	way = direction(doc, child)
	out = []
	for code in dict.fromkeys(rfid.normalize(c) for c in _load(codes) if c):
		out.append({"code": code, **_code_status(child, way, code)})
	return {"state": _row_state(doc, child), "codes": out}


def _code_status(row, way, code):
	try:
		rfid.check_tag_code(code)
	except frappe.ValidationError as e:
		frappe.clear_last_message()
		return {"ok": False, "message": str(e)}
	tag = frappe.db.get_value("RFID Tag", code,
		["name", "status", "reserved_doctype", "reserved_for", "reserved_row", "item_code", "serial_no",
			"component", "current_assignment", "warehouse"], as_dict=True)
	if way == IN:
		if not tag:
			return {"ok": True, "message": _("New tag")}
		if tag.current_assignment:
			name = frappe.get_cached_value("Item", tag.item_code, "item_name") if tag.item_code else ""
			return {"ok": False, "message": _("Already on {0} ({1}). Take it off the reader.").format(
				name or tag.item_code, tag.serial_no or tag.warehouse or "")}
		problem = rfid.tag_problem(tag)
		return {"ok": not problem, "message": problem or _("Free tag")}
	try:
		_check_outgoing(row, code)
	except frappe.ValidationError as e:
		frappe.clear_last_message()
		return {"ok": False, "message": str(e)}
	return {"ok": True, "message": _("On {0}").format(row.item_code)}


@frappe.whitelist(methods=["POST"])
def assign_row_tags(doctype, name, row, codes):
	"""Save the tags read for one row, all usable, or nothing. Incoming stock may be tagged in part
	(the rest stays untagged); moving or sending stock out needs the tag of every unit."""
	from rfid_retail.api.pos import _load

	doc, child = _row_doc(doctype, name, row)
	codes = list(dict.fromkeys(rfid.normalize(c) for c in _load(codes) if c))
	state = _row_state(doc, child)
	need = state["to_tag"]
	if not need:
		frappe.throw(_("Row {0}: every unit of {1} already has its tag.").format(child.idx, child.item_code))
	if len(codes) > need or (len(codes) < need and state["direction"] != IN):
		more_less = _("{0} too many").format(len(codes) - need) if len(codes) > need else _("{0} missing").format(
			need - len(codes))
		frappe.throw(_("Row {0}: {1} tags read for {2} pieces of {3} ({4}). Nothing was tagged.").format(
			child.idx, len(codes), need, child.item_code, more_less), title=_("Tag count does not match"))
	bad = [(c, v["message"]) for c in codes for v in [_code_status(child, state["direction"], c)] if not v["ok"]]
	if bad:
		frappe.throw("<br>".join(f"<b>{frappe.utils.escape_html(c)}</b>: {frappe.utils.escape_html(m)}" for c, m in bad)
			+ "<br>" + _("Nothing was tagged."), title=_("Some tags cannot be used"))
	way, source = state["direction"], (doc.doctype, doc.name, child.name)
	if way == IN and state.get("serials") is not None:
		for code, serial_no in zip(codes, state["serials"]):
			rfid.assign_tag(code, serial_no, source=source)
	elif way == IN:
		for code in codes:
			rfid.assign_tag(code, item_code=child.item_code, warehouse=target_warehouse(child), source=source)
	elif way == TRANSFER:
		for code in codes:
			_move(code, source_warehouse(child), target_warehouse(child), source)
	else:
		reason = "Returned To Supplier" if cint(doc.get("is_return")) else "Issued Out"
		for code in codes:
			rfid.close_assignment(code, reason, action="Issued Out", reference_doctype=doc.doctype,
				reference_name=doc.name)
	saved = row_tags(child) + codes
	frappe.db.set_value(child.doctype, child.name, {"custom_rfid_tags": "\n".join(saved),
		"custom_rfid_tag_count": len(saved)}, update_modified=False)
	return {"tagged": len(codes), "state": _row_state(frappe.get_doc(doctype, name), next(
		r for r in frappe.get_doc(doctype, name).items if r.name == row))}


@frappe.whitelist()
def setup_existing_serials(item_code=None):
	"""Backfill for serials created before the item was flagged RFID tracked."""
	require_station("Tag Repair")
	filters = {"status": "Active"}
	if item_code:
		filters["item_code"] = item_code
	count = 0
	for serial_no in frappe.get_all("Serial No", filters=filters, pluck="name"):
		item = frappe.db.get_value("Serial No", serial_no, "item_code")
		if rfid.is_rfid_item(item):
			rfid.ensure_serial_setup(serial_no)
			count += 1
	return count


def release_stale_reservations():
	"""Scheduler: free tags reserved on drafts that were deleted or cancelled."""
	if not is_enabled():
		return
	for tag in frappe.get_all("RFID Tag", {"status": rfid.TAG_RESERVED}, ["name", "reserved_doctype", "reserved_for"]):
		alive = tag.reserved_doctype and frappe.db.get_value(tag.reserved_doctype, tag.reserved_for, "docstatus")
		if alive is None or cint(alive) == 2:
			rfid.release_reservation(tag.name, _("Draft no longer exists"))
