"""RFID identity model for garment retail.

Hierarchy:
	Item variant (SKU)  ->  Serial No (one physical sellable unit, optional)
	                    ->  Garment Component (one physical piece of a set)
	                    ->  RFID Tag Assignment (reusable tag <-> unit, one row per use of the tag)

Items with serial numbers: a tag points at one serial (or one piece of a set).
Items without serial numbers: a tag points at "one unit of this item in this
warehouse"; ERPNext keeps the quantity, the tags only have to add up to it.
Stock and valuation always stay in ERPNext. Every tag change is written to
RFID Tag Event, which is never edited.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt, now_datetime

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
TAG_RESERVED = "Reserved"
TAG_LOST = "Lost"
TAG_HOLD = "Hold"
UNUSABLE = (TAG_DAMAGED, TAG_LOST, TAG_RETIRED)
# Tags that stand for a unit still in stock
IN_STOCK_TAG = (TAG_ASSIGNED, TAG_HOLD)


# ---------------------------------------------------------------- lookups


def is_rfid_item(item_code):
	return bool(item_code and cint(frappe.get_cached_value("Item", item_code, "custom_rfid_tracked")))


def has_serial(item_code):
	return bool(item_code and cint(frappe.get_cached_value("Item", item_code, "has_serial_no")))


def is_item_level(item_code):
	"""RFID item without serial numbers: a tag stands for one unit of the item in a warehouse."""
	return is_rfid_item(item_code) and not has_serial(item_code)


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


def get_or_create_tag(epc, device=None):
	epc = normalize(epc)
	if not frappe.db.exists("RFID Tag", epc):
		frappe.get_doc({"doctype": "RFID Tag", "epc": epc, "status": TAG_AVAILABLE}).insert(
			ignore_permissions=True
		)
		log_event(epc, "Registered", new_status=TAG_AVAILABLE, device=device)
	return frappe.get_doc("RFID Tag", epc, for_update=True)


def check_tag_code(code):
	"""Refuse codes that are labels or barcodes read by mistake instead of the tag itself."""
	if frappe.db.exists("Serial No", code) or frappe.db.exists("Garment Component", code):
		frappe.throw(_("{0} is a garment label, not an RFID tag. Read the tag itself.").format(code))
	if frappe.db.exists("Item Barcode", {"barcode": code}) or frappe.db.exists("Item", code):
		frappe.throw(_("{0} is an item barcode, not an RFID tag. Read the tag itself.").format(code))


def tag_problem(tag, source=None):
	"""Why this tag cannot go on a garment now, or None. source = (doctype, name) of a draft that reserved it."""
	if tag.status in UNUSABLE:
		return _("Tag {0} is marked {1}. Use another tag.").format(tag.name, tag.status)
	if tag.status == TAG_RESERVED:
		if source and (tag.reserved_doctype, tag.reserved_for) == tuple(source[:2]):
			return None
		return _("Tag {0} is already read on draft {1} {2}.").format(tag.name, tag.reserved_doctype, tag.reserved_for)
	if tag.status != TAG_AVAILABLE or tag.current_assignment:
		return _("Tag {0} is already on {1}. Free it or use another tag.").format(
			tag.name, tag.component or tag.serial_no or tag.item_code)
	return None


def assign_tag(epc, serial_no=None, component=None, device=None, remarks=None, item_code=None,
	warehouse=None, source=None):
	"""Put a tag on a garment.

	serial_no given: the tag identifies that unit (or one piece of a set).
	Otherwise item_code + warehouse: the tag identifies one unit of a non-serial item there.
	source = (doctype, name, row) of the document the tagging was done on.
	"""
	code = normalize(epc)
	check_tag_code(code)
	tag = get_or_create_tag(code, device=device)
	problem = tag_problem(tag, source)
	if problem:
		log_event_on_failure(tag.name, "Rejected", reason=problem, item_code=item_code, serial_no=serial_no,
			device=device)
		if tag.status in UNUSABLE:
			log_exception_on_failure("Retired Tag Read", problem, severity="Medium", epc=tag.name, device=device)
		else:
			log_exception_on_failure("Duplicate Assignment", problem, severity="High", epc=tag.name,
				serial_no=tag.serial_no, component=tag.component, device=device)
		frappe.throw(problem)

	role = None
	if serial_no:
		sn = serial_info(serial_no)
		if not sn:
			frappe.throw(_("Serial No {0} not found").format(serial_no))
		ensure_serial_setup(serial_no)
		sn = serial_info(serial_no)
		if sn.custom_rfid_status == SOLD:
			frappe.throw(_("{0} is already sold.").format(serial_no))
		if sn.custom_rfid_status == RETURNED:
			frappe.throw(_("{0} is a customer return. Check its pieces and accept the return before tagging.").format(serial_no))
		item_code, warehouse = sn.item_code, sn.warehouse
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
	else:
		component = None
		if not is_rfid_item(item_code):
			frappe.throw(_("{0} is not an RFID item (Item > RFID Tracked Garment).").format(item_code))
		if has_serial(item_code):
			frappe.throw(_("{0} has serial numbers. Pick the unit (serial) the tag goes on.").format(item_code))
		if not warehouse:
			frappe.throw(_("Warehouse is needed to tag {0}.").format(item_code))
		if untagged_qty(item_code, warehouse) < 1:
			frappe.throw(_("Every unit of {0} in {1} already has a tag.").format(item_code, warehouse))

	cycle = cint(tag.cycle) + 1
	source = tuple(source or ()) + (None, None, None)
	asg = frappe.get_doc(
		{
			"doctype": "RFID Tag Assignment",
			"tag": tag.name,
			"status": "Active",
			"item_code": item_code,
			"serial_no": serial_no,
			"component": component,
			"role": role,
			"warehouse": warehouse,
			"cycle": cycle,
			"assigned_on": now_datetime(),
			"assigned_by": frappe.session.user,
			"device": device,
			"source_doctype": source[0],
			"source_name": source[1],
			"source_row": source[2],
			"remarks": remarks,
		}
	).insert(ignore_permissions=True)

	previous = tag.status
	tag.update(
		{
			"status": TAG_ASSIGNED,
			"item_code": item_code,
			"serial_no": serial_no,
			"component": component,
			"warehouse": warehouse,
			"current_assignment": asg.name,
			"cycle": cycle,
			"basket": None,
			"reserved_doctype": None,
			"reserved_for": None,
			"reserved_row": None,
		}
	)
	tag.save(ignore_permissions=True)
	log_tag_event(tag, "Assigned", previous_status=previous, reference_doctype=source[0],
		reference_name=source[1], reference_row=source[2], device=device, reason=remarks)
	if component:
		frappe.db.set_value("Garment Component", component, {"rfid_tag": tag.name, "status": "Tagged"})
	if serial_no:
		refresh_serial_readiness(serial_no)
	return asg.name


def close_assignment(epc, reason, new_tag_status=TAG_AVAILABLE, remarks=None, action="Freed",
	reference_doctype=None, reference_name=None, amount=None):
	"""End the tag's current assignment. History rows are never deleted or overwritten."""
	tag = frappe.get_doc("RFID Tag", normalize(epc), for_update=True)
	previous = tag.status
	snapshot = frappe._dict(tag.as_dict())
	if tag.current_assignment:
		frappe.db.set_value(
			"RFID Tag Assignment",
			tag.current_assignment,
			{
				"status": "Retired" if new_tag_status in UNUSABLE else "Closed",
				"closed_on": now_datetime(),
				"closed_by": frappe.session.user,
				"close_reason": reason,
				"remarks": remarks,
			},
		)
	if tag.component and frappe.db.get_value("Garment Component", tag.component, "rfid_tag") == tag.name:
		frappe.db.set_value("Garment Component", tag.component, "rfid_tag", None)
	tag.update(
		{"status": new_tag_status, "item_code": None, "serial_no": None, "component": None, "warehouse": None,
			"current_assignment": None, "basket": None}
	)
	tag.save(ignore_permissions=True)
	log_tag_event(snapshot, action, previous_status=previous, new_status=new_tag_status,
		reference_doctype=reference_doctype, reference_name=reference_name, amount=amount,
		reason=remarks or reason)
	return tag


