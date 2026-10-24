"""Counter (tray reader) billing API.

Flow: open_basket -> scan (repeat, any order, duplicates ignored) -> checkout.
Any frontend can drive it: the bundled /rfid-pos page, a custom app, or a
reader bridge posting tray reads directly.
"""

import json

import frappe
from frappe import _
from frappe.utils import cint, flt, now_datetime

from rfid_retail import rfid
from rfid_retail.settings import get as setting, has_station, require_station

def _load(value):
	if isinstance(value, str):
		value = value.strip()
		if value.startswith("["):
			return json.loads(value)
		return [v for v in value.replace(",", "\n").split() if v]
	return value or []


def _basket(name, for_update=False):
	basket = frappe.get_doc("RFID Basket", name, for_update=for_update)
	if basket.cashier != frappe.session.user and not has_station("Manager Override"):
		frappe.throw(_("Basket {0} belongs to {1}.").format(name, basket.cashier), frappe.PermissionError)
	return basket


@frappe.whitelist()
def counter_config(pos_profile):
	"""What a POS frontend needs to switch into RFID mode for this counter. Never throws."""
	from rfid_retail.settings import is_enabled

	off = {"enabled": False}
	if not is_enabled() or not cint(frappe.db.get_value("POS Profile", pos_profile, "custom_rfid_counter")):
		return off
	if not has_station("Billing"):
		return off
	return {
		"enabled": True,
		"require_scan": cint(setting("require_scan_for_sale")),
		"allow_label_fallback": cint(setting("allow_label_fallback")),
		"block_untracked_items": cint(setting("block_untracked_items")),
		"device": frappe.db.get_value("RFID Device",
			{"pos_profile": pos_profile, "device_type": "Tray Reader", "is_active": 1}, "name"),
	}


@frappe.whitelist()
def manual_add_check(item_code, pos_profile):
	"""Picked by hand at a counter: how many units of the item in the counter's warehouse carry a tag
	and how many do not. Both kinds in stock: the cashier is warned (a tagged piece should be read on
	the tray). Only tagged pieces and scan required: picking by hand is blocked. Never throws."""
	from rfid_retail.settings import is_enabled

	out = {"warn": False, "tagged": 0, "untagged": 0}
	if not is_enabled() or not item_code or not rfid.is_rfid_item(item_code):
		return out
	warehouse = frappe.db.get_value("POS Profile", pos_profile, "warehouse")
	if not warehouse:
		return out
	if rfid.has_serial(item_code):
		serials = frappe.get_all("Serial No", {"item_code": item_code, "warehouse": warehouse, "status": "Active"},
			pluck="name")
		tagged = sum(1 for s in serials if rfid.active_assignment_for(serial_no=s))
		untagged = len(serials) - tagged
	else:
		actual = cint(flt(frappe.db.get_value("Bin", {"item_code": item_code, "warehouse": warehouse}, "actual_qty")))
		tagged = min(rfid.count_item_tags(item_code, warehouse), max(actual, 0))
		untagged = max(actual - tagged, 0)
	# Every piece in stock carries a tag: one picked by hand is a tagged piece read nowhere.
	block = bool(tagged and not untagged and cint(setting("require_scan_for_sale")))
	out.update(tagged=tagged, untagged=untagged, warn=bool(tagged and untagged), block=block, warehouse=warehouse)
	return out


@frappe.whitelist()
def get_counters():
	require_station("Billing")
	profiles = frappe.get_all("POS Profile", filters={"disabled": 0}, fields=["name", "warehouse", "company"])
	user = frappe.session.user
	allowed = []
	for p in profiles:
		users = frappe.get_all("POS Profile User", {"parent": p.name}, pluck="user")
		if not users or user in users or user == "Administrator":
			p.devices = frappe.get_all("RFID Device", {"pos_profile": p.name, "is_active": 1}, pluck="name")
			p.payments = frappe.get_all("POS Payment Method", {"parent": p.name},
				["mode_of_payment", "default"], order_by="idx asc")
			allowed.append(p)
	return allowed


