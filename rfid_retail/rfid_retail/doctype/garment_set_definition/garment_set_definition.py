# Copyright (c) 2026, MicroNXT Solutions Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint


class GarmentSetDefinition(Document):
	def validate(self):
		item = frappe.db.get_value("Item", self.item,
			["variant_of", "has_serial_no", "custom_rfid_tracked", "is_stock_item"], as_dict=True)
		if item.variant_of:
			frappe.throw(_("{0} is a variant. Define the set on its template {1}; every variant uses it.").format(
				self.item, item.variant_of))
		if not (item.has_serial_no and item.custom_rfid_tracked and item.is_stock_item):
			frappe.throw(_("{0} must be a stock item with Has Serial No and RFID Tracked Garment ticked.").format(self.item))
		if not self.components:
			frappe.throw(_("Add at least one piece."))
		seen = set()
		for row in self.components:
			if row.role in seen:
				frappe.throw(_("Row {0}: {1} is listed twice. Use Pieces to count it more than once.").format(row.idx, row.role))
			seen.add(row.role)
			row.role_code = frappe.db.get_value("Garment Piece", row.role, "code")
			if cint(row.qty) < 1:
				row.qty = 1
