"""Daily handheld stock reconciliation.

A session freezes the expected position at start, collects unique observations
from any number of (re)uploads, and classifies differences. Nothing here posts
stock: shortages go to review and a separate adjustment approval.

A count covers the whole warehouse, or only some items (scope Selected Items):
then only their tags are expected, and tags of other items read on the way are
ignored. A tag not read can be settled right away from the result: it stood
for a piece sold without its tag read (RFID Unread Sale), or the piece is here
and its tag is dead.
"""

import frappe
from frappe import _
from frappe.utils import cint, now_datetime, today

from rfid_retail import rfid, unread
from rfid_retail.settings import get as setting, is_enabled, require_station
from rfid_retail.api.pos import _load

IN_STORE = (rfid.READY, rfid.PENDING, rfid.RETURNED, rfid.HOLD)


@frappe.whitelist(methods=["POST"])
def start(warehouse, device=None, scope="Full Store", areas=None, items=None):
	"""items: [{"item_code": ...} or {"item_group": ...}] counts only those items (a template = its variants)."""
	require_station("Stock Audit")
	items = [r for r in _load_rows(items) if r.get("item_code") or r.get("item_group")]
	doc = frappe.get_doc({"doctype": "RFID Audit Session", "audit_date": today(), "warehouse": warehouse,
		"device": device, "scope": "Selected Items" if items else scope, "operator": frappe.session.user})
	if areas:
		doc.areas = areas
	for r in items:
		doc.append("scope_items", {"item_code": r.get("item_code"), "item_group": r.get("item_group")})
	doc.insert(ignore_permissions=True)
	return begin(doc.name)


def _load_rows(value):
	if isinstance(value, str):
		import json

		value = json.loads(value or "[]")
	return value or []


def scope_item_codes(doc):
	"""Item codes a Selected Items count covers; None when it covers the whole warehouse."""
	if doc.scope != "Selected Items":
		return None
	codes = set()
	for row in doc.scope_items:
		if row.item_code:
			codes.add(row.item_code)
			codes.update(frappe.get_all("Item", {"variant_of": row.item_code}, pluck="name"))
		if row.item_group:
			lft, rgt = frappe.db.get_value("Item Group", row.item_group, ["lft", "rgt"])
			groups = frappe.get_all("Item Group", {"lft": [">=", lft], "rgt": ["<=", rgt]}, pluck="name")
			codes.update(frappe.get_all("Item", {"item_group": ["in", groups]}, pluck="name"))
	return codes


@frappe.whitelist()
def scope_search(txt=""):
	"""Items (templates too) and item groups to pick for a count of selected items."""
	require_station("Stock Audit")
	like = f"%{(txt or '').strip()}%"
	items = frappe.db.sql("""
		select i.name, i.item_name, i.has_variants from `tabItem` i
		where i.disabled = 0 and (i.name like %(like)s or i.item_name like %(like)s)
			and (i.custom_rfid_tracked = 1 or (i.has_variants = 1 and exists (select 1 from `tabItem` v
				where v.variant_of = i.name and v.custom_rfid_tracked = 1)))
		order by i.has_variants desc, i.item_name limit 15""", {"like": like}, as_dict=True)
	groups = frappe.get_all("Item Group", {"name": ["like", like]}, ["name", "is_group"], limit=5, order_by="name")
	return [{"item_code": i.name, "label": i.item_name, "kind": _("All variants") if i.has_variants else i.name}
		for i in items] + [{"item_group": g.name, "label": g.name, "kind": _("Item group")} for g in groups]