@frappe.whitelist(methods=["POST"])
def open_basket(pos_profile, device=None):
	"""Return the cashier's open basket on this counter, creating one if needed."""
	require_station("Billing")
	name = frappe.db.get_value("RFID Basket",
		{"pos_profile": pos_profile, "cashier": frappe.session.user, "status": "Open"}, "name")
	if not name:
		name = frappe.get_doc({"doctype": "RFID Basket", "pos_profile": pos_profile, "device": device,
			"cashier": frappe.session.user, "status": "Open"}).insert(ignore_permissions=True).name
	return basket_view(name)


@frappe.whitelist(methods=["POST"])
def scan(basket, codes, source="RFID"):
	"""Add tray reads / scanned labels. Re-reading a tag never adds quantity."""
	require_station("Billing")
	doc = _basket(basket, for_update=True)
	if doc.status != "Open":
		frappe.throw(_("Basket {0} is {1}.").format(doc.name, doc.status))
	have = {r.epc for r in doc.reads}
	events = []
	for code in _load(codes):
		code = rfid.normalize(code)
		if not code or code in have:
			continue
		res = rfid.resolve_code(code)
		problem = _reject_reason(doc, res)
		if problem:
			event_type, severity, msg = problem
			rfid.log_exception(event_type, msg, severity=severity, epc=code if res.kind != "serial" else None,
				serial_no=res.serial_no, device=doc.device, pos_profile=doc.pos_profile,
				reference_doctype="RFID Basket", reference_name=doc.name)
			rfid.log_event(code, "Rejected", reason=msg, item_code=res.item_code, serial_no=res.serial_no,
				reference_doctype="RFID Basket", reference_name=doc.name, device=doc.device)
			events.append({"code": code, "level": "error", "message": msg})
			continue
		if res.kind == "serial" and cint(frappe.db.get_value("Serial No", res.serial_no, "custom_is_garment_set")):
			events.append({"code": code, "level": "warning",
				"message": _("{0} is a set label. Scan each piece (tag or piece label) instead.").format(code)})
			continue
		if res.kind in ("component", "serial") and not cint(setting("allow_label_fallback")):
			events.append({"code": code, "level": "error",
				"message": _("{0} is a printed label. Label fallback is switched off; read the RFID tag.").format(code)})
			continue
		if res.kind in ("component", "serial"):
			rfid.log_exception("Barcode Fallback",
				_("{0} entered by label instead of RFID on {1}.").format(code, doc.pos_profile),
				severity="Low", serial_no=res.serial_no, component=res.component, pos_profile=doc.pos_profile,
				reference_doctype="RFID Basket", reference_name=doc.name)
		if res.serial_no:
			_claim(doc, res.serial_no)
		else:
			_claim_tag(doc, code)
		doc.append("reads", {"epc": code, "source": "RFID" if res.kind == "tag" else ("Manual" if source == "Manual" else "Barcode"),
			"serial_no": res.serial_no, "component": res.component, "item_code": res.item_code,
			"read_at": now_datetime(), "read_by": frappe.session.user})
		have.add(code)
		if res.kind == "tag":
			frappe.db.set_value("RFID Tag", code, {"last_read_on": now_datetime(), "last_read_device": doc.device},
				update_modified=False)
	doc.save(ignore_permissions=True)
	view = basket_view(doc.name)
	view["events"] = events
	return view


