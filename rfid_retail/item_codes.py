"""Pattern-built item codes and names (RFID Settings > Item Codes).

Independent of the RFID master switch: a plain retail site can use it alone.

Templates and standalone items get a code only when Item Code is left blank,
so a code typed by hand always wins. Variants are always rebuilt, because
ERPNext generates their code anyway and the store wants one scheme for both.
"""

import re

import frappe
from frappe import _
from frappe.utils import cint, cstr

from rfid_retail.settings import get_settings

TOKEN = re.compile(r"\{([^{}]+)\}")
COUNTER = re.compile(r"^#+$")


def is_enabled():
	return cint(get_settings().item_codes_enabled)


def before_insert(doc, method=None):
	if not is_enabled() or doc.get("__newname"):
		return
	settings = get_settings()
	if doc.variant_of:
		ctx = variant_context(doc, settings)
		doc.item_code = render(settings.variant_code_pattern, ctx, settings, as_code=True)
		doc.item_name = render(settings.variant_name_pattern, ctx, settings) or doc.item_name
	elif not cstr(doc.item_code).strip():
		if not cstr(doc.item_name).strip():
			frappe.throw(_("Enter an Item Name; the item code is built from it."))
		ctx = item_context(doc, settings)
		doc.item_code = render(settings.item_code_pattern, ctx, settings, as_code=True)
		doc.item_name = render(settings.item_name_pattern, ctx, settings) or doc.item_name
	else:
		return
	if not doc.item_code:
		frappe.throw(_("The item code pattern produced an empty code. Check RFID Settings > Item Codes."))
	existing = frappe.db.get_value("Item", doc.item_code, "item_name")
	if existing is not None:
		frappe.throw(
			_("Item code {0} already exists ({1}). The same supplier, name and variant already has an item.").format(
				frappe.bold(doc.item_code), existing
			),
			frappe.DuplicateEntryError,
			title=_("Duplicate item"),
		)


# ---------------------------------------------------------------- context


def supplier_of(doc):
	if doc.get("custom_supplier"):
		return doc.custom_supplier
	for row in doc.get("supplier_items") or []:
		if row.supplier:
			return row.supplier
	return None


def supplier_code(supplier, settings):
	if not supplier:
		return ""
	prefix = frappe.db.get_value("Supplier", supplier, "custom_item_code_prefix")
	if prefix:
		return prefix
	name = frappe.db.get_value("Supplier", supplier, "supplier_name") or supplier
	letters = re.sub(r"[^A-Za-z0-9]", "", name)
	return letters[: cint(settings.supplier_code_length) or 3]


def item_context(doc, settings, fallback=None):
	"""fallback: the template, for fields a variant does not carry itself."""
	fallback = fallback or frappe._dict()

	def pick(field):
		return doc.get(field) or fallback.get(field) or ""

	supplier = supplier_of(doc) or supplier_of(fallback)
	return {
		"supplier": frappe.db.get_value("Supplier", supplier, "supplier_name") if supplier else "",
		"supplier_code": supplier_code(supplier, settings),
		"item_name": pick("item_name"),
		"item_group": pick("item_group"),
		"brand": pick("brand"),
		"style_code": pick("custom_style_code"),
	}


def variant_context(doc, settings):
	template = frappe.get_cached_doc("Item", doc.variant_of)
	ctx = item_context(doc, settings, fallback=template)
	ctx["item_name"] = template.item_name
	ctx["template"] = template.name
	ctx["template_name"] = template.item_name

	order = [row.attribute for row in template.attributes]
	rows = sorted(
		(r for r in doc.attributes if r.attribute_value not in (None, "")),
		key=lambda r: order.index(r.attribute) if r.attribute in order else len(order),
	)
	abbrs, values = [], []
	for row in rows:
		value = cstr(row.attribute_value)
		abbr = attribute_abbr(row.attribute, value)
		abbrs.append(abbr)
		values.append(value)
		ctx[f"attr:{row.attribute}"] = abbr
		ctx[f"value:{row.attribute}"] = value
	ctx["attr_abbrs"] = abbrs
	ctx["attr_values"] = values
	return ctx


def attribute_abbr(attribute, value):
	abbr = frappe.db.get_value(
		"Item Attribute Value", {"parent": attribute, "attribute_value": value}, "abbr"
	)
	return abbr or value  # numeric attributes have no abbreviation


# ---------------------------------------------------------------- rendering


def render(pattern, ctx, settings, as_code=False):
	"""Fill a pattern. An empty token disappears together with the literal
	separator in front of it, so "{supplier_code}-{item_name}" without a supplier
	gives "SILK-TIE", not "-SILK-TIE"."""
	if not pattern:
		return ""
	sep = cstr(settings.code_separator) if as_code else " "
	parts = []  # (kind, text)
	pos = 0
	for m in TOKEN.finditer(pattern):
		if m.start() > pos:
			parts.append(("lit", pattern[pos : m.start()]))
		parts.append(("tok", m.group(1).strip()))
		pos = m.end()
	if pos < len(pattern):
		parts.append(("lit", pattern[pos:]))

	out = []
	for kind, text in parts:
		if kind == "lit":
			out.append(["lit", text])
			continue
		if COUNTER.match(text):
			out.append(["counter", text])
			continue
		value = token_value(text, ctx, settings, as_code, sep)
		if value:
			out.append(["val", value])
		elif out and out[-1][0] == "lit" and is_separator(out[-1][1]):
			out.pop()
		else:
			out.append(["skip", ""])

	# a leading separator left by an empty first token
	while out and out[0][0] in ("skip",):
		out.pop(0)
		if out and out[0][0] == "lit" and is_separator(out[0][1]):
			out.pop(0)

	text = ""
	for kind, value in out:
		if kind == "counter":
			from frappe.model.naming import getseries

			key = f"ITEMCODE-{text}" if text else "ITEMCODE-"
			value = getseries(key, len(value))
		text += value
	if as_code:
		text = clean_code(text, sep)
		if cint(settings.code_uppercase):
			text = text.upper()
	else:
		text = re.sub(r"\s+", " ", text).strip()
	return text[:140]


def token_value(token, ctx, settings, as_code, sep):
	value = ctx.get(token)
	if isinstance(value, list):
		value = [to_code(v, sep) if as_code else v for v in value]
		return (sep if as_code else " ").join(v for v in value if v)
	value = cstr(value).strip()
	if not value:
		return ""
	if as_code:
		if token == "item_name" and cint(settings.item_name_code_length):
			value = re.sub(r"[^A-Za-z0-9]", "", value)[: cint(settings.item_name_code_length)]
		value = to_code(value, sep)
	return value


def to_code(value, sep):
	return re.sub(r"[^A-Za-z0-9]+", sep, cstr(value)).strip(sep)


def clean_code(text, sep):
	if sep:
		text = re.sub(re.escape(sep) + "{2,}", sep, text).strip(sep)
	return text.strip(" -_/.")


def is_separator(text):
	return bool(text) and not re.search(r"[A-Za-z0-9]", text)

