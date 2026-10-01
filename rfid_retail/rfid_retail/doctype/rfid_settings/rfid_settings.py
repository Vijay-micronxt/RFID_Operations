# Copyright (c) 2026, MicroNXT Solutions Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class RFIDSettings(Document):
	def validate(self):
		seen = set()
		for row in self.station_roles:
			key = (row.station, row.role)
			if key in seen:
				frappe.throw(_("Row {0}: {1} is already mapped to {2}.").format(row.idx, row.role, row.station))
			seen.add(key)
		if self.device_offline_minutes is not None and self.device_offline_minutes < 1:
			self.device_offline_minutes = 15

	def on_update(self):
		from rfid_retail.setup.install import grant_counter_permissions

		grant_counter_permissions()
