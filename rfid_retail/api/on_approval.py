"""Goods on approval at the counter.

A customer (often family of the owner) takes clothes home without paying, keeps
some and brings the rest back. The counter gives them on an Approval Note: a
Delivery Note marked On Approval, made from the bill on screen and its RFID
basket, so the tags come off exactly as in a sale. Giving needs an approver
(RFID Approver by PIN, or login), unless the cashier is one.

Settling it later, in one go or piece by piece:
- kept pieces are billed on a Sales Invoice against the note, for whatever the
  customer pays; the rest of the price is given as a discount (nothing stays due);
- returned pieces come back on a return of the note, into stock without a tag,
  ready to be tagged again.

A Delivery Note is not in the sales reports; only the bill of the kept pieces is.
RFID Settings > Give goods on approval at the counter switches all of this on.
"""

import json

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import cint, flt, nowdate

from rfid_retail import approval, unread
from rfid_retail.settings import get as setting, has_station, is_enabled, require_station

GOODS = approval.GOODS
DEFAULT_SERIES = "APN-.YYYY.-"


def allowed():
	return bool(is_enabled() and cint(setting("goods_on_approval")))


def series():
	return (setting("approval_note_series") or DEFAULT_SERIES).strip()


def print_format():
	name = setting("approval_print_format") or "Approval Note"
	return name if frappe.db.exists("Print Format", name) else None


def approval_mode(user=None):
	"""self: the cashier gives on their own; pin / password / pin_or_password: an approver approves."""
	value = setting("goods_approval_needs_approver")
	if not (value is None or cint(value)) or has_station(approval.STATION, user):
		return "self"
	return {"Approver login and password": "password", "PIN or approver login": "pin_or_password"}.get(
		setting("unread_approval_mode") or "", "pin")


def config():
	"""Part of the counter config (api.pos.counter_config)."""
	if not allowed():
		return {}
	return {"goods_on_approval": 1, "goods_approval": approval_mode()}


def _require():
	require_station("Billing")
	if not allowed():
		frappe.throw(_("Giving goods on approval is switched off (RFID Settings)."))


def _rows(value):
	if isinstance(value, str):
		value = json.loads(value or "[]")
	return value or []


def ensure_series(name=None):
	"""The Delivery Note naming series must offer the Approval Note series."""
	name = (name or series()).strip()
	meta = frappe.get_meta("Delivery Note", cached=False)  # a cached meta may hold options never committed
	options = [o for o in (meta.get_field("naming_series").options or "").split("\n") if o]
	if name in options:
		return
	from frappe.custom.doctype.property_setter.property_setter import make_property_setter

	make_property_setter("Delivery Note", "naming_series", "options", "\n".join(options + [name]), "Text",
		validate_fields_for_doctype=False)
	frappe.clear_cache(doctype="Delivery Note")


# ---------------------------------------------------------------- giving


def _basket(name):
	from rfid_retail.api.pos import _basket as load, _check_live

	doc = load(name, for_update=True)
	_check_live(doc)
	return doc


def _customer(customer, pos_profile):
	if not customer or not frappe.db.exists("Customer", customer):
		frappe.throw(_("Choose the customer who takes the goods."), title=_("Customer"))
	if customer == frappe.db.get_value("POS Profile", pos_profile, "customer"):
		frappe.throw(_("Goods on approval need the real customer, not the counter's walk-in customer."),
			title=_("Customer"))
	return customer


@frappe.whitelist(methods=["POST"])
@rate_limit(limit=20, seconds=600)
def request_approval(basket, customer, qty, amount, note=None):
	"""Ask the approvers: each gets a one-time PIN (notification, and WhatsApp when on).
	When the cashier may give on their own, says so instead."""
	_require()
	doc = _basket(basket)
	_customer(customer, doc.pos_profile)
	mode = approval_mode()
	if mode == "self":
		return {"request": None, "status": "Approved", "approved_by": frappe.session.user}
	if mode == "password":
		frappe.throw(_("Approval by PIN is switched off. Ask the approver to sign in."))
	req = approval.create_request(doc, None, cint(qty), GOODS, (note or "").strip() or None,
		frappe.db.get_value("POS Profile", doc.pos_profile, "warehouse"),
		request_type=GOODS, customer=customer, amount=flt(amount))
	return {"request": req.name, "status": req.status, "expires_at": req.expires_at, "sent_to": len(req.approvers),
		"whatsapp": cint(setting("approval_whatsapp"))}


@frappe.whitelist(methods=["POST"])
@rate_limit(limit=30, seconds=600)
def confirm_pin(request, pin):
	_require()
	req, error = approval.check_pin(request, pin)
	return {**approval.status(req.name), "error": error}


