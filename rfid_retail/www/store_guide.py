import frappe

no_cache = 1


def get_context(context):
	# Same audience as /rfid and /pos: store staff with a login.
	if frappe.session.user == "Guest":
		frappe.local.flags.redirect_location = "/login?redirect-to=/store-guide"
		raise frappe.Redirect
	return context
