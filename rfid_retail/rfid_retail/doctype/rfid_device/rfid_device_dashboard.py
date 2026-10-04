from frappe import _


def get_data():
	return {
		"fieldname": "device",
		"transactions": [
			{"label": _("Activity"), "items": ["RFID Basket", "RFID Audit Session", "RFID Tag Assignment"]},
			{"label": _("Alerts"), "items": ["RFID Exception"]},
		],
	}
