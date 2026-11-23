"""Login for the handheld app.

Checks the password exactly as Frappe's own login does (same lockout after repeated failures, same
IP and login-hour rules) and hands back the user's API key and a NEW API secret. The app sends them
as `Authorization: token <key>:<secret>` on every later call, so it holds no session.

The secret lives from one login to the next logout:
- every login creates a fresh secret, so the one from an earlier login stops working;
- logout cancels the secret on the server too.

That also means a second device logging in as the same user signs the first one out, and anything
else using this user's API key (Postman, an integration) stops working at each app login. Use
personal accounts for the app, never a service account.
"""

import frappe
from frappe import _
from frappe.auth import validate_ip_address
from frappe.core.doctype.activity_log.activity_log import add_authentication_log
from frappe.rate_limiter import rate_limit
from frappe.twofactor import should_run_2fa
from frappe.utils.password import set_encrypted_password

from rfid_retail.settings import allowed_stations, is_enabled


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=30, seconds=60)
def login(usr, pwd):
	"""Returns {user, full_name, api_key, api_secret, stations}. Fails with 401 on a wrong password."""
	if frappe.get_system_settings("disable_user_pass_login"):
		frappe.throw(_("Login with username and password is not allowed."), frappe.AuthenticationError)

	manager = frappe.local.login_manager
	manager.authenticate(user=usr, pwd=pwd)  # wrong password / locked / disabled: raises, and is logged
	user = manager.user

	if user in frappe.STANDARD_USERS:
		frappe.throw(_("Log in with a personal account, not {0}.").format(user), frappe.AuthenticationError)
	if frappe.db.get_value("User", user, "user_type") != "System User":
		frappe.throw(_("This account cannot use the app."), frappe.AuthenticationError)
	if manager.force_user_to_reset_password():
		frappe.throw(_("Your password has expired. Change it on the website first."), frappe.AuthenticationError)
	if should_run_2fa(user):
		# The app has no screen for the one-time code, and skipping it would bypass the second factor.
		frappe.throw(_("Two-factor login is not supported in the app."), frappe.AuthenticationError)
	validate_ip_address(user)
	manager.validate_hour()

	api_key, api_secret = _rotate_keys(user)
	add_authentication_log(_("Logged in from the RFID app"), user)
	return {
		"user": user,
		"full_name": frappe.db.get_value("User", user, "full_name"),
		"api_key": api_key,
		"api_secret": api_secret,
		"stations": allowed_stations(user) if is_enabled() else [],
	}


@frappe.whitelist(methods=["POST"])
def logout():
	"""Cancels the caller's API secret (they must send it, so only the current login can end itself).

	A secret that was already replaced by a later login never reaches here: that call is refused with 401
	before this runs, which is right, because it no longer works anyway.
	"""
	user = frappe.session.user
	if user in frappe.STANDARD_USERS:
		return {"ok": False}
	_rotate_keys(user)  # a new secret nobody is given: the old one is dead
	add_authentication_log(_("Logged out from the RFID app"), user, operation="Logout")
	return {"ok": True}


def _rotate_keys(user):
	"""The user's API key (created once, then kept) and a brand-new secret."""
	api_key = frappe.db.get_value("User", user, "api_key")
	if not api_key:
		api_key = frappe.generate_hash(length=15)
		frappe.db.set_value("User", user, "api_key", api_key, update_modified=False)
	secret = frappe.generate_hash(length=15)
	# Stored encrypted like Frappe's own Password fields, without saving (and re-validating) the whole User.
	set_encrypted_password("User", user, secret, fieldname="api_secret")
	return api_key, secret
