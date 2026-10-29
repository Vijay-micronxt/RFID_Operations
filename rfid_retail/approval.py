"""Approval of a sale whose RFID tag will not read, by PIN.

The counter raises an RFID Approval Request. Every approver (users holding a role
mapped to the Unread Sale Approval station) gets their own one-time PIN in the
system notifications and, when switched on, by WhatsApp. The approver tells the
cashier the PIN, or approves with one click on the request. A PIN works once, for
that request only (basket, item, quantity), until it expires; too many wrong PINs
lock the request.
"""

import hashlib
import hmac
import secrets

import frappe
from frappe import _
from frappe.utils import add_to_date, cint, get_datetime, now_datetime

from rfid_retail.settings import get as setting, has_station

STATION = "Unread Sale Approval"
MAX_WRONG_PINS = 5


def approvers(exclude=None):
	"""Enabled users holding a role mapped to the station (System Manager alone does not count)."""
	roles = {r.role for r in frappe.get_cached_doc("RFID Settings").station_roles if r.station == STATION}
	if not roles:
		return []
	users = frappe.get_all("Has Role", {"role": ["in", list(roles)], "parenttype": "User"}, pluck="parent", distinct=True)
	skip = {"Administrator", "Guest", exclude}
	return [u for u in frappe.get_all("User", {"name": ["in", users or [""]], "enabled": 1}, pluck="name") if u not in skip]


def _hash(request, pin):
	key = (frappe.local.conf.get("encryption_key") or frappe.local.site).encode()
	return hmac.new(key, f"{request}:{pin}".encode(), hashlib.sha256).hexdigest()


def create_request(basket, item_code, qty, reason, note, warehouse):
	"""New pending request with a PIN per approver; notifies them. Returns the request doc."""
	people = approvers(exclude=frappe.session.user)
	if not people:
		frappe.throw(_("Nobody can approve this yet. Give a manager the RFID Approver role (RFID Settings > {0} station).").format(
			STATION), title=_("No approver"))
	req = frappe.get_doc({
		"doctype": "RFID Approval Request",
		"status": "Pending",
		"item_code": item_code,
		"qty": qty,
		"reason": reason,
		"note": note,
		"requested_by": frappe.session.user,
		"pos_profile": basket.pos_profile,
		"warehouse": warehouse,
		"basket": basket.name,
		"expires_at": add_to_date(now_datetime(), minutes=max(cint(setting("approval_pin_minutes")) or 10, 1)),
	})
	req.insert(ignore_permissions=True)
	pins = {}
	for user in people:
		pin = f"{secrets.randbelow(1000000):06d}"
		while pin in pins.values():
			pin = f"{secrets.randbelow(1000000):06d}"
		pins[user] = pin
		req.append("approvers", {"approver": user, "pin_hash": _hash(req.name, pin),
			"mobile": frappe.db.get_value("User", user, "mobile_no") or frappe.db.get_value("User", user, "phone")})
	req.save(ignore_permissions=True)
	_notify(req, pins)
	return req


def _message(req, pin):
	return _("{0} at {1} asks to sell {2} x {3} without reading its RFID tag ({4}).{5} PIN: {6} (valid till {7}).").format(
		frappe.utils.get_fullname(req.requested_by), req.pos_profile, req.qty, req.item_name or req.item_code, req.reason,
		f" {_('Note')}: {req.note}." if req.note else "", pin, get_datetime(req.expires_at).strftime("%H:%M"))


def _notify(req, pins):
	"""Bell notification now; WhatsApp after the request is committed (a slow API never holds the counter)."""
	for row in req.approvers:
		frappe.get_doc({
			"doctype": "Notification Log",
			"for_user": row.approver,
			"from_user": req.requested_by,
			"type": "Alert",
			"document_type": req.doctype,
			"document_name": req.name,
			"subject": _message(req, pins[row.approver]),
		}).insert(ignore_permissions=True)
		row.db_set("notified", 1)
	if cint(setting("approval_whatsapp")):
		frappe.enqueue("rfid_retail.approval.send_whatsapp", queue="short", enqueue_after_commit=True,
			request=req.name, messages={r.approver: _message(req, pins[r.approver]) for r in req.approvers})


def _mobile(number):
	digits = "".join(c for c in str(number or "") if c.isdigit())
	if len(digits) == 11 and digits.startswith("0"):
		digits = digits[1:]
	if len(digits) == 10:
		digits = "91" + digits
	return digits if len(digits) >= 11 else None


