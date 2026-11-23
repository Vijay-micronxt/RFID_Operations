// Copyright (c) 2026, MicroNXT Solutions Pvt. Ltd. and contributors
// For license information, please see license.txt

frappe.ui.form.on("Physical Audit", {
	refresh(frm) {
		// Only a submitted count can lead to a stock change, and only for someone allowed to make one.
		if (frm.doc.docstatus !== 1) return;

		if (frm.doc.stock_reconciliation) {
			frm.add_custom_button(__("Open Stock Reconciliation"), () =>
				frappe.set_route("Form", "Stock Reconciliation", frm.doc.stock_reconciliation)
			);
			return;
		}

		if (!frappe.model.can_create("Stock Reconciliation")) return;
		frm.add_custom_button(__("Create Stock Reconciliation"), () => {
			frappe.confirm(
				__(
					"Make a draft Stock Reconciliation from the differences? Missing pieces lower the stock and extra pieces raise it. Nothing changes in stock until you submit that draft."
				),
				() =>
					frappe
						.call({
							method: "rfid_retail.rfid_retail.doctype.physical_audit.physical_audit.make_stock_reconciliation",
							args: { name: frm.doc.name },
							freeze: true,
							freeze_message: __("Making the draft..."),
						})
						.then((r) => {
							const out = r.message;
							if (!out) return;
							const notes = [];
							if (out.skipped && out.skipped.length) {
								notes.push(__("Left out because they have serial or batch numbers: {0}", [out.skipped.join(", ")]));
							}
							if (out.no_rate && out.no_rate.length) {
								notes.push(
									__("No cost is known for {0}. Fill in its Valuation Rate on the draft before you submit it.", [
										out.no_rate.join(", "),
									])
								);
							}
							if (notes.length) frappe.msgprint(notes.join("<br><br>"));
							frappe.set_route("Form", "Stock Reconciliation", out.name);
						})
			);
		});
	},
});