@frappe.whitelist(methods=["POST"])
def begin(session):
	"""Freeze the expected identities at this moment (cut-off)."""
	require_station("Stock Audit")
	doc = frappe.get_doc("RFID Audit Session", session, for_update=True)
	if doc.status != "Planned":
		frappe.throw(_("Session {0} already started.").format(doc.name))
	doc.cutoff = now_datetime()
	doc.expected = []
	codes = scope_item_codes(doc)
	if codes is not None and not codes:
		frappe.throw(_("The chosen items or groups have no items."))
	in_scope = {"item_code": ["in", list(codes)]} if codes is not None else {}
	units = frappe.get_all("Serial No",
		filters={"warehouse": doc.warehouse, "status": "Active", "custom_rfid_status": ["in", IN_STORE], **in_scope},
		fields=["name", "item_code", "custom_is_garment_set"])
	for sn in units:
		if not rfid.is_rfid_item(sn.item_code):
			continue
		if cint(sn.custom_is_garment_set):
			for c in rfid.get_components(sn.name):
				doc.append("expected", {"serial_no": sn.name, "component": c.name, "tag": c.rfid_tag,
					"item_code": sn.item_code})
		else:
			asg = rfid.active_assignment_for(serial_no=sn.name)
			doc.append("expected", {"serial_no": sn.name, "item_code": sn.item_code,
				"tag": asg and frappe.db.get_value("RFID Tag Assignment", asg, "tag")})
	tags = frappe.get_all("RFID Tag", {"warehouse": doc.warehouse, "status": ["in", rfid.IN_STOCK_TAG],
		"serial_no": ("is", "not set"), **in_scope}, ["name", "item_code"])
	for t in tags:
		doc.append("expected", {"item_code": t.item_code, "tag": t.name})
	doc.expected_units = len(units) + len(tags)
	doc.status = "In Progress"
	doc.save(ignore_permissions=True)
	return summary(doc.name)


@frappe.whitelist(methods=["POST"])
def upload(session, batch_id, codes, area=None, device=None):
	"""Handheld upload. Re-sending the same batch_id changes nothing."""
	require_station("Stock Audit")
	doc = frappe.get_doc("RFID Audit Session", session, for_update=True)
	if doc.status not in ("In Progress", "Comparison Ready", "Investigation Pending"):
		frappe.throw(_("Session {0} is {1}; uploads are closed.").format(doc.name, doc.status))
	if any(u.batch_id == batch_id for u in doc.uploads):
		return summary(doc.name, note=_("Batch {0} was already received.").format(batch_id))
	have = {o.epc for o in doc.observations}
	codes = [rfid.normalize(c) for c in _load(codes) if c]
	now = now_datetime()
	new = 0
	for code in codes:
		if code in have:
			continue
		have.add(code)
		new += 1
		doc.append("observations", {"epc": code, "area": area, "first_read_at": now, "device": device,
			"batch_id": batch_id})
	doc.append("uploads", {"batch_id": batch_id, "device": device, "area": area, "uploaded_at": now,
		"read_count": len(codes), "new_count": new})
	doc.last_upload = now
	doc.save(ignore_permissions=True)
	if device and frappe.db.exists("RFID Device", device):
		frappe.db.set_value("RFID Device", device, {"last_seen": now, "health": "Online"}, update_modified=False)
	return summary(doc.name)