def _reject_reason(basket, res):
	if res.tag_status in rfid.UNUSABLE:
		return "Retired Tag Read", "Medium", _("Tag {0} is marked {1}. Put a new tag on this garment.").format(
			res.code, res.tag_status)
	if res.kind == "unknown":
		return "Unmapped Tag", "Medium", _("{0} is not a known tag or label.").format(res.code)
	if res.kind == "unmapped":
		if res.tag_status == rfid.TAG_RESERVED:
			return "Unmapped Tag", "Medium", _("Tag {0} is on a purchase that is not submitted yet.").format(res.code)
		return "Unmapped Tag", "Medium", _("Tag {0} is not on any garment. Tag the garment first.").format(res.code)
	counter_wh = frappe.db.get_value("POS Profile", basket.pos_profile, "warehouse")
	if not res.serial_no:
		# One unit of a non-serial item
		if res.tag_status == rfid.TAG_SOLD:
			return "Sold Tag Read", "High", _("Tag {0} ({1}) belongs to a garment that is already sold.").format(
				res.code, res.item_code)
		if res.tag_status != rfid.TAG_ASSIGNED:
			return "Manual Override", "Medium", _("Tag {0} is {1}.").format(res.code, res.tag_status)
		if res.warehouse != counter_wh:
			return "Manual Override", "Medium", _("{0} ({1}) is recorded in {2}, not this counter's warehouse.").format(
				res.code, res.item_code, res.warehouse or _("no warehouse"))
		if res.basket and res.basket != basket.name and frappe.db.get_value("RFID Basket", res.basket, "status") == "Open":
			counter = frappe.db.get_value("RFID Basket", res.basket, "pos_profile")
			return "Counter Conflict", "High", _("{0} is already on {1} ({2}).").format(res.code, counter, res.basket)
		return None
	sn = rfid.serial_info(res.serial_no)
	if sn.custom_rfid_status == rfid.SOLD or res.tag_status == rfid.TAG_SOLD:
		return "Sold Tag Read", "High", _("{0} ({1}) is already sold on {2}.").format(
			res.code, res.serial_no, sn.custom_sales_invoice or "-")
	if sn.custom_rfid_status != rfid.READY:
		return "Manual Override", "Medium", _("{0} is '{1}', not released for sale.").format(
			res.serial_no, sn.custom_rfid_status)
	if sn.warehouse != counter_wh:
		return "Manual Override", "Medium", _("{0} is in {1}, not this counter's warehouse.").format(
			res.serial_no, sn.warehouse or _("no warehouse"))
	other = sn.custom_rfid_basket
	if other and other != basket.name and frappe.db.get_value("RFID Basket", other, "status") == "Open":
		counter = frappe.db.get_value("RFID Basket", other, "pos_profile")
		return "Counter Conflict", "High", _("{0} is already on {1} ({2}).").format(res.serial_no, counter, other)
	return None


def _claim(basket, serial_no):
	"""Row-lock the unit so two counters cannot both sell it."""
	current = frappe.db.sql("select custom_rfid_basket from `tabSerial No` where name=%s for update",
		serial_no)[0][0]
	if current != basket.name:
		frappe.db.set_value("Serial No", serial_no, "custom_rfid_basket", basket.name, update_modified=False)


def _claim_tag(basket, epc):
	"""Same for a tag of a non-serial item: the tag row is the unit."""
	current = frappe.db.sql("select basket from `tabRFID Tag` where name=%s for update", epc)[0][0]
	if current != basket.name:
		frappe.db.set_value("RFID Tag", epc, "basket", basket.name, update_modified=False)


def _unclaim(basket, reads):
	for r in reads:
		if r.serial_no:
			if frappe.db.get_value("Serial No", r.serial_no, "custom_rfid_basket") == basket.name:
				frappe.db.set_value("Serial No", r.serial_no, "custom_rfid_basket", None, update_modified=False)
		elif frappe.db.get_value("RFID Tag", r.epc, "basket") == basket.name:
			frappe.db.set_value("RFID Tag", r.epc, "basket", None, update_modified=False)


def _line_key(read):
	"""A unit on the tray: its serial, or for a non-serial item the tag itself."""
	return read.serial_no or read.epc


