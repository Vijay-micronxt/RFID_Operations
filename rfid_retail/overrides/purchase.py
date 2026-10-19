"""Purchase Order / Receipt / Invoice rows (RFID Settings > Purchase Pricing): item from Item Name,
item tax template, MRP and offer, and on submit (Receipt, Invoice) the row prices become the item's
buying and selling prices."""

import json

import frappe
from frappe import _
from frappe.utils import flt

from rfid_retail.pricing import (
	buying_price_list,
	default_margin,
	gst_percent,
	import_fill_on,
	in_range,
	item_tax_rows,
	mrp_for,
	offer_price,
	offers_on,
	plain_price,
	pricing_on,
	selling_price_list,
)


def before_validate(doc, method=None):
	if import_fill_on():
		fill_item_from_name(doc)
		fill_item_tax_template(doc)


def validate(doc, method=None):
	if pricing_on():
		set_row_prices(doc)


def on_submit(doc, method=None):
	if pricing_on():
		set_item_prices(doc)


def on_cancel(doc, method=None):
	# restores what submit changed even if pricing was switched off since
	if frappe.get_meta(doc.doctype + " Item").has_field("custom_prev_selling_price"):
		restore_item_prices(doc)


def fill_item_from_name(doc):
	"""Rows with only an Item Name (Data Import) get the item with exactly that name."""
	for row in doc.items:
		name = (row.item_name or "").strip()
		if row.item_code or not name:
			continue
		active = {"disabled": 0, "has_variants": 0}
		hits = frappe.get_all("Item", filters={"item_name": name, **active}, pluck="name")
		if not hits and frappe.db.exists("Item", {"name": name, **active}):
			hits = [name]
		if not hits:
			frappe.throw(_("Row {0}: no active Item found with Item Name '{1}'").format(row.idx, name))
		if len(hits) > 1:
			frappe.throw(
				_("Row {0}: more than one Item has Item Name '{1}' ({2}). Give the Item Code instead.").format(
					row.idx, name, ", ".join(hits)
				)
			)
		row.item_code = hits[0]


def fill_item_tax_template(doc):
	"""ERPNext picks an item tax template only on an exact Tax Category match. Rows left without one
	take it from the Taxes table: same category first, then blank, then any, inside the rate range."""
	for row in doc.items:
		if not row.item_code or row.item_tax_template:
			continue
		base = flt(row.base_net_rate or row.base_rate or row.rate) / (flt(row.conversion_factor) or 1)
		rows = [t for t in item_tax_rows(row.item_code) if in_range(t, base)]
		rows = [t for t in rows if frappe.db.get_value("Item Tax Template", t.item_tax_template, "company") in (None, doc.company)]
		pick = (
			next((t for t in rows if (t.tax_category or "") == (doc.get("tax_category") or "")), None)
			or next((t for t in rows if not t.tax_category), None)
			or (rows[0] if rows else None)
		)
		if not pick:
			continue
		row.item_tax_template = pick.item_tax_template
		rates = frappe.get_all(
			"Item Tax Template Detail", filters={"parent": pick.item_tax_template}, fields=["tax_type", "tax_rate"]
		)
		row.item_tax_rate = json.dumps({r.tax_type: flt(r.tax_rate) for r in rates})


def set_row_prices(doc):
	offers = offers_on()
	for row in doc.items:
		if not row.item_code:
			continue
		if row.get("custom_margin_percent") in (None, ""):
			row.custom_margin_percent = default_margin()
		if not offers:
			row.custom_offer_percent = 0
		elif not flt(row.get("custom_offer_percent")) and _is_new_row(row) and not row.get("__offer_set"):
			# a new row keeps the offer the item already sells with; on the desk a 0 typed
			# by the user is kept (the form sends __offer_set)
			current = plain_price(row.item_code, selling_price_list())
			row.custom_offer_percent = flt(current.custom_offer_percent) if current else 0
		row.custom_gst_percent = gst_percent(row)
		base = flt(row.base_rate) / (flt(row.conversion_factor) or 1)
		row.custom_selling_price = mrp_for(base, row.custom_gst_percent, row.custom_margin_percent)
		if row.meta.has_field("custom_offer_price"):
			row.custom_offer_price = offer_price(row.custom_selling_price, row.custom_offer_percent)


