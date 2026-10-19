import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

ROLES = ("RFID Owner", "RFID Store Manager", "RFID Cashier", "RFID Stock Operator", "RFID Device")


def after_install():
	after_migrate()


def after_migrate():
	make_roles()
	create_custom_fields(get_custom_fields(), update=True)
	seed_station_roles()
	seed_setting_defaults()
	grant_counter_permissions()
	ensure_pricing_fields()


def ensure_pricing_fields():
	from rfid_retail.pricing import ensure_fields

	ensure_fields()


def seed_setting_defaults():
	"""Settings fields added after a site was set up start empty; give them their defaults."""
	values = frappe.db.get_singles_dict("RFID Settings")
	for df in frappe.get_meta("RFID Settings").fields:
		if df.default is None or df.default == "" or df.fieldtype in ("Table", "Section Break", "Column Break", "Tab Break"):
			continue
		if values.get(df.fieldname) is None:
			frappe.db.set_single_value("RFID Settings", df.fieldname, df.default)
	frappe.clear_document_cache("RFID Settings", "RFID Settings")


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
	row_tags = [
		{"fieldname": "custom_rfid_tags", "fieldtype": "Small Text", "label": "RFID Tags", "no_copy": 1,
			"read_only": 1, "allow_on_submit": 1, "insert_after": "serial_no",
			"description": "Tags read for this row after submit (RFID Tags button), one per unit."},
		{"fieldname": "custom_rfid_tag_count", "fieldtype": "Int", "label": "RFID Tags Read", "read_only": 1,
			"allow_on_submit": 1, "no_copy": 1, "insert_after": "custom_rfid_tags"},
	]
	return {
		"Purchase Receipt Item": row_tags,
		"Purchase Invoice Item": row_tags,
		"Stock Entry Detail": row_tags,
		"Item": [
			{"fieldname": "custom_rfid_section", "fieldtype": "Section Break", "label": "RFID",
				"insert_after": "has_serial_no", "collapsible": 0},
			{"fieldname": "custom_rfid_tracked", "fieldtype": "Check", "label": "RFID Tracked Garment",
				"insert_after": "custom_rfid_section",
				"description": "Every unit carries a reusable RFID tag. With Has Serial No each unit also gets a serial "
					"number (set a Serial No Series to create them automatically); without it, tags stand for "
					"units of the item."},
			{"fieldname": "custom_style_code", "fieldtype": "Data", "label": "Style Code",
				"insert_after": "custom_rfid_tracked", "in_standard_filter": 1},
			{"fieldname": "custom_supplier", "fieldtype": "Link", "label": "Supplier", "options": "Supplier",
				"insert_after": "brand", "in_standard_filter": 1,
				"description": "Main supplier. Used by the item code pattern; variants take it from the template."},
		],
		"Supplier": [
			{"fieldname": "custom_item_code_prefix", "fieldtype": "Data", "label": "Item Code Prefix",
				"insert_after": "supplier_group", "length": 10,
				"description": "Short code used in item codes, e.g. RAY. Blank = first letters of the name."},
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
		"POS Profile": [
			{"fieldname": "custom_rfid_counter", "fieldtype": "Check", "label": "RFID Counter",
				"insert_after": "warehouse",
				"description": "Bill this counter from RFID tray reads (RFID Settings must be enabled)."},
		],
		"Sales Invoice": [basket],
		"POS Invoice": [basket],
		"Sales Invoice Item": [manifest],
		"POS Invoice Item": [manifest],
	}
