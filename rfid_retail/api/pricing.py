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
