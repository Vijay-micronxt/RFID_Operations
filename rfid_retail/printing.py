"""Data for the Retail Tax Invoice and Retail Receipt print formats.

Tax per HSN is read from taxes[].item_wise_tax_detail, never from item-level
GST amount fields: on tax-inclusive (POS) invoices those are grossed up and
wrong. Seller details come from the invoice's company address, never typed in.
"""

import json

import frappe
from frappe.utils import cint, flt, format_time


def invoice_context(doc):
	"""Jinja: everything a retail invoice prints, already grouped and signed."""
	sign = -1 if cint(doc.get("is_return")) else 1
	items = [item_line(row, sign) for row in doc.items]
	return frappe._dict(
		title=title(doc),
		time=format_time(doc.posting_time, "HH:mm") if doc.get("posting_time") else "",
		company=company_block(doc),
		customer=customer_block(doc),
		lines=items,  # not "items": that is dict.items in Jinja
		total_qty=sum(i.qty for i in items),
		hsn=hsn_summary(doc, sign),
		taxes=[
			frappe._dict(description=t.description, rate=flt(t.rate), amount=flt(t.tax_amount_after_discount_amount) * sign)
			for t in doc.taxes
			if flt(t.tax_amount_after_discount_amount)
		],
		net_total=flt(doc.net_total) * sign,
		total=flt(doc.total) * sign,
		discount=flt(doc.discount_amount) * sign,
		rounding=flt(doc.rounding_adjustment) * sign,
		grand_total=flt(doc.rounded_total or doc.grand_total) * sign,
		in_words=doc.get("in_words") or "",
		payments=[
			frappe._dict(mode=p.mode_of_payment, amount=flt(p.amount) * sign)
			for p in (doc.get("payments") or [])
			if flt(p.amount)
		],
		paid=flt(doc.get("paid_amount")) * sign,
		change=flt(doc.get("change_amount")),
		outstanding=flt(doc.get("outstanding_amount")) * sign,
		loyalty_points=cint(doc.get("loyalty_points")),
		tax_inclusive=any(cint(t.included_in_print_rate) for t in doc.taxes),
	)


def title(doc):
	if cint(doc.get("is_return")):
		return "Credit Note"
	if doc.get("company_gstin") and flt(doc.total_taxes_and_charges):
		return "Tax Invoice"
	if doc.get("company_gstin"):
		return "Bill of Supply"
	return "Invoice"


def company_block(doc):
	company = frappe.get_cached_doc("Company", doc.company)
	address = doc.get("company_address_display")
	if not address and doc.get("company_address"):
		from frappe.contacts.doctype.address.address import get_address_display

		address = get_address_display(doc.company_address)
	if not address:
		from erpnext.setup.doctype.company.company import get_default_company_address

		name = get_default_company_address(doc.company)
		if name:
			from frappe.contacts.doctype.address.address import get_address_display

			address = get_address_display(name)
	phone = email = None
	if doc.get("company_address"):
		phone, email = frappe.db.get_value("Address", doc.company_address, ["phone", "email_id"]) or (None, None)
	gstin = doc.get("company_gstin") or company.get("gstin") or ""
	return frappe._dict(
		name=company.company_name,
		address=address or "",
		gstin=gstin,
		gstin_in_address=bool(gstin and gstin in (address or "")),  # India Compliance address template prints it
		phone=phone or company.get("phone_no") or "",
		email=email or company.get("email") or "",
		logo=company.get("company_logo"),
		pan=company.get("pan") or "",
	)


def customer_block(doc):
	walk_in = doc.customer == frappe.db.get_value("POS Profile", doc.get("pos_profile"), "customer") if doc.get(
		"pos_profile"
	) else False
	mobile = doc.get("contact_mobile") or frappe.db.get_value("Customer", doc.customer, "mobile_no")
	return frappe._dict(
		name=doc.customer_name,
		walk_in=walk_in,
		mobile=mobile or "",
		address=doc.get("address_display") or "",
		gstin=doc.get("billing_address_gstin") or "",
		place_of_supply=doc.get("place_of_supply") or "",
	)


def item_line(row, sign):
	from rfid_retail.overrides.sales_invoice import get_row_serials

	serials = get_row_serials(row) if row.get("serial_and_batch_bundle") or row.get("serial_no") else []
	details = frappe.get_all(
		"Item Variant Attribute",
		filters={"parent": row.item_code, "parenttype": "Item"},
		pluck="attribute_value",
		order_by="idx asc",
	)
	return frappe._dict(
		idx=row.idx,
		item_code=row.item_code,
		item_name=row.item_name,
		details=" / ".join(d for d in details if d and d.lower() not in (row.item_name or "").lower()),
		show_code=row.item_code != row.item_name,
		hsn=row.get("gst_hsn_code") or "",
		serials=serials,
		qty=flt(row.qty) * sign,
		uom=row.uom,
		mrp=flt(row.price_list_rate),
		rate=flt(row.rate),
		discount=flt(row.discount_amount) * flt(row.qty) * sign if flt(row.discount_amount) else 0,
		taxable=flt(row.net_amount) * sign,
		amount=flt(row.amount) * sign,
	)


def tax_kind(tax):
	kind = (tax.get("gst_tax_type") or "").lower()
	if kind:
		return kind.replace("_rcm", "").upper()
	desc = (tax.description or tax.account_head or "").upper()
	for k in ("CGST", "SGST", "UTGST", "IGST", "CESS"):
		if k in desc:
			return k
	return tax.description or tax.account_head


def detail_of(raw):
	"""item_wise_tax_detail value -> (rate, amount); old list and new dict shapes."""
	if isinstance(raw, dict):
		return flt(raw.get("tax_rate")), flt(raw.get("tax_amount"))
	if isinstance(raw, (list, tuple)) and raw:
		return flt(raw[0]), flt(raw[1] if len(raw) > 1 else 0)
	return flt(raw), 0


def hsn_summary(doc, sign):
	"""[{hsn, taxable, taxes: {CGST: {rate, amount}}, total_tax}], plus the tax kinds in column order."""
	hsn_of, taxable = {}, {}
	for row in doc.items:
		hsn = row.get("gst_hsn_code") or ""
		hsn_of.setdefault(row.item_code, hsn)
		taxable[hsn] = taxable.get(hsn, 0) + flt(row.net_amount)

	kinds, groups = [], {}
	for tax in doc.taxes:
		try:
			detail = json.loads(tax.item_wise_tax_detail or "{}")
		except ValueError:
			continue
		if not detail:
			continue
		kind = tax_kind(tax)
		if kind not in kinds:
			kinds.append(kind)
		for item_code, raw in detail.items():
			rate, amount = detail_of(raw)
			hsn = hsn_of.get(item_code, "")
			cell = groups.setdefault(hsn, {}).setdefault(kind, {"rate": rate, "amount": 0})
			cell["amount"] += amount
			cell["rate"] = cell["rate"] or rate

	rows = []
	for hsn, total in taxable.items():
		taxes = groups.get(hsn, {})
		rows.append(
			frappe._dict(
				hsn=hsn,
				taxable=total * sign,
				taxes={k: frappe._dict(rate=v["rate"], amount=v["amount"] * sign) for k, v in taxes.items()},
				total_tax=sum(v["amount"] for v in taxes.values()) * sign,
			)
		)
	return frappe._dict(kinds=kinds, rows=rows, total_tax=sum(r.total_tax for r in rows),
		total_taxable=sum(r.taxable for r in rows))
