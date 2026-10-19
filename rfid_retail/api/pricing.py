import frappe
from frappe import _
from frappe.utils import flt

from rfid_retail.pricing import offer_price, offers_on, plain_price, selling_price_list


@frappe.whitelist()
def selling_price(item_code):
	"""MRP, Offer % and Rate of the item on the selling price list."""
	frappe.has_permission("Item", "read", item_code, throw=True)
	if not offers_on():
		return None
	price = plain_price(item_code, selling_price_list())
	if not price:
		return None
	return {
		"name": price.name,
		"item_code": item_code,
		"price_list": selling_price_list(),
		"mrp": flt(price.get("custom_mrp")) or flt(price.price_list_rate),
		"offer": flt(price.get("custom_offer_percent")),
		"rate": flt(price.price_list_rate),
	}


@frappe.whitelist()
def get_variants(template):
	"""Active variants of a template, for the Add Variants dialog."""
	frappe.has_permission("Item", "read", template, throw=True)
	variants = frappe.get_all(
		"Item",
		filters={"variant_of": template, "disabled": 0},
		fields=["name as item_code", "item_name"],
		order_by="name asc",
	)
	if not variants:
		return []
	attrs = frappe.get_all(
		"Item Variant Attribute",
		filters={"parent": ["in", [v.item_code for v in variants]], "parenttype": "Item"},
		fields=["parent", "attribute_value"],
		order_by="idx asc",
	)
	by_item = {}
	for a in attrs:
		by_item.setdefault(a.parent, []).append(a.attribute_value or "")
	for v in variants:
		v.attributes = " / ".join(x for x in by_item.get(v.item_code, []) if x)
	return variants


@frappe.whitelist()
def set_offer(items, offer):
	"""Set Offer % on the selling price of the items (a template means all its variants).
	Rate becomes MRP less the offer. Items without a selling price are skipped."""
	if not offers_on():
		frappe.throw(_("Offers are switched off (RFID Settings > Purchase Pricing)."))
	items = frappe.parse_json(items) if isinstance(items, str) else items
	offer = flt(offer)
	if offer < 0 or offer >= 100:
		frappe.throw(_("Offer % must be from 0 to 99."))
	frappe.has_permission("Item Price", "write", throw=True)

	codes = []
	for code in items:
		if frappe.db.get_value("Item", code, "has_variants"):
			codes += frappe.get_all("Item", filters={"variant_of": code}, pluck="name")
		else:
			codes.append(code)

	updated, missing = [], []
	for code in dict.fromkeys(codes):
		ex = plain_price(code, selling_price_list())
		if not ex:
			missing.append(code)
			continue
		price = frappe.get_doc("Item Price", ex.name)
		if flt(price.custom_mrp) <= 0:
			price.custom_mrp = price.price_list_rate
		price.custom_offer_percent = offer
		price.save()
		updated.append({"item_code": code, "mrp": flt(price.custom_mrp), "rate": offer_price(price.custom_mrp, offer)})
	return {"updated": updated, "missing": missing}


@frappe.whitelist()
def item_rows(doc, rows):
	"""Item table rows for many items in one call (Add Variants): ERPNext's own item details for each,
	with the qty / rate given. The form adds them without per-row triggers, so 50+ rows stay fast."""
	from erpnext.stock.get_item_details import get_item_details

	doc = frappe._dict(frappe.parse_json(doc))
	rows = frappe.parse_json(rows)
	frappe.has_permission(doc.doctype, "create" if not doc.get("name") or doc.get("__islocal") else "write", throw=True)
	child_doctype = frappe.get_meta(doc.doctype).get_field("items").options
	out = []
	for r in rows:
		args = {
			"item_code": r["item_code"],
			"set_warehouse": doc.get("set_warehouse"),
			"supplier": doc.get("supplier"),
			"currency": doc.get("currency"),
			"is_internal_supplier": doc.get("is_internal_supplier"),
			"update_stock": doc.get("update_stock"),
			"conversion_rate": doc.get("conversion_rate"),
			"price_list": doc.get("buying_price_list"),
			"price_list_currency": doc.get("price_list_currency"),
			"plc_conversion_rate": doc.get("plc_conversion_rate"),
			"company": doc.get("company"),
			"is_return": doc.get("is_return"),
			"is_subcontracted": doc.get("is_subcontracted"),
			"ignore_pricing_rule": doc.get("ignore_pricing_rule"),
			"doctype": doc.doctype,
			"name": doc.get("name"),
			"project": doc.get("project"),
			"qty": flt(r.get("qty")) or 1,
			"tax_category": doc.get("tax_category"),
			"child_doctype": child_doctype,
		}
		details = frappe._dict(get_item_details(args, doc))
		details.item_code = r["item_code"]
		details.qty = flt(r.get("qty")) or 1
		details.stock_qty = details.qty * (flt(details.conversion_factor) or 1)
		if flt(r.get("rate")) > 0:
			# the rate typed in the dialog is the purchase rate: no price list discount on top
			details.update({"rate": flt(r["rate"]), "price_list_rate": flt(r["rate"]),
				"discount_percentage": 0, "discount_amount": 0, "margin_rate_or_amount": 0})
		out.append(details)
	return out
