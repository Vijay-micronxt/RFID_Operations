"""Barcodes and printed labels (RFID Settings > Barcodes & Labels).

Independent of the RFID master switch. What a label encodes:
	serial-tracked unit  -> the Serial No (one label per physical piece)
	set piece            -> the Garment Component name, e.g. SUIT-000125-JKT
	anything else        -> the item's barcode (one label per unit received)

The serial and piece labels are the same codes the RFID counter accepts as
label fallback, so a label always resolves to exactly one physical unit.
"""

import json

import frappe
from frappe import _
from frappe.utils import cint, flt, nowdate

from rfid_retail.settings import get_settings

INWARD_DOCTYPES = ("Purchase Receipt", "Purchase Invoice", "Stock Entry", "Stock Reconciliation")


# ---------------------------------------------------------------- item barcodes


def item_before_insert(doc, method=None):
	settings = get_settings()
	if not cint(settings.barcodes_enabled):
		return
	if doc.has_variants or not doc.is_stock_item or doc.get("barcodes"):
		return
	barcode, barcode_type = new_barcode(doc.item_code, settings)
	doc.append("barcodes", {"barcode": barcode, "barcode_type": barcode_type})


def new_barcode(item_code, settings=None):
	from frappe.model.naming import getseries

	settings = settings or get_settings()
	kind = settings.barcode_kind or "EAN-13"
	if kind == "Item Code":
		return item_code, ""
	prefix = "".join(ch for ch in (settings.barcode_prefix or "") if ch.isdigit())
	while True:
		if kind == "EAN-13":
			body = prefix[:11]
			number = body + getseries(f"RFIDEAN-{body}", 12 - len(body))
			code, barcode_type = number + ean_check_digit(number), "EAN"
		else:
			code, barcode_type = prefix + getseries(f"RFIDNUM-{prefix}", 8), ""
		if not frappe.db.exists("Item Barcode", {"barcode": code}):
			return code, barcode_type


def ean_check_digit(digits12):
	total = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(digits12))
	return str((10 - total % 10) % 10)


def item_barcode(item_code, create=True):
	"""The item's first barcode; creates one when allowed and missing."""
	code = frappe.db.get_value("Item Barcode", {"parent": item_code, "parenttype": "Item"}, "barcode",
		order_by="idx asc")
	if code or not create:
		return code
	settings = get_settings()
	if not cint(settings.barcodes_enabled):
		return None
	if frappe.db.get_value("Item", item_code, "has_variants"):
		return None
	code, barcode_type = new_barcode(item_code, settings)
	item = frappe.get_doc("Item", item_code)
	item.append("barcodes", {"barcode": code, "barcode_type": barcode_type})
	item.barcodes[-1].db_insert()
	frappe.clear_document_cache("Item", item_code)
	return code


@frappe.whitelist()
def backfill_barcodes(item_group=None):
	"""Give every stock item without a barcode one. Returns the count."""
	frappe.only_for(("System Manager", "Stock Manager", "Item Manager"))
	if not cint(get_settings().barcodes_enabled):
		frappe.throw(_("Switch on barcode creation in RFID Settings > Barcodes & Labels first."))
	filters = {"is_stock_item": 1, "has_variants": 0, "disabled": 0}
	if item_group:
		filters["item_group"] = item_group
	count = 0
	for item_code in frappe.get_all("Item", filters=filters, pluck="name"):
		if not frappe.db.exists("Item Barcode", {"parent": item_code, "parenttype": "Item"}):
			item_barcode(item_code)
			count += 1
	return count


# ---------------------------------------------------------------- barcode drawing


def barcode_svg(value, height="100%"):
	"""Jinja: inline SVG that stretches to its box, so any label size works.

	EAN-13 numbers draw as EAN-13, everything else as Code 128.
	"""
	import barcode as pybarcode

	value = (value or "").strip()
	if not value:
		return ""
	kind = "ean13" if (len(value) == 13 and value.isdigit() and ean_check_digit(value[:12]) == value[12]) else "code128"
	try:
		modules = pybarcode.get(kind, value).build()[0]
	except Exception:
		return frappe.utils.escape_html(value)
	width = len(modules)
	bars, x = [], 0
	while x < width:
		if modules[x] == "1":
			start = x
			while x < width and modules[x] == "1":
				x += 1
			bars.append(f'<rect x="{start}" y="0" width="{x - start}" height="1"/>')
		else:
			x += 1
	return (
		f'<svg class="rbl-barcode" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} 1" '
		f'preserveAspectRatio="none" style="width:100%;height:{height};display:block" shape-rendering="crispEdges">'
		f'{"".join(bars)}</svg>'
	)