def reserve_tag(epc, doctype, name, row, item_code):
	"""Hold a tag read on a draft inward document so nobody else uses it before submit."""
	tag = get_or_create_tag(epc)
	problem = tag_problem(tag, (doctype, name))
	if problem:
		frappe.throw(problem)
	if tag.status == TAG_RESERVED and tag.reserved_row == row:
		return
	previous = tag.status
	tag.update({"status": TAG_RESERVED, "reserved_doctype": doctype, "reserved_for": name, "reserved_row": row,
		"item_code": item_code})
	tag.flags.ignore_links = True  # a new draft is not in the database yet while it validates
	tag.save(ignore_permissions=True)
	log_tag_event(tag, "Reserved", previous_status=previous, reference_doctype=doctype, reference_name=name,
		reference_row=row)


def release_reservation(epc, reason=None):
	tag = frappe.get_doc("RFID Tag", normalize(epc), for_update=True)
	if tag.status != TAG_RESERVED:
		return
	ref = (tag.reserved_doctype, tag.reserved_for, tag.reserved_row)
	tag.update({"status": TAG_AVAILABLE, "reserved_doctype": None, "reserved_for": None, "reserved_row": None,
		"item_code": None})
	tag.save(ignore_permissions=True)
	log_tag_event(tag, "Reservation Released", previous_status=TAG_RESERVED, reference_doctype=ref[0],
		reference_name=ref[1], reference_row=ref[2], reason=reason)


