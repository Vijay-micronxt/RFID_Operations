"""RFID identity model for garment retail.

Hierarchy:
	Item variant (SKU)  ->  Serial No (one physical sellable unit)
	                    ->  Garment Component (one physical piece of a set)
	                    ->  RFID Tag Assignment (reusable tag <-> serial/component, with history)

Only the Serial No carries stock and valuation. Components exist for physical
traceability and never add saleable quantity.
"""

import frappe
from frappe import _
from frappe.utils import cint, now_datetime

# Serial No.custom_rfid_status
PENDING = "Pending Mapping"
READY = "Ready"
SOLD = "Sold"
RETURNED = "Returned - Hold"
HOLD = "Hold"

# RFID Tag.status
TAG_AVAILABLE = "Available"
TAG_ASSIGNED = "Assigned"
TAG_SOLD = "Sold - Pending Detach"
TAG_DAMAGED = "Damaged"
TAG_RETIRED = "Retired"


# ---------------------------------------------------------------- lookups


def is_rfid_item(item_code):
	return bool(item_code and cint(frappe.get_cached_value("Item", item_code, "custom_rfid_tracked")))


def get_set_definition(item_code):
	"""Active set definition for a variant, falling back to its template."""
	if not item_code:
		return None
	for candidate in (item_code, frappe.get_cached_value("Item", item_code, "variant_of")):
		if candidate and frappe.db.get_value(
			"Garment Set Definition", {"item": candidate, "is_active": 1}, "name"
		):
			return frappe.get_cached_doc("Garment Set Definition", candidate)
	return None


def get_components(serial_no):
	return frappe.get_all(
		"Garment Component",
		filters={"serial_no": serial_no},
		fields=["name", "role", "role_code", "piece_no", "status", "rfid_tag"],
		order_by="name asc",
	)


def serial_info(serial_no):
	return frappe.db.get_value(
		"Serial No",
		serial_no,
		[
			"name", "item_code", "item_name", "warehouse", "status", "custom_rfid_status",
			"custom_is_garment_set", "custom_rfid_basket", "custom_sales_invoice",
		],
		as_dict=True,
	)


# ---------------------------------------------------------------- setup of new units


def ensure_serial_setup(serial_no):
	"""Idempotent: flag an inward serial for mapping and create its component records."""
	sn = serial_info(serial_no)
	if not sn or not is_rfid_item(sn.item_code):
		return
	definition = get_set_definition(sn.item_code)
	updates = {}
	if not sn.custom_rfid_status:
		updates["custom_rfid_status"] = PENDING
	if definition and not sn.custom_is_garment_set:
		updates["custom_is_garment_set"] = 1
	if updates:
		frappe.db.set_value("Serial No", serial_no, updates, update_modified=False)
	if not definition:
		return
	for row in definition.components:
		qty = max(cint(row.qty), 1)
		for piece in range(1, qty + 1):
			name = f"{serial_no}-{row.role_code}" + (str(piece) if qty > 1 else "")
			if frappe.db.exists("Garment Component", name):
				continue
			frappe.get_doc(
				{
					"doctype": "Garment Component",
					"serial_no": serial_no,
					"item_code": sn.item_code,
					"role": row.role,
					"role_code": row.role_code,
					"piece_no": piece,
					"status": "Pending Tag",
				}
			).insert(ignore_permissions=True, set_name=name)


def refresh_serial_readiness(serial_no):
	"""Pending Mapping -> Ready once every required identity carries an active tag."""
	sn = serial_info(serial_no)
	if sn.custom_rfid_status not in (PENDING, RETURNED, READY):
		return sn.custom_rfid_status
	if cint(sn.custom_is_garment_set):
		comps = get_components(serial_no)
		ready = bool(comps) and all(c.rfid_tag and c.status == "Tagged" for c in comps)
	else:
		ready = bool(active_assignment_for(serial_no=serial_no))
	status = READY if ready else (sn.custom_rfid_status if sn.custom_rfid_status != READY else PENDING)
	if status != sn.custom_rfid_status:
		frappe.db.set_value("Serial No", serial_no, "custom_rfid_status", status, update_modified=False)
	return status


def active_assignment_for(serial_no=None, component=None):
	filters = {"status": "Active"}
	if component:
		filters["component"] = component
	else:
		filters.update(serial_no=serial_no, component=("is", "not set"))
	return frappe.db.get_value("RFID Tag Assignment", filters, "name")


