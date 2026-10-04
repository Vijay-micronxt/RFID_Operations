from frappe import _


def get_data():
	return {
		"fieldname": "component",
		"transactions": [{"label": _("Tags"), "items": ["RFID Tag Assignment", "RFID Tag", "RFID Exception"]}],
	}
