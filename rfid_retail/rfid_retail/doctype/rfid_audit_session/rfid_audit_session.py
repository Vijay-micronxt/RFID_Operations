# Copyright (c) 2026, MicroNXT Solutions Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class RFIDAuditSession(Document):
	def validate(self):
		if self.scope != "Selected Items":
			return
		for row in self.scope_items:
			if bool(row.item_code) == bool(row.item_group):
				frappe.throw(_("Items Counted row {0}: choose an item or an item group, not both.").format(row.idx))
		if not self.scope_items:
			frappe.throw(_("Choose the items to count."))
