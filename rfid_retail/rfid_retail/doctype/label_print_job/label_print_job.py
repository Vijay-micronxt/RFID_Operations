# Copyright (c) 2026, MicroNXT Solutions Pvt. Ltd. and contributors
# For license information, please see license.txt

from frappe.model.document import Document
from frappe.utils import cint


class LabelPrintJob(Document):
	def validate(self):
		from rfid_retail.labels import fill_row, label_price_list

		self.price_list = self.price_list or label_price_list()
		for row in self.items:
			fill_row(row, self.price_list)
		self.total_labels = sum(max(cint(r.copies), 0) for r in self.items)
