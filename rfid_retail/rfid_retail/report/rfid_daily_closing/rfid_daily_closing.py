"""One-screen daily closing pack for the owner.

Sales, collections, RFID stock position, today's count, open exceptions and
device health side by side. Nothing reports "all clear" just because a source
returned no data: a missing count or silent device is itself flagged.
"""

import frappe
from frappe import _
from frappe.utils import flt, getdate, now_datetime


def execute(filters=None):
	filters = frappe._dict(filters or {})
	date, company = getdate(filters.date), filters.company
	rows = []

	def header(label):
		rows.append({"section": label, "is_header": 1})

	def add(metric, value, status="", detail=""):
		rows.append({"metric": metric, "value": value, "status": status, "detail": detail})

	si = {"company": company, "posting_date": date, "docstatus": 1}

	header(_("Sales"))
	sales = frappe.get_all("Sales Invoice", {**si, "is_return": 0},
		["count(name) as n", "sum(grand_total) as amt", "sum(total_qty) as qty"])[0]
	rets = frappe.get_all("Sales Invoice", {**si, "is_return": 1}, ["count(name) as n", "sum(grand_total) as amt"])[0]
	add(_("Bills"), sales.n or 0)
	add(_("Gross sales"), flt(sales.amt), detail=_("{0} units").format(flt(sales.qty)))
	add(_("Returns"), flt(rets.amt), "Check" if rets.n else "", _("{0} credit notes").format(rets.n or 0))
	add(_("Net sales"), flt(sales.amt) + flt(rets.amt))
	for r in frappe.db.sql("""select coalesce(pos_profile, 'Desk') counter, owner, count(*) n, sum(grand_total) amt
		from `tabSales Invoice` where company=%s and posting_date=%s and docstatus=1
		group by counter, owner order by counter""", (company, date), as_dict=True):
		add(_("{0} · {1}").format(r.counter, r.owner), flt(r.amt), detail=_("{0} bills").format(r.n))
	cancelled = frappe.db.count("Sales Invoice", {"company": company, "posting_date": date, "docstatus": 2})
	if cancelled:
		add(_("Cancelled bills"), cancelled, "Check")

	header(_("Collections by mode"))
	for r in frappe.db.sql("""select p.mode_of_payment, sum(p.base_amount) amt from `tabSales Invoice Payment` p
		join `tabSales Invoice` s on s.name=p.parent
		where s.company=%s and s.posting_date=%s and s.docstatus=1 group by p.mode_of_payment""",
		(company, date), as_dict=True):
		add(r.mode_of_payment, flt(r.amt))
	change = frappe.db.sql("""select sum(change_amount) from `tabSales Invoice`
		where company=%s and posting_date=%s and docstatus=1""", (company, date))[0][0]
	if change:
		add(_("Change returned (cash)"), -flt(change))
	closings = frappe.get_all("POS Closing Entry", {"company": company, "posting_date": date, "docstatus": 1},
		["name", "pos_profile", "user"])
	add(_("Cashier closings submitted"), len(closings), "OK" if closings else "Check",
		", ".join(c.pos_profile or c.name for c in closings))

	header(_("RFID stock position (now)"))
	for status in ("Ready", "Pending Mapping", "Hold", "Returned - Hold"):
		n = frappe.db.count("Serial No", {"custom_rfid_status": status, "status": "Active"})
		add(_("Units: {0}").format(status), n, "Check" if n and status != "Ready" else "")
	away = frappe.db.count("Garment Component", {"status": "Out for Alteration"})
	add(_("Pieces out for alteration"), away, "Check" if away else "")
	pending_detach = frappe.db.count("RFID Tag", {"status": "Sold - Pending Detach"})
	add(_("Sold tags not yet detached"), pending_detach, "Check" if pending_detach else "OK")
	add(_("Free reusable tags"), frappe.db.count("RFID Tag", {"status": "Available"}))

	header(_("Today's stock count"))
	sessions = frappe.get_all("RFID Audit Session", {"audit_date": date},
		["name", "warehouse", "status", "expected_units", "verified_units", "incomplete_sets",
			"unverified_count", "unexpected_count", "sold_observed_count", "last_upload", "reviewed_by"])
	if not sessions:
		add(_("Count"), _("NOT DONE"), "Alert", _("No handheld count recorded for this date"))
	for s in sessions:
		clean = not (s.incomplete_sets or s.unverified_count or s.unexpected_count or s.sold_observed_count)
		add(s.name, s.status, "OK" if s.status == "Reviewed" and clean else ("Check" if s.status == "Reviewed" else "Alert"),
			_("{0}: {1}/{2} units verified, {3} incomplete sets, {4} unverified, {5} unexpected, last upload {6}, reviewer {7}").format(
				s.warehouse, s.verified_units or 0, s.expected_units or 0, s.incomplete_sets or 0,
				s.unverified_count or 0, s.unexpected_count or 0, s.last_upload or "-", s.reviewed_by or "-"))

	header(_("Open exceptions"))
	any_open = False
	for r in frappe.db.sql("""select event_type, severity, count(*) n, sum(occurrences) occ from `tabRFID Exception`
		where status != 'Resolved' group by event_type, severity
		order by field(severity, 'Critical', 'High', 'Medium', 'Low'), n desc""", as_dict=True):
		any_open = True
		add(r.event_type, r.n, "Alert" if r.severity in ("Critical", "High") else "Check",
			_("{0} · {1} occurrences").format(r.severity, int(r.occ or 0)))
	if not any_open:
		add(_("None open"), 0, "OK")

	header(_("Devices"))
	devices = frappe.get_all("RFID Device", {"is_active": 1}, ["name", "device_type", "health", "last_seen"])
	if not devices:
		add(_("Devices"), _("none registered"), "Check")
	for d in devices:
		add(f"{d.name} ({d.device_type})", d.health, "OK" if d.health == "Online" else "Alert",
			_("last heartbeat {0}").format(d.last_seen or _("never")))

	columns = [
		{"fieldname": "section", "label": _("Section"), "fieldtype": "Data", "width": 190},
		{"fieldname": "metric", "label": _("Metric"), "fieldtype": "Data", "width": 260},
		{"fieldname": "value", "label": _("Value"), "fieldtype": "Data", "width": 130},
		{"fieldname": "status", "label": _("Status"), "fieldtype": "Data", "width": 90},
		{"fieldname": "detail", "label": _("Detail"), "fieldtype": "Data", "width": 460},
	]
	for r in rows:
		if isinstance(r.get("value"), float):
			r["value"] = frappe.format_value(r["value"], {"fieldtype": "Currency"})
	message = _("Generated {0}. Figures are live; re-run for latest.").format(now_datetime().strftime("%d-%m-%Y %H:%M"))
	return columns, rows, message
