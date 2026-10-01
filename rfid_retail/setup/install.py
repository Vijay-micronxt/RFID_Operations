import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

ROLES = ("RFID Owner", "RFID Store Manager", "RFID Cashier", "RFID Stock Operator", "RFID Device")


def after_install():
	after_migrate()


def after_migrate():
	make_roles()
	create_custom_fields(get_custom_fields(), update=True)
	seed_station_roles()
	grant_counter_permissions()


def seed_station_roles():
	"""Fresh install: fill the station table with the generic RFID roles. Never overwrites a configured table."""
	from rfid_retail.settings import DEFAULT_STATION_ROLES

	settings = frappe.get_single("RFID Settings")
	if settings.station_roles:
		return
	for station, roles in DEFAULT_STATION_ROLES.items():
		for role in roles:
			settings.append("station_roles", {"station": station, "role": role})
	settings.flags.ignore_permissions = True
	settings.save()


# Selling a serialised garment creates a Serial and Batch Bundle as the cashier.
# POS Next's cashier role does not cover that, so every serial sale would fail.
# Granted to whichever roles RFID Settings maps to the Billing station.
COUNTER_PERMS = {
	"Serial and Batch Bundle": ("read", "create", "write", "submit", "cancel"),
	"Serial No": ("read",),
	"Sales Invoice": ("read", "create", "write", "submit"),
	"POS Profile": ("read",),
	"Loyalty Program": ("read",),
	# India Compliance creates one log per GST period on the first invoice of that period.
	"GST Return Log": ("read", "create", "write"),
}


def grant_counter_permissions():
	from frappe.permissions import add_permission, update_permission_property

	from rfid_retail.settings import station_roles

	roles = station_roles("Billing") - {"System Manager"}
	for doctype, ptypes in COUNTER_PERMS.items():
		if not frappe.db.exists("DocType", doctype):  # e.g. GST Return Log without India Compliance
			continue
		for role in roles:
			if not frappe.db.exists("Custom DocPerm", {"parent": doctype, "role": role, "permlevel": 0}):
				add_permission(doctype, role, 0)
			for ptype in ptypes:
				update_permission_property(doctype, role, 0, ptype, 1, validate=False)


def make_roles():
	for role in ROLES:
		if not frappe.db.exists("Role", role):
			frappe.get_doc({"doctype": "Role", "role_name": role, "desk_access": 1}).insert(ignore_permissions=True)


def get_custom_fields():
	manifest = {
		"fieldname": "custom_rfid_manifest",
		"fieldtype": "Small Text",
		"label": "RFID Manifest",
		"read_only": 1,
		"allow_on_submit": 1,
		"insert_after": "serial_no",
		"description": "Parent serial, pieces and tags at the moment of sale",
	}
	basket = {
		"fieldname": "custom_rfid_basket",
		"fieldtype": "Link",
		"label": "RFID Basket",
		"options": "RFID Basket",
		"read_only": 1,
		"no_copy": 1,
		"insert_after": "pos_profile",
	}
	return {
		"Item": [
			{"fieldname": "custom_rfid_section", "fieldtype": "Section Break", "label": "RFID",
				"insert_after": "has_serial_no", "collapsible": 0},
			{"fieldname": "custom_rfid_tracked", "fieldtype": "Check", "label": "RFID Tracked Garment",
				"insert_after": "custom_rfid_section",
				"description": "Every unit gets a serial number and reusable RFID tag(s). Needs Has Serial No."},
			{"fieldname": "custom_style_code", "fieldtype": "Data", "label": "Style Code",
				"insert_after": "custom_rfid_tracked", "in_standard_filter": 1},
		],
		"Serial No": [
			{"fieldname": "custom_rfid_section", "fieldtype": "Section Break", "label": "RFID",
				"insert_after": "status"},
			{"fieldname": "custom_rfid_status", "fieldtype": "Select", "label": "RFID Status",
				"options": "\nPending Mapping\nReady\nSold\nReturned - Hold\nHold", "read_only": 1,
				"in_list_view": 1, "in_standard_filter": 1, "insert_after": "custom_rfid_section"},
			{"fieldname": "custom_is_garment_set", "fieldtype": "Check", "label": "Is Garment Set",
				"read_only": 1, "insert_after": "custom_rfid_status"},
			{"fieldname": "custom_rfid_basket", "fieldtype": "Link", "label": "On Counter Basket",
				"options": "RFID Basket", "read_only": 1, "insert_after": "custom_is_garment_set"},
			{"fieldname": "custom_sales_invoice", "fieldtype": "Link", "label": "Sold On",
				"options": "Sales Invoice", "read_only": 1, "insert_after": "custom_rfid_basket"},
		],
		"Sales Invoice": [basket],
		"POS Invoice": [basket],
		"Sales Invoice Item": [manifest],
		"POS Invoice Item": [manifest],
	}
