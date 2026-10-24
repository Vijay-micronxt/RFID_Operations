"""Adds an "RFID" group to the Connections panel of standard ERPNext doctypes."""

import frappe
from frappe import _


def _add(data, fieldname, links):
	"""links: {doctype: link field on that doctype, or None when it equals the dashboard fieldname}"""
	data.fieldname = data.fieldname or fieldname
	data.setdefault("non_standard_fieldnames", {})
	for doctype, field in links.items():
		if field and field != data.fieldname:
			data.non_standard_fieldnames[doctype] = field
	data.setdefault("transactions", []).append({"label": _("RFID"), "items": list(links)})
	return data


def item(data):
	return _add(data, "item_code", {
		"Garment Set Definition": "item",
		"Garment Component": "item_code",
		"RFID Tag": "item_code",
		"RFID Tag Assignment": "item_code",
		"RFID Tag Event": "item_code",
	})


def serial_no(data):
	return _add(data, "serial_no", {
		"Garment Component": None,
		"RFID Tag": None,
		"RFID Tag Assignment": None,
		"RFID Tag Event": None,
		"RFID Exception": None,
	})


def sales_invoice(data):
	return _add(data, "sales_invoice", {
		"RFID Basket": None,
		"RFID Tag Assignment": None,
		"RFID Tag Event": "reference_name",
		"Garment Component": None,
	})


def pos_profile(data):
	return _add(data, "pos_profile", {"RFID Basket": None, "RFID Device": None, "RFID Exception": None})


def warehouse(data):
	return _add(data, "warehouse", {"RFID Device": None, "RFID Audit Session": None})


def _stock_document(data, doctype):
	"""Tags read on a purchase / stock document: assignments it made, every tag event on it,
	exceptions raised for it and sticker jobs printed from it."""
	_add(data, data.fieldname or frappe.scrub(doctype), {
		"RFID Tag Assignment": "source_name",
		"RFID Tag Event": "reference_name",
		"RFID Exception": "reference_name",
		"Sticker Print Job": "source_name",
	})
	data.setdefault("dynamic_links", {}).update({
		"source_name": [doctype, "source_doctype"],
		"reference_name": [doctype, "reference_doctype"],
	})
	return data


def purchase_receipt(data):
	return _stock_document(data, "Purchase Receipt")


def purchase_invoice(data):
	return _stock_document(data, "Purchase Invoice")


def stock_entry(data):
	return _stock_document(data, "Stock Entry")
