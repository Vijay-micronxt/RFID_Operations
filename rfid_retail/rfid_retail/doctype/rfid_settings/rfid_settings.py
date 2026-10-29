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
		self.validate_item_codes()
		self.validate_labels()
		if (self.mrp_round_up_to or 0) < 1:
			self.mrp_round_up_to = 1
		if (self.approval_pin_minutes or 0) < 1:
			self.approval_pin_minutes = 10
		if self.approval_whatsapp and not (self.whatsapp_api_url and self.whatsapp_sender_id and self.whatsapp_auth_token):
			frappe.throw(_("Fill the WhatsApp API URL, Sender ID and Auth Token to send PINs by WhatsApp."))

	def validate_item_codes(self):
		if not self.item_codes_enabled:
			return
		if frappe.db.get_default("item_naming_by") == "Naming Series":
			frappe.throw(_("Stock Settings names items by Naming Series. Set Item Naming By to Item Code to use code patterns."))
		if len(self.code_separator or "") > 1:
			frappe.throw(_("Use a single character as the code separator."))

	def validate_labels(self):
		if self.barcode_kind == "EAN-13" and len("".join(c for c in (self.barcode_prefix or "") if c.isdigit())) > 9:
			frappe.throw(_("An EAN-13 prefix can have at most 9 digits, so at least 3 remain for the running number."))
		if (self.label_width_mm or 0) <= 0 or (self.label_height_mm or 0) <= 0:
			frappe.throw(_("Set the label width and height in mm."))

	def on_update(self):
		from rfid_retail.setup.install import grant_counter_permissions

		grant_counter_permissions()
		if self.purchase_pricing or self.offers_enabled or self.variant_buttons:
			from rfid_retail.pricing import ensure_fields

			ensure_fields(self)  # the cached settings still hold the switches as they were before this save
		frappe.clear_cache()  # boot info carries the item code / label / pricing switches
