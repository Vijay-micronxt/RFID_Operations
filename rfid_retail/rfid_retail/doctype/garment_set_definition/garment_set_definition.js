// Copyright (c) 2026, MicroNXT Solutions Pvt. Ltd. and contributors
// For license information, please see license.txt

frappe.ui.form.on("Garment Set Definition", {
	setup(frm) {
		// Sets are defined on templates (shared by all variants) or on single items, never on a variant.
		frm.set_query("item", () => ({
			filters: {
				variant_of: ["is", "not set"],
				has_serial_no: 1,
				custom_rfid_tracked: 1,
				is_stock_item: 1,
				disabled: 0,
			},
		}));
	},
});
