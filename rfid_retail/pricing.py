"""Purchase pricing and offers (RFID Settings > Purchase Pricing). Independent of the RFID master switch.

MRP         = purchase rate per stock unit x (1 + GST %) x (1 + Margin %), rupee-rounded, then up to the
              next multiple of "Round MRP up to" (10: 647 -> 650).
Offer Price = MRP less Offer %, rupee-rounded: the rate the item sells at (selling Item Price rate).

The columns these need are added only when a switch is on, so other sites see no change.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.utils import cint, flt

from rfid_retail.settings import get_settings

PURCHASE_ITEM_DOCTYPES = ("Purchase Order Item", "Purchase Receipt Item", "Purchase Invoice Item")
PRICED_ITEM_DOCTYPES = ("Purchase Receipt Item", "Purchase Invoice Item")  # submit sets the item's prices


def pricing_on():
	return cint(get_settings().purchase_pricing)


def offers_on():
	return cint(get_settings().offers_enabled)


def import_fill_on():
	return cint(get_settings().purchase_import_fill)


def default_margin():
	margin = get_settings().default_margin
	return 10.0 if margin is None else flt(margin)


def round_up(value):
	step = max(cint(get_settings().mrp_round_up_to), 1)
	rupees = cint(flt(value) + 0.5) if flt(value) > 0 else 0
	return ((rupees + step - 1) // step) * step


def mrp_for(base_rate, gst, margin):
	if flt(base_rate) <= 0:
		return 0
	return round_up(flt(base_rate) * (1 + flt(gst) / 100.0) * (1 + flt(margin) / 100.0))


def offer_price(mrp, offer):
	if flt(mrp) <= 0:
		return 0
	if not offers_on():
		return flt(mrp)
	return cint(flt(mrp) * (1 - flt(offer) / 100.0) + 0.5)


def selling_price_list():
	return frappe.db.get_single_value("Selling Settings", "selling_price_list") or "Standard Selling"


def buying_price_list():
	return frappe.db.get_single_value("Buying Settings", "buying_price_list") or "Standard Buying"


def plain_price(item_code, price_list):
	"""The item's general price on a list (no customer, supplier or batch), newest first."""
	fields = ["name", "price_list_rate"]
	if frappe.get_meta("Item Price").has_field("custom_mrp"):
		fields += ["custom_mrp", "custom_offer_percent"]
	rows = frappe.get_all(
		"Item Price",
		filters={
			"item_code": item_code,
			"price_list": price_list,
			"customer": ["is", "not set"],
			"supplier": ["is", "not set"],
			"batch_no": ["is", "not set"],
		},
		fields=fields,
		order_by="valid_from desc, modified desc",
		limit=1,
	)
	return rows[0] if rows else None


def item_tax_rows(item_code):
	"""Taxes table rows of the item, else its template item, else its item group (cached per request:
	a document with 50+ rows of the same template asks once)."""
	cache = getattr(frappe.local, "rfid_item_tax_rows", None)
	if cache is None:
		cache = frappe.local.rfid_item_tax_rows = {}
	if item_code not in cache:
		cache[item_code] = _item_tax_rows(item_code)
	return cache[item_code]


def _item_tax_rows(item_code):
	iv = frappe.db.get_value("Item", item_code, ["variant_of", "item_group"], as_dict=True) or {}
	for parent, ptype in ((item_code, "Item"), (iv.get("variant_of"), "Item"), (iv.get("item_group"), "Item Group")):
		if not parent:
			continue
		rows = frappe.get_all(
			"Item Tax",
			filters={"parent": parent, "parenttype": ptype},
			fields=["item_tax_template", "tax_category", "minimum_net_rate", "maximum_net_rate"],
			order_by="idx asc",
		)
		if rows:
			return rows
	return []


def in_range(row, rate):
	lo, hi = flt(row.minimum_net_rate), flt(row.maximum_net_rate)
	return rate >= lo and (not hi or rate <= hi)


def gst_percent(row):
	"""GST % of a purchase row: its Item Tax Template, else the item's Taxes table by rate range."""
	template = row.item_tax_template
	if not template:
		base = flt(row.base_rate) / (flt(row.conversion_factor) or 1)
		template = next((t.item_tax_template for t in item_tax_rows(row.item_code) if in_range(t, base)), None)
	if not template or not frappe.get_meta("Item Tax Template").has_field("gst_rate"):  # gst_rate: India Compliance
		return 0.0
	return flt(frappe.get_cached_value("Item Tax Template", template, "gst_rate"))


# ---------------------------------------------------------------- columns