@frappe.whitelist(methods=["POST"])
def remove_unit(basket, serial_no=None, key=None):
	"""Drop a unit (all its reads) from the basket. key = serial, or the tag of a non-serial unit."""
	require_station("Billing")
	key = key or serial_no
	doc = _basket(basket, for_update=True)
	if doc.status != "Open":
		frappe.throw(_("Basket {0} is {1}.").format(doc.name, doc.status))
	gone = [r for r in doc.reads if _line_key(r) == key]
	doc.reads = [r for r in doc.reads if _line_key(r) != key]
	doc.save(ignore_permissions=True)
	_unclaim(doc, gone)
	return basket_view(doc.name)


@frappe.whitelist(methods=["POST"])
def cancel_basket(basket):
	require_station("Billing")
	doc = _basket(basket, for_update=True)
	if doc.status != "Open":
		return doc.status
	_unclaim(doc, doc.reads)
	doc.db_set("status", "Cancelled")
	return "Cancelled"


@frappe.whitelist()
def basket_view(basket):
	require_station("Billing")
	doc = _basket(basket)
	units = {}
	for r in doc.reads:
		if r.serial_no:
			units.setdefault(r.serial_no, {"serial_no": r.serial_no, "seen": set()})["seen"].add(r.component or r.serial_no)
		elif r.item_code:
			units[r.epc] = {"serial_no": None, "epc": r.epc, "item_code": r.item_code}
	lines = []
	for key, unit in units.items():
		if not unit["serial_no"]:
			lines.append({"key": key, "serial_no": None, "epc": key, "item_code": unit["item_code"],
				"item_name": frappe.get_cached_value("Item", unit["item_code"], "item_name"),
				"is_set": 0, "pieces": [], "complete": True})
			continue
		sn = rfid.serial_info(key)
		line = {"key": key, "serial_no": key, "item_code": sn.item_code, "item_name": sn.item_name,
			"is_set": cint(sn.custom_is_garment_set), "pieces": [], "complete": True}
		if line["is_set"]:
			for c in rfid.get_components(key):
				ok = c.name in unit["seen"]
				line["pieces"].append({"component": c.name, "role": c.role, "verified": ok})
				line["complete"] = line["complete"] and ok
		lines.append(line)
	totals = _preview_totals(doc, [ln for ln in lines if ln["complete"]]) if lines else {}
	rates = totals.pop("rates", {})
	for ln in lines:
		ln["rate"] = rates.get(ln["serial_no"] or ln["item_code"], 0)
	return {"basket": doc.name, "status": doc.status, "pos_profile": doc.pos_profile, "customer": doc.customer,
		"sales_invoice": doc.sales_invoice, "lines": lines, "read_count": len(doc.reads),
		"blocked": any(not ln["complete"] for ln in lines), "totals": totals}


def _build_invoice(basket, lines, customer=None):
	profile = frappe.get_cached_doc("POS Profile", basket.pos_profile)
	si = frappe.new_doc("Sales Invoice")
	si.update({"is_pos": 1, "pos_profile": profile.name, "company": profile.company, "update_stock": 1,
		"customer": customer or basket.customer or profile.customer, "set_warehouse": profile.warehouse,
		"custom_rfid_basket": basket.name})
	item_qty = {}
	for ln in lines:
		if ln["serial_no"]:
			si.append("items", {"item_code": ln["item_code"], "qty": 1, "warehouse": profile.warehouse,
				"serial_no": ln["serial_no"], "use_serial_batch_fields": 1})
		else:
			item_qty[ln["item_code"]] = item_qty.get(ln["item_code"], 0) + 1
	for item_code, qty in item_qty.items():
		si.append("items", {"item_code": item_code, "qty": qty, "warehouse": profile.warehouse})
	si.set_missing_values()
	si.calculate_taxes_and_totals()
	return si


def _preview_totals(basket, lines):
	if not lines:
		return {"net_total": 0, "total_taxes": 0, "grand_total": 0, "rounded_total": 0, "rates": {}}
	si = _build_invoice(basket, lines)
	return {"net_total": si.net_total, "total_taxes": si.total_taxes_and_charges, "grand_total": si.grand_total,
		"rounded_total": si.rounded_total or si.grand_total, "currency": si.currency,
		"rates": {row.serial_no or row.item_code: row.rate for row in si.items}}


