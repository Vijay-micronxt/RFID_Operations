// Copyright (c) 2026, MicroNXT Solutions Pvt. Ltd. and contributors
// For license information, please see license.txt

frappe.ui.form.on("RFID Audit Session", {
	refresh(frm) {
		if (frm.is_new()) return;
		const run = (method, label) =>
			frm.add_custom_button(label, () =>
				frappe.call({ method: `rfid_retail.api.audit.${method}`, args: { session: frm.doc.name }, freeze: true })
					.then(() => frm.reload_doc())
			);
		if (["Comparison Ready", "Investigation Pending"].includes(frm.doc.status)) {
			run("compare", __("Compare Again"));
			run("submit_for_review", __("Send for Review"));
		}
		if (frm.doc.status === "Submitted for Review") {
			frm.add_custom_button(__("Mark Reviewed"), () =>
				frappe.confirm(
					__("Unresolved shortages will be raised as alerts. Mark this count as reviewed?"),
					() => frappe.call({ method: "rfid_retail.api.audit.mark_reviewed", args: { session: frm.doc.name }, freeze: true })
						.then(() => frm.reload_doc())
				)
			).addClass("btn-primary");
		}
		if (frm.doc.status !== "Reviewed") {
			frm.set_intro(__("Set a Resolution on each result row (Rescanned - Found, Confirmed Missing, ...) and save before review."), "blue");
		}
	},
});
