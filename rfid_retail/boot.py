from frappe.utils import cint, flt

from rfid_retail.settings import get_settings


def extend_bootinfo(bootinfo):
	settings = get_settings()
	bootinfo.rfid_retail = {
		"enabled": cint(settings.enabled),
		"item_codes": cint(settings.item_codes_enabled),
		"label_print_format": settings.label_print_format or "Retail Barcode Label",
		"purchase_pricing": cint(settings.purchase_pricing),
		"default_margin": flt(settings.default_margin if settings.default_margin is not None else 10),
		"mrp_round_up_to": cint(settings.mrp_round_up_to) or 1,
		"offers": cint(settings.offers_enabled),
		"variant_buttons": cint(settings.variant_buttons),
	}
