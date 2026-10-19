from frappe.utils import flt

from rfid_retail.pricing import offer_price, offers_on


def validate(doc, method=None):
	"""Selling prices with offers on: Rate = MRP less Offer %. A price without an MRP takes its rate as MRP."""
	if not doc.selling or not offers_on() or not doc.meta.has_field("custom_mrp"):
		return
	if flt(doc.custom_mrp) <= 0:
		doc.custom_mrp = flt(doc.price_list_rate)
		doc.custom_offer_percent = 0
	doc.custom_offer_percent = min(max(flt(doc.custom_offer_percent), 0), 100)
	doc.price_list_rate = offer_price(doc.custom_mrp, doc.custom_offer_percent)
