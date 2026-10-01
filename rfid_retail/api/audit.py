"""Daily handheld stock reconciliation.

A session freezes the expected position at start, collects unique observations
from any number of (re)uploads, and classifies differences. Nothing here posts
stock: shortages go to review and a separate adjustment approval.
"""

import frappe
from frappe import _
from frappe.utils import cint, now_datetime, today

from rfid_retail import rfid
from rfid_retail.settings import get as setting, is_enabled, require_station
from rfid_retail.api.pos import _load

IN_STORE = (rfid.READY, rfid.PENDING, rfid.RETURNED, rfid.HOLD)


@frappe.whitelist(methods=["POST"])
def start(warehouse, device=None, scope="Full Store", areas=None):
	require_station("Stock Audit")
	doc = frappe.get_doc({"doctype": "RFID Audit Session", "audit_date": today(), "warehouse": warehouse,
		"device": device, "scope": scope, "operator": frappe.session.user})
	if areas:
		doc.areas = areas
	doc.insert(ignore_permissions=True)
	return begin(doc.name)


@frappe.whitelist(methods=["POST"])
def begin(session):
	"""Freeze the expected identities at this moment (cut-off)."""
	require_station("Stock Audit")
	doc = frappe.get_doc("RFID Audit Session", session, for_update=True)
	if doc.status != "Planned":
		frappe.throw(_("Session {0} already started.").format(doc.name))
	doc.cutoff = now_datetime()
	doc.expected = []
	units = frappe.get_all("Serial No",
		filters={"warehouse": doc.warehouse, "status": "Active", "custom_rfid_status": ["in", IN_STORE]},
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
	doc.expected_units = len(units)
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
			"note": old.note if old else None})

	by_serial = {}
	for e in doc.expected:
		by_serial.setdefault(e.serial_no, []).append(e)
	expected_tags = {e.tag for e in doc.expected if e.tag}
	verified = incomplete = unverified = 0
	for serial_no, rows in by_serial.items():
		item = rows[0].item_code
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
	for epc in sorted(observed - expected_tags):
		res = rfid.resolve_code(epc)
		if res.kind != "tag":
			unexpected += 1
			add("Unmapped", epc=epc, detail=_("Tag has no active garment"))
		elif res.tag_status == rfid.TAG_SOLD:
			sold_seen += 1
			add("Sold but Observed", res.serial_no, res.component, epc, res.item_code,
				_("Sold on {0}. Tag not detached, or garment still here?").format(
					frappe.db.get_value("Serial No", res.serial_no, "custom_sales_invoice")))
		else:
			unexpected += 1
			add("Unexpected", res.serial_no, res.component, epc, res.item_code,
				_("Expected in {0}").format(frappe.db.get_value("Serial No", res.serial_no, "warehouse")))

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
		"movements_after_cutoff": doc.movements_after_cutoff, "note": note}


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