def ensure_fields(settings=None):
	"""Add the columns for the switches that are on. Never removes columns (they hold data)."""
	settings = settings or get_settings()
	margin = 10.0 if settings.default_margin is None else flt(settings.default_margin)
	fields = {}
	if cint(settings.purchase_pricing):
		for dt in PURCHASE_ITEM_DOCTYPES:
			fields.setdefault(dt, []).extend(_pricing_fields(dt, cint(settings.offers_enabled), margin))
	if cint(settings.offers_enabled):
		fields["Item Price"] = [
			{"fieldname": "custom_mrp", "fieldtype": "Currency", "label": "MRP", "options": "currency",
				"insert_after": "currency", "depends_on": "eval:doc.selling", "in_list_view": 1,
				"description": "Rate = MRP less Offer %"},
			{"fieldname": "custom_offer_percent", "fieldtype": "Percent", "label": "Offer %",
				"insert_after": "custom_mrp", "depends_on": "eval:doc.selling", "in_list_view": 1},
		]
	if cint(settings.variant_buttons):
		for dt in PURCHASE_ITEM_DOCTYPES:
			fields.setdefault(dt, []).append(
				{"fieldname": "custom_variant_of", "fieldtype": "Link", "label": "Template", "options": "Item",
					"insert_after": "item_name", "read_only": 1, "fetch_from": "item_code.variant_of"}
			)
	if fields:
		create_custom_fields(fields, update=True)
	if cint(settings.purchase_pricing) and cint(settings.offers_enabled):
		fit_grid_columns()
	if cint(settings.offers_enabled):
		backfill_mrp()


# Item grids show 10 column units; narrower standard columns leave room for Margin / MRP / Offer %
# / Offer Price. (fieldname, columns); 0 = out of the grid (still in the row form).
GRID_COLUMNS = {
	"Purchase Invoice Item": [("item_code", 3), ("qty", 1), ("rate", 1), ("amount", 1)],
	"Purchase Receipt Item": [("item_code", 2), ("qty", 1), ("rejected_qty", 1), ("rate", 1), ("net_amount", 1)],
	"Purchase Order Item": [("item_code", 2), ("schedule_date", 1), ("qty", 1), ("uom", 0), ("rate", 1), ("amount", 1)],
}


def fit_grid_columns():
	from frappe.custom.doctype.property_setter.property_setter import make_property_setter

	for doctype, cols in GRID_COLUMNS.items():
		for fieldname, columns in cols:
			if columns:
				make_property_setter(doctype, fieldname, "columns", columns, "Int", validate_fields_for_doctype=False)
			else:
				make_property_setter(doctype, fieldname, "in_list_view", 0, "Check", validate_fields_for_doctype=False)


def _pricing_fields(doctype, offers, margin):
	fields = [
		{"fieldname": "custom_margin_percent", "fieldtype": "Percent", "label": "Margin %", "insert_after": "rate",
			"in_list_view": 1, "columns": 1, "default": str(margin)},
		{"fieldname": "custom_gst_percent", "fieldtype": "Percent", "label": "GST %", "read_only": 1,
			"insert_after": "custom_margin_percent"},
		{"fieldname": "custom_selling_price", "fieldtype": "Currency", "label": "MRP", "read_only": 1,
			"insert_after": "custom_gst_percent", "in_list_view": 1, "columns": 1,
			"description": "Rate x (1 + GST %) x (1 + Margin %), rounded up"},
	]
	after = "custom_selling_price"
	if offers:
		fields += [
			{"fieldname": "custom_offer_percent", "fieldtype": "Percent", "label": "Offer %",
				"insert_after": "custom_selling_price", "in_list_view": 1, "columns": 1,
				"description": "Sale discount on MRP. A new row starts with the item's current offer"},
			{"fieldname": "custom_offer_price", "fieldtype": "Currency", "label": "Offer Price", "read_only": 1,
				"insert_after": "custom_offer_percent", "in_list_view": 1, "columns": 1,
				"description": "MRP less Offer %: the rate the item sells at"},
		]
		after = "custom_offer_price"
	if doctype in PRICED_ITEM_DOCTYPES:
		fields += [
			{"fieldname": "custom_prev_selling_price", "fieldtype": "Data", "label": "Previous Selling Price",
				"read_only": 1, "hidden": 1, "no_copy": 1, "insert_after": after},
			{"fieldname": "custom_prev_buying_price", "fieldtype": "Data", "label": "Previous Buying Price",
				"read_only": 1, "hidden": 1, "no_copy": 1, "insert_after": "custom_prev_selling_price"},
		]
	return fields


def backfill_mrp():
	"""Selling prices made before offers were on: MRP = their rate, no offer."""
	frappe.db.sql(
		"""update `tabItem Price` set custom_mrp = price_list_rate, custom_offer_percent = 0
		where selling = 1 and ifnull(custom_mrp, 0) <= 0"""
	)