def set_tag_condition(epc, status, reason, device=None):
	"""Damaged / Lost / Retired, or Found (a lost tag turned up). The garment it was on needs a new tag."""
	if not (reason or "").strip():
		frappe.throw(_("Write why (for example: torn, fell off, not found in stock count)."))
	tag = frappe.get_doc("RFID Tag", normalize(epc), for_update=True)
	if status == "Found":
		if tag.status != TAG_LOST:
			frappe.throw(_("Tag {0} is {1}, not lost.").format(tag.name, tag.status))
		tag.status = TAG_AVAILABLE
		tag.save(ignore_permissions=True)
		log_tag_event(tag, "Found", previous_status=TAG_LOST, device=device, reason=reason)
		return tag
	if status not in UNUSABLE:
		frappe.throw(_("Unknown tag condition {0}").format(status))
	serial_no, component = tag.serial_no, tag.component
	if tag.current_assignment:
		close_assignment(tag.name, {TAG_LOST: "Tag Lost"}.get(status, "Tag Damaged"), new_tag_status=status,
			remarks=reason, action=status if status != TAG_RETIRED else "Retired")
		if component:
			frappe.db.set_value("Garment Component", component, "status", "Pending Tag")
		if serial_no:
			frappe.db.set_value("Serial No", serial_no, "custom_rfid_status", PENDING, update_modified=False)
			refresh_serial_readiness(serial_no)
	else:
		if tag.status == TAG_RESERVED:
			release_reservation(tag.name, reason)
			tag.reload()
		previous = tag.status
		tag.status = status
		tag.save(ignore_permissions=True)
		log_tag_event(tag, status if status != TAG_RETIRED else "Retired", previous_status=previous,
			device=device, reason=reason)
	return frappe.get_doc("RFID Tag", tag.name)


def remove_unsold_tag(epc, reason, device=None):
	"""Take a good tag off a garment that was not sold, so it can go on another one.

	The garment stays in stock and goes back to the to-tag list. Every removal is
	written to the history and raised as an exception, so a manager can see who
	took tags off unpaid stock and why.
	"""
	if not (reason or "").strip():
		frappe.throw(_("Write why the tag is removed (for example: item not for sale, wrong item tagged)."))
	tag = frappe.get_doc("RFID Tag", normalize(epc), for_update=True)
	if tag.status == TAG_SOLD:
		frappe.throw(_("Tag {0} is on a sold item. Remove it on the Remove tags screen.").format(tag.name))
	if tag.status != TAG_ASSIGNED or not tag.current_assignment:
		frappe.throw(_("Tag {0} is {1}, not on an unsold item.").format(tag.name, tag.status))
	if tag.basket and frappe.db.get_value("RFID Basket", tag.basket, "status") == "Open":
		frappe.throw(_("Tag {0} is in an open bill at a counter ({1}). Take it off the bill first.").format(
			tag.name, tag.basket))
	serial_no, component, item_code, warehouse = tag.serial_no, tag.component, tag.item_code, tag.warehouse
	close_assignment(tag.name, "Manual", remarks=reason, action="Removed")
	if component:
		frappe.db.set_value("Garment Component", component, "status", "Pending Tag")
	if serial_no:
		frappe.db.set_value("Serial No", serial_no, "custom_rfid_status", PENDING, update_modified=False)
		refresh_serial_readiness(serial_no)
	log_exception("Tag Removed",
		_("Tag {0} removed from unsold {1} in {2} by {3}: {4}").format(
			tag.name, serial_no or item_code, warehouse or "-", frappe.session.user, reason),
		severity="Medium", epc=tag.name, serial_no=serial_no, component=component, device=device)
	return frappe.get_doc("RFID Tag", tag.name)