def label_context(doc):
	"""Jinja: settings plus one entry per physical label for a Label Print Job."""
	settings = get_settings()
	labels = []
	for row in doc.items:
		for _copy in range(max(cint(row.copies), 0)):
			labels.append(row)
	company = doc.get("company") or frappe.defaults.get_user_default("Company")
	return frappe._dict(
		labels=labels,
		width=flt(settings.label_width_mm) or 50,
		height=flt(settings.label_height_mm) or 25,
		per_row=max(cint(settings.labels_per_row), 1),
		gap=flt(settings.label_gap_mm),
		show_price=cint(settings.label_show_price),
		footer=settings.label_footer or company or "",
		currency=frappe.db.get_value("Company", company, "default_currency") if company else None,
	)


# ---------------------------------------------------------------- label rows


def label_price_list():
	return get_settings().label_price_list or frappe.db.get_single_value("Selling Settings", "selling_price_list")


def item_price(item_code, price_list):
	if not price_list:
		return 0
	today = nowdate()
	for code in (item_code, frappe.get_cached_value("Item", item_code, "variant_of")):
		if not code:
			continue
		rate = frappe.db.sql(
			"""select price_list_rate from `tabItem Price`
			where item_code=%s and price_list=%s
				and (valid_from is null or valid_from <= %s)
				and (valid_upto is null or valid_upto >= %s)
			order by valid_from desc, modified desc limit 1""",
			(code, price_list, today, today),
		)
		if rate:
			return flt(rate[0][0])
	return 0


def item_details(item_code):
	"""Short variant text for a label: 'Red / 40'."""
	values = frappe.get_all(
		"Item Variant Attribute",
		filters={"parent": item_code, "parenttype": "Item"},
		pluck="attribute_value",
		order_by="idx asc",
	)
	return " / ".join(v for v in values if v)


def fill_row(row, price_list=None):
	"""Complete a Label Print Item from whatever identifies it."""
	if row.component and not row.serial_no:
		row.serial_no = frappe.db.get_value("Garment Component", row.component, "serial_no")
	if row.serial_no and not row.item_code:
		row.item_code = frappe.db.get_value("Serial No", row.serial_no, "item_code")
	if not row.item_code:
		frappe.throw(_("Row {0}: set an Item, Serial No or set piece.").format(row.idx))
	item = frappe.get_cached_value("Item", row.item_code, ["item_name", "has_variants"], as_dict=True)
	if item.has_variants:
		frappe.throw(_("Row {0}: {1} is a template. Print labels for its variants.").format(row.idx, row.item_code))
	row.item_name = row.item_name or item.item_name
	if not row.details:
		row.details = item_details(row.item_code)
		if row.component:
			role = frappe.db.get_value("Garment Component", row.component, "role")
			row.details = " / ".join(x for x in (row.details, role) if x)
	if not row.barcode:
		row.barcode = row.component or row.serial_no or item_barcode(row.item_code) or row.item_code
	if not flt(row.rate):
		row.rate = item_price(row.item_code, price_list)
	if row.serial_no:
		row.copies = 1  # one physical piece, one label
	return row


def rows_for_unit(item_code, serial_no=None, qty=1, with_components=None):
	"""Label rows for some stock of an item."""
	if with_components is None:
		with_components = cint(get_settings().component_labels)
	if not serial_no:
		return [{"item_code": item_code, "copies": max(cint(qty), 0)}]
	rows = [{"item_code": item_code, "serial_no": serial_no, "copies": 1}]
	if with_components:
		for comp in frappe.get_all("Garment Component", filters={"serial_no": serial_no}, pluck="name",
				order_by="name asc"):
			rows.append({"item_code": item_code, "serial_no": serial_no, "component": comp, "copies": 1})
	return rows


def rows_from_document(source_doctype, source_name):
	from rfid_retail.overrides.sales_invoice import get_row_serials

	doc = frappe.get_doc(source_doctype, source_name)
	if doc.docstatus != 1:
		frappe.throw(_("Submit {0} {1} before printing labels; serial numbers are created on submit.").format(
			_(source_doctype), source_name))
	if source_doctype == "Purchase Invoice" and not doc.update_stock:
		frappe.throw(_("Purchase Invoice {0} does not update stock. Print labels from its Purchase Receipt.").format(
			source_name))
	rows = []
	for item in doc.items:
		if source_doctype == "Stock Entry" and not item.get("t_warehouse"):
			continue
		if not frappe.get_cached_value("Item", item.item_code, "is_stock_item"):
			continue
		serials = get_row_serials(item)
		if serials:
			for serial_no in serials:
				rows += rows_for_unit(item.item_code, serial_no)
		else:
			qty = item.get("stock_qty") or item.get("transfer_qty") or item.get("qty")
			rows += rows_for_unit(item.item_code, qty=qty)
	return doc, rows


