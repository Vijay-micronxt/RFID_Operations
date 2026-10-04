"""Adds an "RFID" group to the Connections panel of standard ERPNext doctypes."""

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
	})


def serial_no(data):
	return _add(data, "serial_no", {
		"Garment Component": None,
		"RFID Tag": None,
		"RFID Tag Assignment": None,
		"RFID Exception": None,
	})


def sales_invoice(data):
	return _add(data, "sales_invoice", {
		"RFID Basket": None,
		"RFID Tag Assignment": None,
		"Garment Component": None,
	})


def pos_profile(data):
	return _add(data, "pos_profile", {"RFID Basket": None, "RFID Device": None, "RFID Exception": None})


def warehouse(data):
	return _add(data, "warehouse", {"RFID Device": None, "RFID Audit Session": None})
