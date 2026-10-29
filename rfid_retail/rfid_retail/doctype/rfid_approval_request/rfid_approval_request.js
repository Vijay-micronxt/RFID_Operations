// Copyright (c) 2026, MicroNXT Solutions Pvt. Ltd. and contributors
// For license information, please see license.txt

frappe.ui.form.on("RFID Approval Request", {
	refresh(frm) {
		if (frm.doc.status !== "Pending") return;
		const decide = (method, args = {}) =>
			frappe
				.call({ method: `rfid_retail.approval.${method}`, args: { request: frm.doc.name, ...args }, freeze: true })
				.then(() => frm.reload_doc());
		frm.add_custom_button(__("Approve"), () => decide("approve_request")).addClass("btn-primary");
		frm.add_custom_button(__("Reject"), () =>
			frappe.prompt({ fieldname: "why", fieldtype: "Data", label: __("Why (optional)") }, (v) =>
				decide("reject_request", { note: v.why })
			)
		);
		frm.set_intro(
			__("{0} asks to sell {1} x {2} without reading its RFID tag ({3}). Approve here, or give them your PIN from the notification.", [
				frm.doc.requested_by, frm.doc.qty, frm.doc.item_name || frm.doc.item_code, frm.doc.reason,
			]),
			"orange"
		);
	},
});