@frappe.whitelist(methods=["POST"])
def checkout(basket, payments, customer=None, redeem_points=0):
	"""Create and submit the POS Sales Invoice. Safe to retry: a billed basket returns its invoice."""
	require_station("Billing")
	doc = _basket(basket, for_update=True)
	if doc.status == "Billed":
		return {"sales_invoice": doc.sales_invoice, "already_billed": True}
	if doc.status != "Open":
		frappe.throw(_("Basket {0} is {1}.").format(doc.name, doc.status))
	view = basket_view(doc.name)
	if not view["lines"]:
		frappe.throw(_("Basket is empty."))
	if view["blocked"]:
		incomplete = [ln["key"] for ln in view["lines"] if not ln["complete"]]
		frappe.throw(_("Incomplete sets on the tray: {0}. Read the missing pieces or remove the set.").format(
			", ".join(incomplete)))
	if customer:
		doc.db_set("customer", customer)

	si = _build_invoice(doc, view["lines"], customer)
	if cint(redeem_points):
		si.redeem_loyalty_points = 1
		si.loyalty_points = cint(redeem_points)
		si.set_missing_values()
		si.calculate_taxes_and_totals()

	payments = _load(payments) if not isinstance(payments, list) else payments
	given = {p["mode_of_payment"]: flt(p.get("amount")) for p in payments if flt(p.get("amount"))}
	for mode in given:
		if mode not in [row.mode_of_payment for row in si.payments]:
			si.append("payments", {"mode_of_payment": mode})
	for row in si.payments:
		row.amount = given.get(row.mode_of_payment, 0)
	si.payments = [row for row in si.payments if row.amount or row.default]
	due = flt(si.rounded_total or si.grand_total) - flt(si.loyalty_amount)
	if flt(sum(given.values())) + 0.001 < due:
		frappe.throw(_("Payment {0} is less than bill amount {1}.").format(sum(given.values()), due))
	si.insert()
	si.submit()
	return {"sales_invoice": si.name, "grand_total": si.grand_total, "rounded_total": si.rounded_total,
		"change_amount": si.change_amount, "paid_amount": si.paid_amount}


@frappe.whitelist()
def find_customer(mobile):
	require_station("Billing")
	mobile = (mobile or "").strip()
	if len(mobile) < 6:
		return []
	return frappe.get_all("Customer", or_filters={"mobile_no": ["like", f"%{mobile}%"], "name": ["like", f"%{mobile}%"]},
		fields=["name", "customer_name", "mobile_no", "loyalty_program"], limit=10)


@frappe.whitelist(methods=["POST"])
def create_customer(customer_name, mobile):
	require_station("Billing")
	existing = frappe.db.get_value("Customer", {"mobile_no": mobile}, "name")
	if existing:
		return existing
	settings = frappe.get_cached_doc("Selling Settings")
	cust = frappe.get_doc({"doctype": "Customer", "customer_name": customer_name, "mobile_no": mobile,
		"customer_type": "Individual", "customer_group": settings.customer_group or "Individual",
		"territory": settings.territory or "India"})
	cust.insert(ignore_permissions=True)
	return cust.name


@frappe.whitelist()
def loyalty_balance(customer, company=None):
	require_station("Billing")
	from erpnext.accounts.doctype.loyalty_program.loyalty_program import get_loyalty_program_details_with_points

	program = frappe.db.get_value("Customer", customer, "loyalty_program")
	if not program:
		return {"program": None, "points": 0}
	company = company or frappe.defaults.get_user_default("Company")
	details = get_loyalty_program_details_with_points(customer, program, company=company)
	return {"program": program, "points": details.get("loyalty_points", 0),
		"conversion_factor": details.get("conversion_factor", 0)}
