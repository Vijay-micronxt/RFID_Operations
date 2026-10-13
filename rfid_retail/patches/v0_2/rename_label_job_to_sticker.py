"""Label Print Job is called Sticker Print Job (what store staff search for)."""

import frappe


def execute():
	for old, new in (("Label Print Item", "Sticker Print Job Item"), ("Label Print Job", "Sticker Print Job")):
		if frappe.db.exists("DocType", old) and not frappe.db.exists("DocType", new):
			frappe.rename_doc("DocType", old, new, force=True)
