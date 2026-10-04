from frappe import _


def get_data():
	return {
		"fieldname": "tag",
		"non_standard_fieldnames": {"RFID Exception": "epc"},
		"transactions": [{"label": _("History"), "items": ["RFID Tag Assignment", "RFID Exception"]}],
	}