@frappe.whitelist()
def approval_status(request):
	_require()
	if frappe.db.get_value("RFID Approval Request", request, "requested_by") != frappe.session.user \
		and not has_station("Manager Override"):
		frappe.throw(_("Not your request."), frappe.PermissionError)
	return approval.status(request)


def _approver(basket, customer, request=None, manager=None, password=None):
	mode = approval_mode()
	if mode == "self":
		return frappe.session.user, None
	if request:
		req = frappe.get_doc("RFID Approval Request", request, for_update=True)
		if req.get("request_type") != GOODS or req.basket != basket.name or req.customer != customer:
			frappe.throw(_("Approval {0} is for another bill.").format(req.name))
		if req.status != "Approved":
			frappe.throw(_("Approval {0} is {1}.").format(req.name, _(req.status)))
		if req.get("delivery_note"):
			frappe.throw(_("Approval {0} was already used on {1}.").format(req.name, req.delivery_note))
		return req.approved_by, req
	if mode == "pin":
		frappe.throw(_("Ask an approver for a PIN."), title=_("Approval"))
	return unread.check_approver(manager, password, mode="password"), None


@frappe.whitelist(methods=["POST"])
@rate_limit(limit=30, seconds=600)
def give(basket, customer, items, request=None, manager=None, password=None, note=None):
	"""Make and submit the Approval Note for the bill on screen. items: the cart rows, as POS Next
	sends them for an invoice. Returns the note's name and print format."""
	_require()
	doc = _basket(basket)
	customer = _customer(customer, doc.pos_profile)
	rows = [r for r in _rows(items) if flt(r.get("qty")) > 0]
	if not rows:
		frappe.throw(_("Add the pieces the customer takes."))
	approver, req = _approver(doc, customer, request, manager, password)
	profile = frappe.get_cached_doc("POS Profile", doc.pos_profile)
	ensure_series()

	dn = frappe.new_doc("Delivery Note")
	dn.update({
		"naming_series": series(),
		"company": profile.company,
		"customer": customer,
		"posting_date": nowdate(),
		"set_warehouse": profile.warehouse,
		"selling_price_list": profile.selling_price_list,
		"currency": profile.currency,
		"ignore_pricing_rule": 1,
		"taxes_and_charges": profile.taxes_and_charges,
		"custom_is_approval": 1,
		"custom_rfid_basket": doc.name,
		"custom_approval_request": req.name if req else None,
		"custom_approved_by": approver,
		"custom_pos_profile": profile.name,
		"instructions": (note or "").strip() or None,
	})
	if profile.get("cost_center"):
		dn.cost_center = profile.cost_center
	for r in rows:
		dn.append("items", {
			"item_code": r.get("item_code"),
			"qty": flt(r.get("qty")),
			"uom": r.get("uom"),
			"conversion_factor": flt(r.get("conversion_factor")) or 1,
			"rate": flt(r.get("rate")),
			"price_list_rate": flt(r.get("price_list_rate")) or flt(r.get("rate")),
			"discount_percentage": flt(r.get("discount_percentage")),
			"discount_amount": flt(r.get("discount_amount")),
			"warehouse": r.get("warehouse") or profile.warehouse,
			"serial_no": r.get("serial_no"),
			"batch_no": r.get("batch_no"),
			"use_serial_batch_fields": 1 if (r.get("serial_no") or r.get("batch_no")) else 0,
		})
	dn.set_missing_values()
	if not dn.get("taxes") and dn.taxes_and_charges:
		dn.append_taxes_from_master()
	dn.calculate_taxes_and_totals()
	dn.flags.ignore_permissions = True
	dn.insert()
	dn.submit()
	if req:
		req.db_set("delivery_note", dn.name)
	dn.add_comment("Info", _("Given on approval to {0}, approved by {1}{2}.").format(
		dn.customer_name or customer, frappe.utils.get_fullname(approver), f" ({req.name})" if req else ""))
	return {"name": dn.name, "grand_total": dn.grand_total, "print_format": print_format(),
		"approved_by": approver}


# ---------------------------------------------------------------- settling


def _pending(dn):
	from erpnext.stock.doctype.delivery_note.delivery_note import get_invoiced_qty_map, get_returned_qty_map

	invoiced, returned = get_invoiced_qty_map(dn.name), get_returned_qty_map(dn.name)
	return {r.name: max(flt(r.qty) - flt(invoiced.get(r.name)) - flt(returned.get(r.name)), 0) for r in dn.items}


