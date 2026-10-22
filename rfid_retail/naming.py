# Copyright (c) 2026, MicroNXT Solutions Pvt. Ltd. and contributors
# For license information, please see license.txt

"""Keep naming-series counters ahead of the names already in use.

Frappe takes the next number from `tabSeries`. If that row is lost or reset
(series reset by hand, partial restore, ...), the counter restarts at 1 and
every insert fails with "<name> already exists" until someone fixes the
counter. For RFID doctypes that stops billing at the counter (RFID Basket),
so before naming we lift the counter to the highest number already taken.
"""

import frappe
from frappe.model.naming import parse_naming_series
from frappe.utils import cint


def sync_series(doc, method=None):
	autoname = frappe.get_meta(doc.doctype).autoname or ""
	if ".#" not in autoname:
		return

	series, hashes = autoname.rsplit(".", 1)
	if not hashes or set(hashes) != {"#"}:
		return
	prefix = parse_naming_series(series, doc=doc)
	digits = len(hashes)

	# highest number in use: names shaped exactly <prefix><digits>
	last = frappe.db.sql(
		f"""select max(cast(substring(name, %(start)s, {digits}) as unsigned))
		from `tab{doc.doctype}`
		where name like %(like)s and char_length(name) >= %(length)s""",
		{"start": len(prefix) + 1, "like": f"{prefix}%", "length": len(prefix) + digits},
	)[0][0]
	last = cint(last)
	if not last:
		return

	current = frappe.db.sql("select current from `tabSeries` where name=%s for update", prefix)
	if not current:
		frappe.db.sql("insert into `tabSeries` (name, current) values (%s, %s)", (prefix, last))
	elif cint(current[0][0]) < last:
		frappe.db.sql("update `tabSeries` set current=%s where name=%s", (last, prefix))