def untagged_qty(item_code, warehouse):
	"""Units of a non-serial RFID item in a warehouse that have no tag yet."""
	actual = flt(frappe.db.get_value("Bin", {"item_code": item_code, "warehouse": warehouse}, "actual_qty"))
	return max(cint(actual) - count_item_tags(item_code, warehouse), 0)


def count_item_tags(item_code, warehouse):
	return frappe.db.count("RFID Tag", {"item_code": item_code, "warehouse": warehouse,
		"status": ["in", IN_STOCK_TAG], "serial_no": ("is", "not set")})


def untagged_items(warehouse=None, item_code=None):
	"""Non-serial RFID items with more stock than tags, per warehouse."""
	conditions, values = "", {"statuses": IN_STOCK_TAG}
	if warehouse:
		conditions += " and b.warehouse = %(warehouse)s"
		values["warehouse"] = warehouse
	if item_code:
		conditions += " and b.item_code = %(item_code)s"
		values["item_code"] = item_code
	return frappe.db.sql(f"""
		select b.item_code, i.item_name, b.warehouse, b.actual_qty,
			(select count(*) from `tabRFID Tag` t where t.item_code = b.item_code and t.warehouse = b.warehouse
				and t.status in %(statuses)s and ifnull(t.serial_no, '') = '') as tagged
		from `tabBin` b join `tabItem` i on i.name = b.item_code
		where i.custom_rfid_tracked = 1 and i.has_serial_no = 0 and b.actual_qty > 0 {conditions}
		having b.actual_qty - tagged >= 1
		order by i.item_name, b.warehouse""", values, as_dict=True)


# ---------------------------------------------------------------- history


def log_event(tag, action, **fields):
	"""Append one row to the tag's history. Unknown codes (never registered) have no history."""
	tag = normalize(tag)
	if not tag or not frappe.db.exists("RFID Tag", tag):
		return None
	doc = frappe.get_doc({"doctype": "RFID Tag Event", "tag": tag, "action": action, "event_time": now_datetime(),
		"performed_by": fields.pop("performed_by", None) or frappe.session.user,
		**{k: v for k, v in fields.items() if v not in (None, "")}})
	# History must never block the action it records (e.g. a draft that is not saved yet),
	# so links are not validated - which also skips fetch_from.
	doc.flags.ignore_links = True
	if doc.item_code and not doc.item_name:
		doc.item_name = frappe.get_cached_value("Item", doc.item_code, "item_name")
	doc.insert(ignore_permissions=True)
	return doc.name


def log_tag_event(tag, action, **fields):
	"""log_event with the garment, warehouse and cycle taken from the tag (a doc or dict)."""
	for key, field in (("item_code", "item_code"), ("serial_no", "serial_no"), ("component", "component"),
		("warehouse", "warehouse"), ("cycle", "cycle"), ("assignment", "current_assignment")):
		fields.setdefault(key, tag.get(field))
	fields.setdefault("new_status", tag.get("status"))
	return log_event(tag.get("name") or tag.get("epc"), action, **fields)


def log_event_on_failure(tag, action, **fields):
	"""History row for a request that is about to throw (written after the rollback)."""
	fields.setdefault("performed_by", frappe.session.user)

	def _queue():
		frappe.enqueue("rfid_retail.rfid.log_event", queue="short", enqueue_after_commit=False,
			tag=tag, action=action, **fields)

	frappe.db.after_rollback.add(_queue)


# ---------------------------------------------------------------- resolution


def normalize(code):
	return (code or "").strip().upper()


def resolve_code(code):
	"""Turn a tag read or a scanned label into an identity.

	Returns dict(kind, code, serial_no, component, role, item_code, warehouse, tag_status, basket).
	kind: tag (on a garment) | component | serial | unmapped (known tag, on nothing) | unknown
	A tag on a non-serial item has kind "tag" with item_code and warehouse but no serial_no.
	"""
	code = normalize(code)
	out = frappe._dict(code=code, kind="unknown", serial_no=None, component=None, role=None,
		item_code=None, warehouse=None, tag_status=None, basket=None)
	if not code:
		return out
	tag = frappe.db.get_value("RFID Tag", code,
		["status", "serial_no", "component", "item_code", "warehouse", "current_assignment", "basket"], as_dict=True)
	if tag:
		out.tag_status = tag.status
		if tag.current_assignment and tag.item_code:
			out.update(kind="tag", serial_no=tag.serial_no, component=tag.component, item_code=tag.item_code,
				warehouse=tag.warehouse, basket=tag.basket)
			if tag.serial_no:
				out.warehouse = frappe.db.get_value("Serial No", tag.serial_no, "warehouse")
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