# ---------------------------------------------------------------- tag lifecycle


def get_or_create_tag(epc):
	epc = normalize(epc)
	if not frappe.db.exists("RFID Tag", epc):
		frappe.get_doc({"doctype": "RFID Tag", "epc": epc, "status": TAG_AVAILABLE}).insert(
			ignore_permissions=True
		)
	return frappe.get_doc("RFID Tag", epc, for_update=True)


def assign_tag(epc, serial_no, component=None, device=None, remarks=None):
	code = normalize(epc)
	if frappe.db.exists("Serial No", code) or frappe.db.exists("Garment Component", code):
		frappe.throw(_("{0} is a garment label, not an RFID tag. Read the tag itself.").format(code))
	tag = get_or_create_tag(epc)
	if tag.status in (TAG_DAMAGED, TAG_RETIRED):
		log_exception_on_failure("Retired Tag Read", _("Tag {0} is {1} and cannot be mapped.").format(tag.name, tag.status),
			severity="Medium", epc=tag.name, device=device)
		frappe.throw(_("Tag {0} is {1}; use a working tag.").format(tag.name, tag.status))
	if tag.status != TAG_AVAILABLE or tag.current_assignment:
		log_exception_on_failure("Duplicate Assignment",
			_("Tag {0} is already {1} to {2}.").format(tag.name, tag.status, tag.component or tag.serial_no),
			severity="High", epc=tag.name, serial_no=tag.serial_no, component=tag.component, device=device)
		frappe.throw(_("Tag {0} is already mapped to {1}. Detach or retire it first.").format(
			tag.name, tag.component or tag.serial_no))

	sn = serial_info(serial_no)
	if not sn:
		frappe.throw(_("Serial No {0} not found").format(serial_no))
	ensure_serial_setup(serial_no)
	sn = serial_info(serial_no)
	if sn.custom_rfid_status == SOLD:
		frappe.throw(_("{0} is already sold.").format(serial_no))
	if sn.custom_rfid_status == RETURNED:
		frappe.throw(_("{0} is a customer return. Check its pieces and accept the return before tagging.").format(serial_no))

	role = None
	if cint(sn.custom_is_garment_set):
		if not component:
			frappe.throw(_("{0} is a set. Pick the component (Jacket/Trouser/...) this tag goes on.").format(serial_no))
		comp = frappe.db.get_value("Garment Component", component, ["serial_no", "role", "rfid_tag"], as_dict=True)
		if not comp or comp.serial_no != serial_no:
			frappe.throw(_("Component {0} does not belong to {1}").format(component, serial_no))
		if comp.rfid_tag:
			frappe.throw(_("{0} already carries tag {1}. Use Replace Tag instead.").format(component, comp.rfid_tag))
		role = comp.role
	else:
		component = None
		if active_assignment_for(serial_no=serial_no):
			frappe.throw(_("{0} already has an active tag. Use Replace Tag instead.").format(serial_no))

	asg = frappe.get_doc(
		{
			"doctype": "RFID Tag Assignment",
			"tag": tag.name,
			"status": "Active",
			"item_code": sn.item_code,
			"serial_no": serial_no,
			"component": component,
			"role": role,
			"assigned_on": now_datetime(),
			"assigned_by": frappe.session.user,
			"device": device,
			"remarks": remarks,
		}
	).insert(ignore_permissions=True)

	tag.update(
		{
			"status": TAG_ASSIGNED,
			"item_code": sn.item_code,
			"serial_no": serial_no,
			"component": component,
			"current_assignment": asg.name,
		}
	)
	tag.save(ignore_permissions=True)
	if component:
		frappe.db.set_value("Garment Component", component, {"rfid_tag": tag.name, "status": "Tagged"})
	refresh_serial_readiness(serial_no)
	return asg.name


