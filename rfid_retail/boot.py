from frappe.utils import cint

from rfid_retail.settings import get_settings


def extend_bootinfo(bootinfo):
	settings = get_settings()
	bootinfo.rfid_retail = {
		"enabled": cint(settings.enabled),
		"item_codes": cint(settings.item_codes_enabled),
		"label_print_format": settings.label_print_format or "Retail Barcode Label",
	}