def _note(name):
	dn = frappe.get_doc("Delivery Note", name)
	if not cint(dn.get("custom_is_approval")) or cint(dn.is_return):
		frappe.throw(_("{0} is not an Approval Note.").format(name))
	if dn.docstatus != 1 or dn.status == "Closed":
		frappe.throw(_("Approval Note {0} is not open.").format(name))
	return dn


@frappe.whitelist()
def open_notes(pos_profile=None, customer=None, search=None):
	"""Approval Notes with pieces still out with the customer, newest first."""
	_require()
	filters = {"custom_is_approval": 1, "is_return": 0, "docstatus": 1, "status": ["not in", ("Closed", "Completed")]}
	if customer:
		filters["customer"] = customer
	if pos_profile:
		company = frappe.db.get_value("POS Profile", pos_profile, "company")
		if company:
			filters["company"] = company
	or_filters = None
	if search:
		like = f"%{search.strip()}%"
		or_filters = {"name": ["like", like], "customer": ["like", like], "customer_name": ["like", like],
			"contact_mobile": ["like", like]}
	out = []
	for row in frappe.get_all("Delivery Note", filters, ["name", "customer", "customer_name", "posting_date",
		"grand_total", "total_qty", "custom_approved_by"], or_filters=or_filters, order_by="creation desc", limit=100):
		dn = frappe.get_doc("Delivery Note", row.name)
		pending = _pending(dn)
		left = sum(pending.values())
		if left <= 0:
			continue
		row.pending_qty = left
		row.pending_value = flt(sum(flt(r.amount) / flt(r.qty) * pending[r.name] for r in dn.items if flt(r.qty))
			* _total_ratio(dn), 2)
		row.mobile = frappe.db.get_value("Customer", row.customer, "mobile_no")
		out.append(row)
	return out


@frappe.whitelist()
def note_detail(delivery_note):
	_require()
	dn = _note(delivery_note)
	pending = _pending(dn)
	return {
		"name": dn.name,
		"customer": dn.customer,
		"customer_name": dn.customer_name,
		"posting_date": dn.posting_date,
		"currency": dn.currency,
		"approved_by": dn.get("custom_approved_by"),
		"print_format": print_format(),
		"rows": [{
			"dn_detail": r.name,
			"item_code": r.item_code,
			"item_name": r.item_name,
			"uom": r.uom,
			"qty": r.qty,
			"pending": pending[r.name],
			"rate": r.rate,
			# what the customer pays per piece: the row's share of the note's total, taxes and discount in
			"price": flt(flt(r.amount) / flt(r.qty) * _total_ratio(dn), 2) if flt(r.qty) else 0,
			"serial_no": r.serial_no,
		} for r in dn.items],
	}


def _total_ratio(dn):
	return flt(dn.grand_total) / flt(dn.total) if flt(dn.total) else 1


@frappe.whitelist(methods=["POST"])
@rate_limit(limit=30, seconds=600)
def settle(delivery_note, rows, amount_received=0, mode_of_payment=None, pos_profile=None, pos_opening_shift=None):
	"""rows: [{dn_detail, keep, back}]. Back pieces return into stock (without tag); kept pieces are billed
	for amount_received, the rest of their price given as discount. Either part may be empty; pieces
	not mentioned stay out on the note."""
	_require()
	dn = _note(delivery_note)
	pending = _pending(dn)
	by_row = {r.name: r for r in dn.items}
	keep, back = {}, {}
	for r in _rows(rows):
		name = r.get("dn_detail")
		if name not in by_row:
			frappe.throw(_("Row {0} is not on {1}.").format(name, dn.name))
		k, b = flt(r.get("keep")), flt(r.get("back"))
		if k < 0 or b < 0:
			frappe.throw(_("Quantities cannot be negative."))
		if k + b > pending[name] + 1e-9:
			frappe.throw(_("{0}: only {1} still out with the customer.").format(by_row[name].item_name, pending[name]))
		if k:
			keep[name] = k
		if b:
			back[name] = b
	if not keep and not back:
		frappe.throw(_("Mark which pieces the customer keeps and which come back."))
	received = flt(amount_received, 2)
	if received < 0:
		frappe.throw(_("The amount received cannot be negative."))
	if received and not keep:
		frappe.throw(_("Money is taken only for pieces the customer keeps."))

	returned = _make_return(dn, back) if back else None
	invoice = _make_invoice(dn, keep, received, mode_of_payment, pos_profile or dn.get("custom_pos_profile"),
		pos_opening_shift) if keep else None

	dn.reload()
	if sum(_pending(dn).values()) <= 0 and dn.status != "Completed":
		dn.update_status("Closed")
	return {
		"delivery_note": dn.name,
		"status": frappe.db.get_value("Delivery Note", dn.name, "status"),
		"sales_invoice": invoice and invoice.name,
		"grand_total": invoice and invoice.grand_total,
		"discount": invoice and invoice.discount_amount,
		"return_note": returned and returned.name,
		"returned_qty": sum(back.values()),
		"item_codes": sorted({by_row[n].item_code for n in back}),
	}


