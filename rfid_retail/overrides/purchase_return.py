"""Return to supplier by reading tags.

A return made from a bill (Create > Return / Debit Note) comes with every row of the bill. With
"Scan Return Tags" the user reads the tags of the pieces going back: the return keeps only those
items, with one unit per tag, and the tags ride on the rows so submit frees them. Pieces without a
tag are added to the qty by hand afterwards.

A Debit Note whose rows came from a Purchase Receipt cannot move stock itself (ERPNext: "Stock
cannot be updated against Purchase Receipt"). For those rows the Debit Note stays a money document
and its submit makes and submits the Purchase Return against that receipt (stock and tags), linked
both ways; cancelling the Debit Note cancels that return.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt

from rfid_retail import rfid
from rfid_retail.settings import is_enabled, require_enabled

RETURN_DOCTYPES = ("Purchase Invoice", "Purchase Receipt")
# field on a return row that points to the row of the original bill / receipt
SOURCE_ROW_FIELDS = ("purchase_invoice_item", "purchase_receipt_item", "pr_detail")


def _units(row):
	return abs(flt(row.get("qty"))) * (flt(row.get("conversion_factor")) or 1)


def _tag_source_row(tag):
	if not tag.current_assignment:
		return None, None, None
	src = frappe.db.get_value("RFID Tag Assignment", tag.current_assignment,
		["source_doctype", "source_name", "source_row"], as_dict=True) or {}
	return src.get("source_doctype"), src.get("source_name"), src.get("source_row")


def _source_rate(source_doctype, source_row, item_code):
	"""Purchase rate of one stock unit: from the row that brought the piece in, else the item's last rate."""
	if source_doctype in ("Purchase Receipt", "Purchase Invoice") and source_row:
		r = frappe.db.get_value(source_doctype + " Item", source_row, ["rate", "conversion_factor"], as_dict=True)
		if r and flt(r.rate):
			return flt(r.rate) / (flt(r.conversion_factor) or 1)
	if source_doctype == "Stock Entry" and source_row:
		rate = frappe.db.get_value("Stock Entry Detail", source_row, "basic_rate")
		if flt(rate):
			return flt(rate)
	return flt(frappe.db.get_value("Item", item_code, "last_purchase_rate")) or flt(
		frappe.db.get_value("Item", item_code, "valuation_rate"))


def _receipt_units_left(pr_detail):
	got = flt(frappe.db.get_value("Purchase Receipt Item", pr_detail, "stock_qty"))
	back = frappe.db.sql("""select ifnull(sum(abs(i.stock_qty)), 0) from `tabPurchase Receipt Item` i
		join `tabPurchase Receipt` p on p.name = i.parent
		where p.docstatus = 1 and p.is_return = 1 and i.purchase_receipt_item = %s""", pr_detail)[0][0]
	return max(int(round(got - flt(back))), 0)


