# Copyright (c) 2026, MicroNXT Solutions Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

HISTORY_DELETE_ROLES = {"System Manager", "RFID Store Manager", "RFID Owner"}


class RFIDTagEvent(Document):
	"""History rows are written by rfid_retail.rfid.log_event only and never change."""

	def validate(self):
		if not self.is_new():
			frappe.throw(_("RFID tag history cannot be edited."))

	def on_trash(self):
		# Managers may clear history of test / cancelled documents
		if not (frappe.flags.in_uninstall or frappe.session.user == "Administrator"
				or set(frappe.get_roles()) & HISTORY_DELETE_ROLES):
			frappe.throw(_("RFID tag history cannot be deleted."))