@frappe.whitelist(methods=["POST"])
def compare(session):
	require_station("Stock Audit")
	doc = frappe.get_doc("RFID Audit Session", session, for_update=True)
	if doc.status in ("Submitted for Review", "Reviewed"):
		frappe.throw(_("Session {0} is {1}.").format(doc.name, doc.status))
	observed = {o.epc for o in doc.observations}
	prior = {(r.classification, r.serial_no, r.component, r.epc): r for r in doc.results}
	results = []

	def add(classification, serial_no=None, component=None, epc=None, item_code=None, detail=None):
		old = prior.get((classification, serial_no, component, epc))
		results.append({"classification": classification, "serial_no": serial_no, "component": component,
			"epc": epc, "item_code": item_code, "detail": detail,
			"resolution": old.resolution if old else ("" if classification == "Matched" else "Pending"),
			"note": old.note if old else None, "unread_sale": old.unread_sale if old else None})

	by_serial = {}
	for e in doc.expected:
		# a non-serial unit is known only by its tag
		by_serial.setdefault(e.serial_no or f"tag:{e.tag}", []).append(e)
	expected_tags = {e.tag for e in doc.expected if e.tag}
	verified = incomplete = unverified = 0
	for serial_no, rows in by_serial.items():
		item = rows[0].item_code
		serial_no = rows[0].serial_no
		if not rows[0].component:
			if rows[0].tag in observed:
				verified += 1
				add("Matched", serial_no, epc=rows[0].tag, item_code=item)
			else:
				unverified += 1
				add("Unverified", serial_no, epc=rows[0].tag, item_code=item,
					detail=_("Not read in first pass. Rescan before calling it missing."))
			continue
		seen = [r for r in rows if r.tag in observed]
		away = [r for r in rows if r not in seen
			and frappe.db.get_value("Garment Component", r.component, "status") == "Out for Alteration"]
		missing = [r for r in rows if r not in seen and r not in away]
		if not missing and not away:
			verified += 1
			add("Matched", serial_no, item_code=item, detail=_("All {0} pieces read").format(len(rows)))
		elif away and not missing:
			add("Held Outside Store", serial_no, item_code=item,
				detail=_("At alteration: {0}").format(", ".join(r.component for r in away)))
		elif seen:
			incomplete += 1
			for r in missing:
				add("Incomplete Set", serial_no, r.component, r.tag, item,
					_("{0} of {1} pieces read").format(len(seen), len(rows)))
		else:
			unverified += 1
			add("Unverified", serial_no, item_code=item, detail=_("No piece of this set was read."))

	unexpected = sold_seen = 0
	codes = scope_item_codes(doc)
	for epc in sorted(observed - expected_tags):
		res = rfid.resolve_code(epc)
		if codes is not None and res.item_code and res.item_code not in codes:
			continue  # another item's tag, read on the way
		if res.kind != "tag":
			unexpected += 1
			add("Unmapped", epc=epc, detail=_("Tag has no active garment"))
		elif res.tag_status == rfid.TAG_SOLD:
			sold_seen += 1
			add("Sold but Observed", res.serial_no, res.component, epc, res.item_code,
				_("Sold on {0}. Tag not detached, or garment still here?").format(_sold_on(res)))
		else:
			unexpected += 1
			add("Unexpected", res.serial_no, res.component, epc, res.item_code,
				_("Expected in {0}").format(res.warehouse))

	for r in rfid.untagged_items(warehouse=doc.warehouse):
		if codes is not None and r.item_code not in codes:
			continue
		add("Untagged Stock", item_code=r.item_code,
			detail=_("{0} in stock, {1} with tags. Tag the rest.").format(cint(r.actual_qty), cint(r.tagged)))

	_hint_unread_sales(doc.warehouse, results)
	doc.results = []
	for r in results:
		doc.append("results", r)
	doc.verified_units, doc.incomplete_sets, doc.unverified_count = verified, incomplete, unverified
	doc.unexpected_count, doc.sold_observed_count = unexpected, sold_seen
	doc.movements_after_cutoff = frappe.db.count("Stock Ledger Entry",
		{"warehouse": doc.warehouse, "creation": [">", doc.cutoff], "is_cancelled": 0})
	doc.status = "Investigation Pending" if any(r["classification"] != "Matched" for r in results) else "Comparison Ready"
	doc.save(ignore_permissions=True)
	return summary(doc.name)


def _hint_unread_sales(warehouse, results):
	"""Tags not read of an item with pieces sold unread: most likely the tags of those pieces."""
	by_item = {}
	for r in results:
		if r["classification"] == "Unverified" and r["epc"] and not r["serial_no"] and r["resolution"] == "Pending":
			by_item.setdefault(r["item_code"], []).append(r)
	for item_code, rows in by_item.items():
		waiting = unread.open_sales(item_code, warehouse)
		if not waiting:
			continue
		n = sum(w.waiting for w in waiting)
		for r in rows:
			r["detail"] = _("Not read. {0} piece(s) of this item were sold without their tag read ({1}): "
				"this may be one of those tags.").format(n, ", ".join(w.name for w in waiting))


@frappe.whitelist(methods=["POST"])
def resolve_tag(session, epc, action, note=None):
	"""A tag the count did not read, settled from the result right away.

	sold: it stood for a piece sold without its tag read; the oldest open RFID Unread
	Sale of the item takes it and the tag is closed against that invoice.
	damaged: the piece is here but its tag does not read; the tag is marked Damaged
	and the piece goes back to the list of stock to tag.
	"""
	require_station("Stock Audit")
	doc = frappe.get_doc("RFID Audit Session", session, for_update=True)
	if doc.status not in ("Comparison Ready", "Investigation Pending"):
		frappe.throw(_("Finish the count first. Session {0} is {1}.").format(doc.name, doc.status))
	epc = rfid.normalize(epc)
	row = next((r for r in doc.results if r.epc == epc and r.classification in ("Unverified", "Incomplete Set")), None)
	if not row:
		frappe.throw(_("Tag {0} is not among the tags this count did not read.").format(epc))
	if row.resolution in ("Sold Without Read", "Tag Damaged", "Tag Lost"):
		frappe.throw(_("Tag {0} is already settled ({1}).").format(epc, row.resolution))
	if epc in {o.epc for o in doc.observations}:
		frappe.throw(_("Tag {0} was read in this count. Run Finish count again.").format(epc))
	note = (note or "").strip() or None
	if action == "sold":
		if row.serial_no:
			frappe.throw(_("{0} has a serial number; a sale without its tag read is not recorded that way.").format(
				row.serial_no))
		waiting = unread.open_sales(row.item_code, doc.warehouse)
		if not waiting:
			frappe.throw(_("No piece of {0} in {1} was sold without its tag read.").format(row.item_code, doc.warehouse))
		urs = unread.settle(waiting[0].name, epc, doc.name)
		row.resolution, row.unread_sale = "Sold Without Read", urs.name
		row.note = note or _("Tag of a piece sold on {0} without its tag read").format(urs.reference_name)
	elif action == "damaged":
		rfid.set_tag_condition(epc, rfid.TAG_DAMAGED, note or _("Piece found, tag not reading (stock count {0})").format(
			doc.name))
		row.resolution, row.note = "Tag Damaged", note or _("Piece here, tag not reading. Put a new tag on it.")
	else:
		frappe.throw(_("Unknown action {0}").format(action))
	doc.save(ignore_permissions=True)
	return summary(doc.name)


