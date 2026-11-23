# Copyright (c) 2026, MicroNXT Solutions Pvt. Ltd. and contributors
# For license information, please see license.txt

"""A physical count sent from the handheld app.

It is a record of what the phone saw: the items with their system and scanned quantities, and the
tags found, missing or not expected. Drafting or submitting it changes no stock and no tag.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, now_datetime


class PhysicalAudit(Document):
	def before_insert(self):
		self.operator = self.operator or frappe.session.user
		self.sent_at = self.sent_at or now_datetime()

	def validate(self):
		self._check_items_exist()
		seen = set()
		for row in self.items:
			if row.item_code in seen:
				frappe.throw(_("Row {0}: {1} is listed twice.").format(row.idx, row.item_code))
			seen.add(row.item_code)
			if cint(row.system_qty) < 0 or cint(row.scanned_qty) < 0:
				frappe.throw(_("Row {0}: quantities cannot be negative.").format(row.idx))
			# Always worked out here, so an edited row cannot disagree with its own numbers.
			row.difference = cint(row.system_qty) - cint(row.scanned_qty)

		self.expected_units = sum(cint(r.system_qty) for r in self.items)
		self.scanned_units = sum(cint(r.scanned_qty) for r in self.items)
		self.difference_units = self.expected_units - self.scanned_units
		self.missing_tags = sum(1 for t in self.tags if t.result == "Missing")
		self.unknown_tags = sum(1 for t in self.tags if t.result == "Unknown")

	def _check_items_exist(self):
		"""Frappe does not check Link fields inside child rows here, so a typo or a deleted item would be saved."""
		codes = {r.item_code for r in self.items if r.item_code} | {t.item_code for t in self.tags if t.item_code}
		if not codes:
			return
		missing = sorted(codes - set(frappe.get_all("Item", {"name": ["in", list(codes)]}, pluck="name")))
		if missing:
			shown = ", ".join(missing[:10]) + (" ..." if len(missing) > 10 else "")
			frappe.throw(_("Item not found: {0}").format(shown))
