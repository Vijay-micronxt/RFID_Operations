import frappe
from frappe.utils import cint

from rfid_retail.settings import get_settings


def on_update(doc, method=None):
	"""Copy a numeric attribute's range into every item's attribute row (RFID Settings switch). ERPNext copies
	it only when the attribute is added to a template, and the Multiple Variants dialog reads the item row."""
	if not cint(get_settings().attribute_range_sync):
		return
	for name in frappe.get_all(
		"Item Variant Attribute", filters={"attribute": doc.name, "parenttype": "Item"}, pluck="name"
	):
		frappe.db.set_value(
			"Item Variant Attribute",
			name,
			{
				"numeric_values": doc.numeric_values,
				"from_range": doc.from_range,
				"to_range": doc.to_range,
				"increment": doc.increment,
			},
			update_modified=False,
		)