@frappe.whitelist(methods=["POST"])
def discard(session):
	"""Throw away a count started by mistake (wrong warehouse, test). Reviewed counts are kept."""
	require_station("Stock Audit")
	doc = frappe.get_doc("RFID Audit Session", session)
	if doc.status in ("Submitted for Review", "Reviewed"):
		frappe.throw(_("{0} is {1} and is kept as a record.").format(doc.name, doc.status))
	frappe.delete_doc("RFID Audit Session", doc.name, ignore_permissions=True)
	return "ok"


@frappe.whitelist(methods=["POST"])
def submit_for_review(session):
	require_station("Stock Audit")
	doc = frappe.get_doc("RFID Audit Session", session)
	if doc.status not in ("Comparison Ready", "Investigation Pending"):
		frappe.throw(_("Run the comparison first."))
	doc.db_set("status", "Submitted for Review")
	return summary(doc.name)


@frappe.whitelist(methods=["POST"])
def mark_reviewed(session):
	"""Owner/manager sign-off. The operator cannot review their own count."""
	require_station("Audit Review")
	doc = frappe.get_doc("RFID Audit Session", session)
	if doc.status != "Submitted for Review":
		frappe.throw(_("Session is {0}.").format(doc.status))
	if doc.operator == frappe.session.user and frappe.session.user != "Administrator":
		frappe.throw(_("The operator who counted cannot review the same session."))
	for r in doc.results:
		if r.classification in ("Unverified", "Incomplete Set", "Confirmed Shortage") and r.resolution in (
			"Pending", "Confirmed Missing", "Adjustment Requested"):
			rfid.log_exception("Audit Shortage", _("{0}: {1} {2} unresolved after review").format(
				doc.name, r.classification, r.component or r.serial_no), severity="High",
				serial_no=r.serial_no, component=r.component, reference_doctype=doc.doctype,
				reference_name=doc.name)
		if r.resolution == "Tag Lost" and r.epc and frappe.db.get_value("RFID Tag", r.epc, "status") not in rfid.UNUSABLE:
			rfid.set_tag_condition(r.epc, rfid.TAG_LOST, _("Not found in stock count {0}").format(doc.name))
	doc.db_set({"status": "Reviewed", "reviewed_by": frappe.session.user, "reviewed_on": now_datetime()})
	return summary(doc.name)


@frappe.whitelist()
def summary(session, note=None):
	require_station("Stock Audit", "Audit Review")
	doc = frappe.get_doc("RFID Audit Session", session)
	counts = {}
	for r in doc.results:
		counts[r.classification] = counts.get(r.classification, 0) + 1
	return {"name": doc.name, "status": doc.status, "warehouse": doc.warehouse, "cutoff": doc.cutoff,
		"expected_units": doc.expected_units, "expected_identities": len(doc.expected),
		"observations": len(doc.observations), "uploads": len(doc.uploads), "results": counts,
		"verified_units": doc.verified_units, "incomplete_sets": doc.incomplete_sets,
		"movements_after_cutoff": doc.movements_after_cutoff, "note": note,
		"checklist": _checklist(doc), "observed": [o.epc for o in doc.observations], "unknown": _unknown(doc),
		"scope": doc.scope, "scope_items": [r.item_code or r.item_group for r in doc.scope_items],
		"by_item": _by_item(doc)}


