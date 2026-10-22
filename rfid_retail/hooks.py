app_name = "rfid_retail"
app_title = "RFID Retail"
app_publisher = "MicroNXT Solutions Pvt. Ltd."
app_description = "RFID garment identity, set billing, tag lifecycle and stock audit for ERPNext retail"
app_email = "tushar@micronxt.com"
app_license = "mit"

# Apps
# ------------------

# required_apps = []

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "rfid_retail",
# 		"logo": "/assets/rfid_retail/logo.png",
# 		"title": "RFID Retail",
# 		"route": "/rfid_retail",
# 		"has_permission": "rfid_retail.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/rfid_retail/css/rfid_retail.css"
# app_include_js = "/assets/rfid_retail/js/rfid_retail.js"

# include js, css files in header of web template
# web_include_css = "/assets/rfid_retail/css/rfid_retail.css"
# web_include_js = "/assets/rfid_retail/js/rfid_retail.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "rfid_retail/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
# doctype_js = {"doctype" : "public/js/doctype.js"}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "rfid_retail/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "rfid_retail.utils.jinja_methods",
# 	"filters": "rfid_retail.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "rfid_retail.install.before_install"
# after_install = "rfid_retail.install.after_install"

# Uninstallation
# ------------

# before_uninstall = "rfid_retail.uninstall.before_uninstall"
# after_uninstall = "rfid_retail.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "rfid_retail.utils.before_app_install"
# after_app_install = "rfid_retail.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "rfid_retail.utils.before_app_uninstall"
# after_app_uninstall = "rfid_retail.utils.after_app_uninstall"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "rfid_retail.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# DocType Class
# ---------------
# Override standard doctype classes

# override_doctype_class = {
# 	"ToDo": "custom_app.overrides.CustomToDo"
# }

# Document Events
# ---------------
# Hook on document methods and events

# doc_events = {
# 	"*": {
# 		"on_update": "method",
# 		"on_cancel": "method",
# 		"on_trash": "method"
# 	}
# }

# Scheduled Tasks
# ---------------

# scheduler_events = {
# 	"all": [
# 		"rfid_retail.tasks.all"
# 	],
# 	"daily": [
# 		"rfid_retail.tasks.daily"
# 	],
# 	"hourly": [
# 		"rfid_retail.tasks.hourly"
# 	],
# 	"weekly": [
# 		"rfid_retail.tasks.weekly"
# 	],
# 	"monthly": [
# 		"rfid_retail.tasks.monthly"
# 	],
# }

# Testing
# -------

# before_tests = "rfid_retail.install.before_tests"

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "rfid_retail.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "rfid_retail.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["rfid_retail.utils.before_request"]
# after_request = ["rfid_retail.utils.after_request"]

# Job Events
# ----------
# before_job = ["rfid_retail.utils.before_job"]
# after_job = ["rfid_retail.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"rfid_retail.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }


# RFID retail
# -----------
# Everything below is a no-op until RFID Settings > Enable RFID is ticked.

required_apps = ["erpnext"]

after_install = "rfid_retail.setup.install.after_install"
after_migrate = "rfid_retail.setup.install.after_migrate"

_rfid_invoice_events = {
	"before_submit": "rfid_retail.overrides.sales_invoice.before_submit",
	"on_submit": "rfid_retail.overrides.sales_invoice.on_submit",
	"on_cancel": "rfid_retail.overrides.sales_invoice.on_cancel",
}
_inward_events = {
	"on_submit": ["rfid_retail.overrides.stock.on_submit", "rfid_retail.labels.on_inward_submit"],
}
# Documents whose rows carry the tags read for them (bulk tagging at purchase, transfers, issues)
_tagged_stock_events = {
	"validate": "rfid_retail.overrides.stock.validate",
	"on_submit": ["rfid_retail.overrides.stock.on_submit", "rfid_retail.labels.on_inward_submit"],
	"on_cancel": "rfid_retail.overrides.stock.on_cancel",
	"on_trash": "rfid_retail.overrides.stock.on_trash",
}

# Purchase pricing and offers (RFID Settings > Purchase Pricing): each switch off = no-op
_purchase_pricing_events = {
	"before_validate": "rfid_retail.overrides.purchase.before_validate",
	"validate": "rfid_retail.overrides.purchase.validate",
}
_priced_inward_events = {
	**_tagged_stock_events,
	"before_validate": "rfid_retail.overrides.purchase.before_validate",
	"validate": ["rfid_retail.overrides.purchase.validate", _tagged_stock_events["validate"]],
	# prices first, so a sticker job made on submit prints the new price
	"on_submit": ["rfid_retail.overrides.purchase.on_submit", *_tagged_stock_events["on_submit"]],
	"on_cancel": [_tagged_stock_events["on_cancel"], "rfid_retail.overrides.purchase.on_cancel"],
}

doc_events = {
	"Sales Invoice": _rfid_invoice_events,
	"POS Invoice": _rfid_invoice_events,
	"Purchase Order": _purchase_pricing_events,
	"Purchase Receipt": _priced_inward_events,
	"Purchase Invoice": _priced_inward_events,
	"Stock Entry": _tagged_stock_events,
	"Item Price": {"validate": "rfid_retail.overrides.item_price.validate"},
	"Item Attribute": {"on_update": "rfid_retail.overrides.item_attribute.on_update"},
	# counters may be reset on a live site; never hand out a taken name
	**{
		doctype: {"before_naming": "rfid_retail.naming.sync_series"}
		for doctype in (
			"RFID Basket",
			"RFID Exception",
			"RFID Tag Assignment",
			"RFID Tag Event",
			"RFID Audit Session",
		)
	},
	"Stock Reconciliation": _inward_events,
	# item codes and barcodes: own switches, independent of RFID
	"Item": {
		"before_insert": ["rfid_retail.item_codes.before_insert", "rfid_retail.labels.item_before_insert"],
	},
}

app_include_js = ["rfid_retail_desk.bundle.js", "rfid_retail_pricing.bundle.js"]
doctype_list_js = {
	"Item": "public/js/item_list.js",
	"Serial No": "public/js/serial_no_list.js",
}
extend_bootinfo = "rfid_retail.boot.extend_bootinfo"

jinja = {
	"methods": [
		"rfid_retail.labels.barcode_svg",
		"rfid_retail.labels.qr_svg",
		"rfid_retail.labels.label_context",
		"rfid_retail.printing.invoice_context",
	],
}

scheduler_events = {
	"cron": {
		"*/5 * * * *": ["rfid_retail.api.gate.mark_stale_devices"],
		"0 23 * * *": ["rfid_retail.api.audit.flag_missed_audit"],
		"30 2 * * *": ["rfid_retail.overrides.stock.release_stale_reservations"],
	},
}

override_doctype_dashboards = {
	"Item": "rfid_retail.dashboards.item",
	"Serial No": "rfid_retail.dashboards.serial_no",
	"Sales Invoice": "rfid_retail.dashboards.sales_invoice",
	"POS Profile": "rfid_retail.dashboards.pos_profile",
	"Warehouse": "rfid_retail.dashboards.warehouse",
}
