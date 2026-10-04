# Copyright (c) 2026, MicroNXT Solutions Pvt. Ltd. and contributors
# For license information, please see license.txt

import re

import frappe
from frappe import _
from frappe.model.document import Document


class GarmentPiece(Document):
	def validate(self):
		self.code = (self.code or "").strip().upper()
		if not re.fullmatch(r"[A-Z0-9]{1,6}", self.code):
			frappe.throw(_("Code must be 1-6 letters or digits, e.g. JKT."))