@frappe.whitelist()
def plan_return_tags(doctype, items, codes, return_against=None, set_warehouse=None):
	"""Which rows of a draft return the tags read belong to.

	Returns one verdict per tag; when all are usable, `keep` (row -> tags), `remove` (rows nobody read a
	tag for), `add` (items read that have no row: only a return not made from a bill) and, for a
	Debit Note, whether it must move stock itself (`update_stock`)."""
	from rfid_retail.api.pos import _load

	require_enabled()
	if doctype not in RETURN_DOCTYPES:
		frappe.throw(_("Scan Return Tags works on a Purchase Invoice or Purchase Receipt return."))
	frappe.has_permission(doctype, "write", throw=True)
	items = [frappe._dict(r) for r in (_load(items) if isinstance(items, str) else items or []) if r.get("item_code")]
	codes = list(dict.fromkeys(rfid.normalize(c) for c in (_load(codes) if isinstance(codes, str) else codes) if c))
	if not codes:
		frappe.throw(_("Read the tags of the pieces going back first."))

	room = {}
	for r in items:
		linked = any(r.get(f) for f in SOURCE_ROW_FIELDS)
		# rows from the bill: no more than the bill still has to give back; own rows: no limit
		room[r.name] = int(round(_units(r))) if linked and return_against else None
		if doctype == "Purchase Invoice" and r.get("pr_detail"):
			# the stock came on a Purchase Receipt: no more than that receipt still holds
			left = _receipt_units_left(r.pr_detail)
			room[r.name] = left if room[r.name] is None else min(room[r.name], left)
	keep = {}
	add = {}
	verdicts = []
	for code in codes:
		tag = frappe.db.get_value("RFID Tag", code, ["name", "status", "item_code", "serial_no", "component",
			"warehouse", "current_assignment", "basket"], as_dict=True)
		v = {"code": code, "ok": False}
		verdicts.append(v)
		if not tag:
			v["message"] = _("Unknown tag - not on any piece")
			continue
		if tag.status != rfid.TAG_ASSIGNED or not tag.item_code:
			v["message"] = _("Not on a piece in stock ({0})").format(tag.status)
			continue
		item_name = frappe.get_cached_value("Item", tag.item_code, "item_name")
		v.update(item_code=tag.item_code, item_name=item_name, warehouse=tag.warehouse)
		if tag.serial_no or tag.component or rfid.get_set_definition(tag.item_code):
			v["message"] = _("{0} is a serial / set item: return it with its serial numbers, not by tag").format(
				tag.item_code)
			continue
		if tag.basket:
			v["message"] = _("On the counter in basket {0}. Take it out of the basket first.").format(tag.basket)
			continue
		src_doctype, src_name, src_row = _tag_source_row(tag)
		rows = [r for r in items if r.item_code == tag.item_code]
		same_wh = [r for r in rows if not r.get("warehouse") or r.warehouse == tag.warehouse]
		if rows and not same_wh:
			v["message"] = _("This piece is in {0}; the rows of {1} send stock from {2}").format(
				tag.warehouse, tag.item_code, ", ".join({r.warehouse for r in rows}))
			continue
		from_source = [r for r in same_wh if src_row and src_row in {r.get(f) for f in SOURCE_ROW_FIELDS}]
		row = next((r for r in from_source + same_wh if room[r.name] is None or room[r.name] > 0), None)
		if row:
			if room[row.name] is not None:
				room[row.name] -= 1
			keep.setdefault(row.name, []).append(code)
			v.update(ok=True, row=row.name, idx=row.get("idx"),
				message=_("Row {0}: {1}").format(row.get("idx"), item_name or tag.item_code))
			continue
		if same_wh:
			v["message"] = _("More pieces of {0} than this bill has left to return. Already returned before? "
				"Then this tag was left on: free it with RFID Tags on that return.").format(item_name or tag.item_code)
			continue
		if return_against:
			v["message"] = _("{0} is not on {1}").format(item_name or tag.item_code, return_against)
			continue
		if set_warehouse and tag.warehouse != set_warehouse:
			v["message"] = _("This piece is in {0}, not {1}").format(tag.warehouse, set_warehouse)
			continue
		key = (tag.item_code, tag.warehouse)
		if key not in add:
			add[key] = {"item_code": tag.item_code, "item_name": item_name, "warehouse": tag.warehouse, "tags": [],
				"rate": _source_rate(src_doctype, src_row, tag.item_code)}
		add[key]["tags"].append(code)
		v.update(ok=True, message=_("New row: {0}").format(item_name or tag.item_code))

	out = {"codes": verdicts, "ok": all(v["ok"] for v in verdicts)}
	if not out["ok"]:
		return out
	kept = [r for r in items if r.name in keep]
	out.update(keep=keep, remove=[r.name for r in items if r.name not in keep], add=list(add.values()))
	if doctype == "Purchase Invoice":
		receipts = sorted({r.purchase_receipt for r in kept if r.get("purchase_receipt")})
		out["receipts"] = receipts
		if receipts and (add or any(not r.get("purchase_receipt") for r in kept)):
			frappe.throw(_("Some pieces came on Purchase Receipt {0} and some did not. Make two returns.").format(
				", ".join(receipts)))
		out["update_stock"] = 0 if receipts else 1
	return out