def _serials_for(row, qty):
	from erpnext.stock.doctype.serial_no.serial_no import get_serial_nos

	serials = get_serial_nos(row.serial_no or "")
	if not serials:
		return None
	if cint(qty) != len(serials):
		frappe.throw(_("{0}: a row with serial numbers is settled whole ({1} pieces).").format(row.item_name,
			len(serials)))
	return "\n".join(serials)


def _make_return(dn, back):
	from erpnext.stock.doctype.delivery_note.delivery_note import make_sales_return

	ret = make_sales_return(dn.name)
	ret.items = [r for r in ret.items if r.dn_detail in back]
	original = {r.name: r for r in dn.items}
	for r in ret.items:
		qty = back[r.dn_detail]
		r.qty = -qty
		r.stock_qty = -qty * flt(r.conversion_factor or 1)
		if original[r.dn_detail].serial_no:
			r.serial_no = _serials_for(original[r.dn_detail], qty)
			r.use_serial_batch_fields = 1
			r.serial_and_batch_bundle = None
	ret.custom_is_approval = 1
	ret.naming_series = series()
	ret.custom_rfid_basket = None
	ret.custom_approval_request = None
	ret.calculate_taxes_and_totals()
	ret.flags.ignore_permissions = True
	ret.insert()
	ret.submit()
	# nothing is billed for pieces that came back: keep the return out of "to bill" lists
	ret.update_status("Closed")
	return ret


def _make_invoice(dn, keep, received, mode_of_payment, pos_profile, pos_opening_shift):
	from erpnext.stock.doctype.delivery_note.delivery_note import make_sales_invoice

	inv = make_sales_invoice(dn.name)
	inv.items = [r for r in inv.items if r.dn_detail in keep]
	if not inv.items:
		frappe.throw(_("These pieces are already billed or returned."))
	original = {r.name: r for r in dn.items}
	for r in inv.items:
		r.qty = keep[r.dn_detail]
		if original[r.dn_detail].serial_no:
			r.serial_no = _serials_for(original[r.dn_detail], r.qty)
	inv.update_stock = 0
	inv.disable_rounded_total = 1
	inv.custom_rfid_basket = None
	if pos_profile:
		inv.is_pos = 1
		inv.pos_profile = pos_profile
		if pos_opening_shift and inv.meta.has_field("posa_pos_opening_shift"):
			inv.posa_pos_opening_shift = pos_opening_shift
	inv.remarks = _("Pieces kept from Approval Note {0}.").format(dn.name)
	inv.calculate_taxes_and_totals()
	full = flt(inv.grand_total, 2)
	if received > full + 0.005:
		frappe.throw(_("Received {0} is more than the price of the kept pieces ({1}).").format(
			frappe.utils.fmt_money(received, currency=inv.currency), frappe.utils.fmt_money(full, currency=inv.currency)))
	if received < full:
		inv.apply_discount_on = "Grand Total"
		inv.discount_amount = flt(full - received, 2)
		inv.additional_discount_percentage = 0
		inv.calculate_taxes_and_totals()
		gap = flt(inv.grand_total - received, 2)
		if gap:  # rounding of the discount over the rows
			inv.discount_amount = flt(inv.discount_amount + gap, 2)
			inv.calculate_taxes_and_totals()
	inv.set("payments", [])
	if inv.is_pos:
		modes = frappe.get_all("POS Payment Method", {"parent": pos_profile}, ["mode_of_payment", "default"],
			order_by="idx")
		mode = mode_of_payment or next((m.mode_of_payment for m in modes if cint(m.default)), None) or (
			modes[0].mode_of_payment if modes else None)
		if received and not mode:
			frappe.throw(_("Choose how the customer paid."))
		if mode:
			inv.append("payments", {"mode_of_payment": mode, "amount": received, "default": 1})
	inv.flags.ignore_permissions = True
	inv.insert()
	if not inv.is_pos and received:
		frappe.throw(_("Take the payment at a counter (POS Profile missing)."))
	inv.submit()
	_mark_kept_serials(inv)
	return inv


def _mark_kept_serials(inv):
	from erpnext.stock.doctype.serial_no.serial_no import get_serial_nos

	for r in inv.items:
		for serial_no in get_serial_nos(r.serial_no or ""):
			frappe.db.set_value("Serial No", serial_no, "custom_sales_invoice", inv.name, update_modified=False)