def rows_from_stock(warehouse, item_group=None, item_code=None):
	filters = {"warehouse": warehouse, "actual_qty": (">", 0)}
	if item_code:
		filters["item_code"] = item_code
	rows = []
	for bin in frappe.get_all("Bin", filters=filters, fields=["item_code", "actual_qty"], order_by="item_code asc"):
		item = frappe.get_cached_value("Item", bin.item_code, ["item_group", "has_serial_no"], as_dict=True)
		if item_group and not in_group(item.item_group, item_group):
			continue
		if item.has_serial_no:
			for serial_no in frappe.get_all("Serial No", filters={"item_code": bin.item_code, "warehouse": warehouse,
					"status": "Active"}, pluck="name", order_by="name asc"):
				rows += rows_for_unit(bin.item_code, serial_no)
		else:
			rows += rows_for_unit(bin.item_code, qty=bin.actual_qty)
	return rows


def in_group(group, parent):
	if group == parent:
		return True
	lft, rgt = frappe.db.get_value("Item Group", parent, ["lft", "rgt"])
	glft = frappe.db.get_value("Item Group", group, "lft")
	return glft is not None and lft <= glft <= rgt


def make_job(rows, source_doctype=None, source_name=None, company=None):
	if not rows:
		frappe.throw(_("Nothing to label: no stock items found."))
	job = frappe.new_doc("Label Print Job")
	job.source_doctype = source_doctype
	job.source_name = source_name
	job.company = company or frappe.defaults.get_user_default("Company")
	job.price_list = label_price_list()
	for row in rows:
		job.append("items", row)
	job.insert()
	return job


# ---------------------------------------------------------------- hooks


def on_inward_submit(doc, method=None):
	if not cint(get_settings().auto_label_job) or doc.doctype == "Stock Reconciliation":
		return
	if doc.doctype == "Purchase Invoice" and not doc.update_stock:
		return
	if doc.get("is_return"):
		return
	try:
		_doc, rows = rows_from_document(doc.doctype, doc.name)
		if rows:
			make_job(rows, doc.doctype, doc.name, doc.company)
	except frappe.ValidationError:
		# labels are a convenience; never block a stock receipt over them
		frappe.clear_last_message()
		frappe.log_error(title=f"Label job for {doc.doctype} {doc.name}")


# ---------------------------------------------------------------- API


def _check_create():
	if not frappe.has_permission("Label Print Job", "create"):
		frappe.throw(_("Not permitted to create label print jobs."), frappe.PermissionError)


@frappe.whitelist()
def make_from_document(source_doctype, source_name):
	_check_create()
	if source_doctype not in INWARD_DOCTYPES:
		frappe.throw(_("Labels can be made from {0}.").format(", ".join(INWARD_DOCTYPES)))
	frappe.has_permission(source_doctype, "read", source_name, throw=True)
	existing = frappe.db.get_value("Label Print Job",
		{"source_doctype": source_doctype, "source_name": source_name, "status": "Draft"}, "name")
	if existing:
		return existing
	doc, rows = rows_from_document(source_doctype, source_name)
	return make_job(rows, source_doctype, source_name, doc.get("company")).name


@frappe.whitelist()
def make_from_items(items, copies=1):
	_check_create()
	items = json.loads(items) if isinstance(items, str) else items
	rows = []
	for item_code in items:
		if frappe.get_cached_value("Item", item_code, "has_variants"):
			for variant in frappe.get_all("Item", filters={"variant_of": item_code, "disabled": 0}, pluck="name"):
				rows.append({"item_code": variant, "copies": cint(copies) or 1})
		else:
			rows.append({"item_code": item_code, "copies": cint(copies) or 1})
	return make_job(rows).name


@frappe.whitelist()
def make_from_serials(serials):
	_check_create()
	serials = json.loads(serials) if isinstance(serials, str) else serials
	rows = []
	for serial_no in serials:
		rows += rows_for_unit(frappe.db.get_value("Serial No", serial_no, "item_code"), serial_no)
	return make_job(rows).name


@frappe.whitelist()
def add_rows(job, source_doctype=None, source_name=None, warehouse=None, item_group=None, item_code=None):
	"""Label Print Job form: append rows from a document or from current stock."""
	doc = frappe.get_doc("Label Print Job", job)
	doc.check_permission("write")
	if source_doctype and source_name:
		frappe.has_permission(source_doctype, "read", source_name, throw=True)
		_src, rows = rows_from_document(source_doctype, source_name)
	elif warehouse:
		rows = rows_from_stock(warehouse, item_group, item_code)
	else:
		frappe.throw(_("Pick a document or a warehouse."))
	for row in rows:
		doc.append("items", row)
	doc.save()
	return len(rows)


@frappe.whitelist()
def mark_printed(job):
	doc = frappe.get_doc("Label Print Job", job)
	doc.check_permission("write")
	doc.db_set({"status": "Printed", "printed_count": cint(doc.printed_count) + 1})
	return doc.printed_count