# ---------------------------------------------------------------- Debit Note over a Purchase Receipt


def receipt_rows_with_tags(doc):
	"""Debit Note rows billed from a Purchase Receipt that carry tags: their stock goes back on that receipt."""
	if doc.doctype != "Purchase Invoice" or not cint(doc.get("is_return")) or cint(doc.get("update_stock")):
		return []
	return [r for r in doc.get("items") or [] if r.get("purchase_receipt") and r.get("pr_detail")
		and (r.get("custom_rfid_tags") or "").strip()]


def before_submit(doc, method=None):
	"""Debit Note billed from a Purchase Receipt, with tags read: return that stock on the receipt now."""
	if not is_enabled():
		return
	rows = receipt_rows_with_tags(doc)
	if not rows:
		return
	from erpnext.controllers.sales_and_purchase_return import make_return_doc

	from rfid_retail.overrides.stock import row_tags

	by_receipt = {}
	for r in rows:
		by_receipt.setdefault(r.purchase_receipt, []).append(r)
	made = []
	for receipt, pi_rows in by_receipt.items():
		if cint(frappe.db.get_value("Purchase Receipt", receipt, "is_return")):
			continue  # billed from a return already: its stock went back with it
		ret = make_return_doc("Purchase Receipt", receipt)
		want = {}
		for r in pi_rows:
			w = want.setdefault(r.pr_detail, frappe._dict(units=0, tags=[], pi_rows=[]))
			w.units += _units(r)
			w.tags += row_tags(r)
			w.pi_rows.append(r)
		ret.items = [i for i in ret.items if i.purchase_receipt_item in want]
		for i in ret.items:
			w = want[i.purchase_receipt_item]
			cf = flt(i.conversion_factor) or 1
			if w.units > _units(i) + 0.001:
				frappe.throw(_("{0}: {1} pieces to return but receipt {2} has only {3} left").format(
					i.item_code, w.units, receipt, _units(i)))
			i.qty = i.received_qty = -w.units / cf
			i.stock_qty = i.received_stock_qty = -w.units
			i.rejected_qty = 0
			i.custom_rfid_tags = "\n".join(w.tags)
		ret.set_posting_time = 1
		ret.posting_date, ret.posting_time = doc.posting_date, doc.posting_time
		ret.custom_rfid_debit_note = doc.name
		ret.remarks = _("Stock going back for Debit Note {0}").format(doc.name)
		ret.flags.ignore_permissions = True
		ret.insert()
		ret.submit()
		made.append(ret.name)
		# the Debit Note now bills this return (as ERPNext's own Debit Note from a Purchase Return does)
		by_detail = {i.purchase_receipt_item: i.name for i in ret.items}
		for i in ret.items:
			for r in want[i.purchase_receipt_item].pi_rows:
				r.purchase_receipt, r.pr_detail = ret.name, by_detail[i.purchase_receipt_item]
	if made:
		frappe.msgprint(_("Stock and RFID tags went back on Purchase Return {0}").format(", ".join(
			frappe.utils.get_link_to_form("Purchase Receipt", n) for n in made)), alert=True, indicator="green")


def on_cancel(doc, method=None):
	"""Cancelling the Debit Note cancels the Purchase Return its submit made (tags back on the pieces)."""
	if doc.doctype != "Purchase Invoice" or not frappe.get_meta("Purchase Receipt").has_field("custom_rfid_debit_note"):
		return
	for name in frappe.get_all("Purchase Receipt", {"custom_rfid_debit_note": doc.name, "docstatus": 1}, pluck="name"):
		ret = frappe.get_doc("Purchase Receipt", name)
		ret.flags.ignore_permissions = True
		ret.cancel()
		frappe.msgprint(_("Purchase Return {0} cancelled with it").format(name), alert=True)