def _by_item(doc):
	"""Per item without serial numbers: tags expected, read, and the ones not read with what was done about them."""
	observed = {o.epc for o in doc.observations}
	settled = {r.epc: r for r in doc.results if r.epc}
	items = {}
	for e in doc.expected:
		if e.serial_no or not e.tag:
			continue
		it = items.setdefault(e.item_code, {"item_code": e.item_code, "expected": 0, "found": 0, "not_read": []})
		it["expected"] += 1
		if e.tag in observed:
			it["found"] += 1
		else:
			r = settled.get(e.tag)
			it["not_read"].append({"tag": e.tag, "resolution": r.resolution if r else None,
				"unread_sale": r.unread_sale if r else None})
	waiting = {}
	for w in frappe.get_all("RFID Unread Sale", {"warehouse": doc.warehouse, "status": "Open",
		"item_code": ["in", list(items) or [""]]}, ["name", "item_code", "qty", "settled_qty", "reference_name", "reason"],
		order_by="creation asc"):
		waiting.setdefault(w.item_code, []).append({"name": w.name, "invoice": w.reference_name, "reason": w.reason,
			"waiting": cint(w.qty) - cint(w.settled_qty)})
	out = []
	for it in items.values():
		it["item_name"] = frappe.get_cached_value("Item", it["item_code"], "item_name")
		it["unread_sales"] = waiting.get(it["item_code"], [])
		out.append(it)
	# items with tags not read first
	return sorted(out, key=lambda i: (i["expected"] == i["found"], i["item_name"] or ""))


def _sold_on(res):
	if res.serial_no:
		return frappe.db.get_value("Serial No", res.serial_no, "custom_sales_invoice")
	return frappe.db.get_value("RFID Tag Assignment", frappe.db.get_value("RFID Tag", res.code, "current_assignment"),
		"sales_invoice")


def _unknown(doc):
	"""Scanned codes that match no expected tag, with a plain reason for staff."""
	expected = {e.tag for e in doc.expected if e.tag}
	codes = scope_item_codes(doc)
	out = []
	for o in doc.observations:
		if o.epc in expected:
			continue
		res = rfid.resolve_code(o.epc)
		if codes is not None and res.item_code and res.item_code not in codes:
			continue  # another item's tag, read on the way
		if res.kind in ("serial", "component"):
			reason = _("This is a printed label, not an RFID tag. Scan the tag on the garment.")
		elif res.kind == "unknown":
			reason = _("This tag is not registered in the system.")
		elif res.kind == "unmapped" and res.tag_status in rfid.UNUSABLE:
			reason = _("This tag was marked {0}. If it is on a garment, mark it found and re-tag.").format(res.tag_status)
		elif res.kind == "unmapped":
			reason = _("Free tag, not on any garment (loose tag lying around?).")
		elif res.tag_status == rfid.TAG_SOLD:
			reason = _("Belongs to a sold garment ({0}). Tag not removed, or garment still here?").format(
				res.serial_no or res.item_code)
		else:
			reason = _("Belongs to {0}, which is recorded in {1}.").format(res.serial_no or res.item_code, res.warehouse)
		out.append({"code": o.epc, "reason": reason})
	return out


def _checklist(doc):
	"""Expected garments with their pieces and tags, for the live checklist on the count screen."""
	names = {}
	garments = {}
	for e in doc.expected:
		key = e.serial_no or f"tag:{e.tag}"
		if key not in garments:
			if e.item_code not in names:
				names[e.item_code] = frappe.get_cached_value("Item", e.item_code, "item_name")
			garments[key] = {"serial_no": e.serial_no or e.tag, "item_name": names[e.item_code], "pieces": []}
		role = frappe.db.get_value("Garment Component", e.component, "role") if e.component else None
		garments[key]["pieces"].append({"role": role or names[e.item_code], "tag": e.tag})
	return list(garments.values())


def flag_missed_audit():
	"""Scheduler (late evening): no compared session today is itself an exception."""
	if not is_enabled() or not cint(setting("daily_audit_required")):
		return
	for wh in {d.warehouse for d in frappe.get_all("POS Profile", {"disabled": 0}, ["warehouse"])}:
		done = frappe.db.exists("RFID Audit Session", {"audit_date": today(), "warehouse": wh,
			"status": ["in", ["Comparison Ready", "Investigation Pending", "Submitted for Review", "Reviewed"]]})
		if not done:
			rfid.log_exception("Missed Audit", _("No completed stock count for {0} on {1}.").format(wh, today()),
				severity="High", reference_doctype="Warehouse", reference_name=wh)