def send_whatsapp(request, messages):
	"""Background job: one WhatsApp per approver. Failures are noted on the request, never raised."""
	import requests

	settings = frappe.get_single("RFID Settings")
	test = settings.whatsapp_test_mobile
	for row in frappe.get_all("RFID Approval Request Approver", {"parent": request}, ["name", "approver", "mobile"]):
		to = _mobile(test or row.mobile)
		if not to:
			status = _("No mobile number on the user")
		else:
			try:
				resp = requests.post(settings.whatsapp_api_url, timeout=15, data={
					"senderId": settings.whatsapp_sender_id,
					"authToken": settings.get_password("whatsapp_auth_token", raise_exception=False),
					"receiverId": to,
					"messageText": messages.get(row.approver, ""),
				})
				body = resp.json() if resp.ok else {}
				first = body[0] if isinstance(body, list) and body else body
				ok = isinstance(first, dict) and (first.get("success") or first.get("status") in (
					"success", "accepted", "delivered") or first.get("result") == "success")
				status = (_("Sent to {0}") if ok else _("Not accepted for {0}")).format(to)
				if not ok:
					frappe.log_error(title="RFID approval WhatsApp", message=f"{request} to {to}: {resp.status_code} {resp.text[:500]}")
			except Exception as e:
				status = _("Failed: {0}").format(str(e)[:80])
				frappe.log_error(title="RFID approval WhatsApp", message=f"{request} to {to}: {e}")
		frappe.db.set_value("RFID Approval Request Approver", row.name, "whatsapp", status[:140], update_modified=False)


def _load(request, for_update=True):
	req = frappe.get_doc("RFID Approval Request", request, for_update=for_update)
	if req.status == "Pending" and get_datetime(req.expires_at) < now_datetime():
		req.db_set("status", "Expired")
	return req


def _grant(req, approver, via):
	"""Approved: the pieces are allowed on the bill's basket."""
	from rfid_retail.api.pos import add_unread_approval

	if frappe.db.get_value("RFID Basket", req.basket, "status") != "Open":
		req.db_set("status", "Expired")
		frappe.throw(_("The bill of request {0} is no longer open.").format(req.name))
	add_unread_approval(req.basket, req.item_code, req.qty, req.reason, req.note, approver, request=req.name)
	req.db_set({"status": "Approved", "approved_by": approver, "approved_via": via, "decided_at": now_datetime()})


def check_pin(request, pin):
	"""Cashier types the PIN an approver gave them. Returns (request, error): a wrong PIN is an answer,
	not an exception, so the count of wrong tries is saved with the request."""
	req = _load(request)
	if req.requested_by != frappe.session.user and not has_station("Manager Override"):
		frappe.throw(_("Request {0} belongs to {1}.").format(req.name, req.requested_by), frappe.PermissionError)
	if req.status == "Approved":
		return req, None
	if req.status != "Pending":
		return req, _("This request is {0}. Ask again.").format(_(req.status))
	digits = "".join(c for c in str(pin or "") if c.isdigit())
	match = next((r for r in req.approvers if digits and hmac.compare_digest(r.pin_hash or "", _hash(req.name, digits))), None)
	if not match:
		wrong = cint(req.wrong_pins) + 1
		req.db_set({"wrong_pins": wrong, **({"status": "Locked"} if wrong >= MAX_WRONG_PINS else {})})
		if wrong >= MAX_WRONG_PINS:
			return req, _("Too many wrong PINs. This request is locked; ask again.")
		return req, _("Wrong PIN. {0} tries left.").format(MAX_WRONG_PINS - wrong)
	_grant(req, match.approver, "PIN")
	return req, None


@frappe.whitelist(methods=["POST"])
def approve_request(request):
	"""One click from the notification / request form."""
	from rfid_retail.settings import require_station

	require_station(STATION)
	req = _load(request)
	if req.status != "Pending":
		frappe.throw(_("Request {0} is {1}.").format(req.name, _(req.status)))
	if req.requested_by == frappe.session.user and frappe.session.user != "Administrator":
		frappe.throw(_("You cannot approve your own request."))
	_grant(req, frappe.session.user, "Button")
	return req.status


@frappe.whitelist(methods=["POST"])
def reject_request(request, note=None):
	from rfid_retail.settings import require_station

	require_station(STATION)
	req = _load(request)
	if req.status != "Pending":
		frappe.throw(_("Request {0} is {1}.").format(req.name, _(req.status)))
	req.db_set({"status": "Rejected", "approved_by": frappe.session.user, "approved_via": "Button",
		"decided_at": now_datetime()})
	if note:
		req.add_comment("Comment", note)
	return req.status


def status(request):
	req = _load(request, for_update=False)
	return {"request": req.name, "status": req.status, "approved_by": req.approved_by,
		"approved_by_name": req.approved_by and frappe.utils.get_fullname(req.approved_by),
		"expires_at": req.expires_at}


def is_approver(user=None):
	return has_station(STATION, user)

