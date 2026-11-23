# Copyright (c) 2026, MicroNXT Solutions Pvt. Ltd. and contributors
# For license information, please see license.txt

"""A physical count sent from the handheld app.

It is a record of what the phone saw: the items with their system and scanned quantities, and the
tags found, missing or not expected. Drafting or submitting it changes no stock and no tag.

Once submitted, `make_stock_reconciliation` can turn its differences into a DRAFT Stock
Reconciliation. Only submitting that draft changes stock.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt, nowdate, nowtime


class PhysicalAudit(Document):
	def before_insert(self):
		self.operator = self.operator or frappe.session.user
		self.sent_at = self.sent_at or frappe.utils.now_datetime()

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


def release_reconciliation(doc, method=None):
	"""A Stock Reconciliation made from a Physical Audit was deleted or cancelled: the audit forgets it, so the
	Create Stock Reconciliation button is offered again (and the draft can be made afresh)."""
	frappe.db.set_value("Physical Audit", {"stock_reconciliation": doc.name}, "stock_reconciliation", None,
		update_modified=False)


@frappe.whitelist(methods=["POST"])
def make_stock_reconciliation(name):
	"""Makes a DRAFT Stock Reconciliation from a submitted Physical Audit. Nothing is submitted: stock only
	changes when someone submits that draft.

	The audit's Difference (system minus scanned) is applied to the item's current ERPNext stock, both ways:
	  - Difference above 0: pieces were not found, so the stock goes DOWN by that much (never below zero);
	  - Difference below 0: more were found than the system knew, so the stock goes UP by that much.
	Only the pieces the audit knows about (tagged ones) are touched, so untagged stock stays as it is.
	Serial and batch items are left out (their rows need a serial or batch list). A raise needs a cost per piece:
	the item's current rate, else its valuation / last purchase / standard rate; rows with none are still put in
	the draft, listed in `no_rate`, for someone to fill in before submitting. Pressing it again returns the draft
	already made.
	"""
	from erpnext.stock.doctype.stock_reconciliation.stock_reconciliation import get_stock_balance_for

	doc = frappe.get_doc("Physical Audit", name, for_update=True)
	doc.check_permission("read")
	if doc.docstatus != 1:
		frappe.throw(_("Submit the Physical Audit first."))
	frappe.has_permission("Stock Reconciliation", "create", throw=True)

	if doc.stock_reconciliation:
		state = frappe.db.get_value("Stock Reconciliation", doc.stock_reconciliation, "docstatus")
		if state in (0, 1):
			return {"name": doc.stock_reconciliation, "created": False, "skipped": [], "no_rate": []}

	posting_date, posting_time = nowdate(), nowtime()
	rows, skipped, no_rate = [], [], []
	for row in doc.items:
		# Positive raises the stock, negative lowers it.
		change = -cint(row.difference)
		if change == 0:
			continue
		item = frappe.get_cached_value(
			"Item", row.item_code,
			["has_serial_no", "has_batch_no", "valuation_rate", "last_purchase_rate", "standard_rate"], as_dict=True)
		if not item or item.has_serial_no or item.has_batch_no:
			skipped.append(row.item_code)
			continue
		balance = get_stock_balance_for(row.item_code, doc.warehouse, posting_date, posting_time, company=doc.company)
		current = flt(balance.get("qty"))
		counted = max(current + change, 0)
		if counted == current:
			continue  # nothing in stock to take off
		# The current rate, so only the quantity changes; a raise of an item with no stock yet has to find one.
		rate = flt(balance.get("rate")) or flt(item.valuation_rate) or flt(item.last_purchase_rate) or flt(item.standard_rate)
		if counted > current and not rate:
			no_rate.append(row.item_code)
		rows.append({"item_code": row.item_code, "warehouse": doc.warehouse, "qty": counted, "valuation_rate": rate})

	if not rows:
		why = _("Nothing to reconcile: no item has a difference that changes the stock.")
		if skipped:
			why += " " + _("Left out (serial or batch items): {0}.").format(", ".join(skipped))
		frappe.throw(why)

	reconciliation = frappe.get_doc({
		"doctype": "Stock Reconciliation",
		"purpose": "Stock Reconciliation",
		"company": doc.company,
		"posting_date": posting_date,
		"posting_time": posting_time,
		"set_warehouse": doc.warehouse,
		"custom_physical_audit": doc.name,
		"items": rows,
	})
	reconciliation.insert()  # as the caller; the difference account comes from the company's default

	frappe.db.set_value("Physical Audit", doc.name, "stock_reconciliation", reconciliation.name, update_modified=False)
	doc.add_comment("Info", _("Draft Stock Reconciliation {0} created.").format(reconciliation.name))
	return {"name": reconciliation.name, "created": True, "skipped": skipped, "no_rate": no_rate}