def close_assignment(epc, reason, new_tag_status=TAG_AVAILABLE, remarks=None):
	"""End the tag's current assignment. History rows are never deleted or overwritten."""
	tag = frappe.get_doc("RFID Tag", normalize(epc), for_update=True)
	if tag.current_assignment:
		frappe.db.set_value(
			"RFID Tag Assignment",
			tag.current_assignment,
			{
				"status": "Retired" if new_tag_status in (TAG_DAMAGED, TAG_RETIRED) else "Closed",
				"closed_on": now_datetime(),
				"closed_by": frappe.session.user,
				"close_reason": reason,
				"remarks": remarks,
			},
		)
	if tag.component and frappe.db.get_value("Garment Component", tag.component, "rfid_tag") == tag.name:
		frappe.db.set_value("Garment Component", tag.component, "rfid_tag", None)
	tag.update(
		{"status": new_tag_status, "item_code": None, "serial_no": None, "component": None, "current_assignment": None}
	)
	tag.save(ignore_permissions=True)
	return tag


# ---------------------------------------------------------------- resolution


def normalize(code):
	return (code or "").strip().upper()


def resolve_code(code):
	"""Turn a tag read or a scanned label into an identity.

	Returns dict(kind, code, serial_no, component, role, item_code, tag_status, source_label).
	kind: tag | component | serial | unmapped | unknown
	"""
	code = normalize(code)
	out = frappe._dict(code=code, kind="unknown", serial_no=None, component=None, role=None,
		item_code=None, tag_status=None)
	if not code:
		return out
	tag = frappe.db.get_value("RFID Tag", code, ["status", "serial_no", "component", "item_code"], as_dict=True)
	if tag:
		out.tag_status = tag.status
		if tag.serial_no:
			out.update(kind="tag", serial_no=tag.serial_no, component=tag.component, item_code=tag.item_code)
		else:
			out.kind = "unmapped"
	elif frappe.db.exists("Garment Component", code):
		comp = frappe.db.get_value("Garment Component", code, ["serial_no", "item_code"], as_dict=True)
		out.update(kind="component", serial_no=comp.serial_no, component=code, item_code=comp.item_code)
	elif frappe.db.exists("Serial No", code):
		out.update(kind="serial", serial_no=code, item_code=frappe.db.get_value("Serial No", code, "item_code"))
	if out.component:
		out.role = frappe.db.get_value("Garment Component", out.component, "role")
	return out


# ---------------------------------------------------------------- exceptions


def log_exception(event_type, message, severity="Medium", epc=None, serial_no=None, component=None,
	device=None, pos_profile=None, reference_doctype=None, reference_name=None, actor=None):
	"""Create an RFID Exception, folding repeats of the same open incident into one row."""
	key = {"event_type": event_type, "status": ("!=", "Resolved")}
	for field, value in (("epc", epc), ("serial_no", serial_no), ("component", component),
		("device", device), ("reference_name", reference_name)):
		key[field] = value or ("is", "not set")
	if reference_doctype and not (reference_name and frappe.db.exists(reference_doctype, reference_name)):
		# e.g. an invoice whose save was rolled back: keep its name in the text, not as a broken link
		message = f"{message} ({reference_doctype} {reference_name or 'unsaved'} was not saved)"
		reference_doctype = reference_name = None
		key["reference_name"] = ("is", "not set")
	now = now_datetime()
	existing = frappe.db.get_value("RFID Exception", key, ["name", "occurrences"], as_dict=True)
	if existing:
		frappe.db.set_value("RFID Exception", existing.name,
			{"occurrences": cint(existing.occurrences) + 1, "last_seen": now, "message": message})
		return existing.name
	return frappe.get_doc(
		{
			"doctype": "RFID Exception",
			"event_type": event_type,
			"severity": severity,
			"status": "Open",
			"message": message,
			"epc": epc,
			"serial_no": serial_no,
			"component": component,
			"device": device,
			"pos_profile": pos_profile,
			"reference_doctype": reference_doctype,
			"reference_name": reference_name,
			"first_seen": now,
			"last_seen": now,
			"actor": actor or frappe.session.user,
		}
	).insert(ignore_permissions=True).name


def log_exception_on_failure(event_type, message, **kwargs):
	"""Record an exception for a request that is about to throw.

	The throw rolls the transaction back, which would erase a normal insert, so the
	row is written by a background job queued only if that rollback happens.
	"""
	kwargs.update(event_type=event_type, message=message)
	kwargs.setdefault("actor", frappe.session.user)

	def _queue():
		frappe.enqueue("rfid_retail.rfid.log_exception", queue="short", enqueue_after_commit=False, **kwargs)

	frappe.db.after_rollback.add(_queue)
