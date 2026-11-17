"""RFID controls on Delivery Notes given on approval (and their returns).

A Delivery Note made at the counter for goods on approval takes the pieces off the
RFID basket exactly like a sale: tags are checked on submit and freed (or kept, per
RFID Settings), and cancelling puts them back. The return of the pieces the
customer did not keep brings them into stock without a tag. Every other Delivery
Note is left alone.
"""

import frappe
from frappe.utils import cint

from rfid_retail.overrides import sales_invoice
from rfid_retail.settings import is_enabled


def is_approval(doc):
	if cint(doc.get("custom_is_approval")):
		return True
	return bool(cint(doc.get("is_return")) and doc.get("return_against")
		and cint(frappe.db.get_value("Delivery Note", doc.return_against, "custom_is_approval")))


def before_submit(doc, method=None):
	if is_enabled() and is_approval(doc):
		sales_invoice.before_submit(doc, method)


def on_submit(doc, method=None):
	if is_enabled() and is_approval(doc):
		sales_invoice.on_submit(doc, method)


def on_cancel(doc, method=None):
	if is_enabled() and is_approval(doc):
		sales_invoice.on_cancel(doc, method)