def _is_new_row(row):
	return not row.name or not frappe.db.exists(row.doctype, row.name)


def _selling_rate(row):
	# rows submitted before offers existed have no Offer Price
	return flt(row.get("custom_offer_price")) or flt(row.custom_selling_price)


def set_item_prices(doc):
	"""Buying price = row rate per stock unit, selling price = MRP with offer. The replaced prices are kept
	on the row ("new" = there was none) so cancel can put them back. Same item on several rows: last row wins."""
	last = {row.item_code: row for row in doc.items if row.item_code}
	for code, row in last.items():
		buying = round(flt(row.base_rate) / (flt(row.conversion_factor) or 1), 2)
		if buying > 0:
			prev = _upsert_price(code, buying_price_list(), {"price_list_rate": buying})
			row.db_set("custom_prev_buying_price", prev, update_modified=False)

		mrp = flt(row.custom_selling_price)
		if mrp > 0:
			values = {"price_list_rate": _selling_rate(row)}
			if offers_on():
				values.update({"custom_mrp": mrp, "custom_offer_percent": flt(row.get("custom_offer_percent"))})
			prev = _upsert_price(code, selling_price_list(), values)
			row.db_set("custom_prev_selling_price", prev, update_modified=False)


def _upsert_price(item_code, price_list, values):
	ex = plain_price(item_code, price_list)
	if not ex:
		price = frappe.new_doc("Item Price")
		price.update(
			{"item_code": item_code, "price_list": price_list, "uom": frappe.db.get_value("Item", item_code, "stock_uom")}
		)
		price.update(values)
		price.insert(ignore_permissions=True)
		return "new"
	prev = json.dumps(
		{"rate": flt(ex.price_list_rate), "mrp": flt(ex.get("custom_mrp")), "offer": flt(ex.get("custom_offer_percent"))}
	)
	if any(flt(ex.get(k)) != flt(v) for k, v in values.items()):
		price = frappe.get_doc("Item Price", ex.name)
		price.update(values)
		price.save(ignore_permissions=True)
	return prev


def restore_item_prices(doc):
	"""Put back the prices this document replaced, unless a later document changed them since."""
	for row in doc.items:
		if not row.item_code:
			continue
		buying = round(flt(row.base_rate) / (flt(row.conversion_factor) or 1), 2)
		_restore(row.item_code, buying_price_list(), row.get("custom_prev_buying_price"), buying)
		_restore(row.item_code, selling_price_list(), row.get("custom_prev_selling_price"), _selling_rate(row))


def _restore(item_code, price_list, prev, rate_set):
	if not prev:
		return
	ex = plain_price(item_code, price_list)
	if not ex or flt(ex.price_list_rate) != flt(rate_set):
		return
	if prev == "new":
		frappe.delete_doc("Item Price", ex.name, ignore_permissions=True)
		return
	if prev.startswith("{"):
		old = json.loads(prev)
		values = {"price_list_rate": old["rate"], "custom_mrp": old.get("mrp") or old["rate"], "custom_offer_percent": old.get("offer") or 0}
	else:  # saved before offers existed: a plain rate
		values = {"price_list_rate": flt(prev), "custom_mrp": flt(prev), "custom_offer_percent": 0}
	if not frappe.db.get_value("Price List", price_list, "selling") or not ex.get("name") or "custom_mrp" not in ex:
		values = {"price_list_rate": values["price_list_rate"]}
	price = frappe.get_doc("Item Price", ex.name)
	price.update(values)
	price.save(ignore_permissions=True)
