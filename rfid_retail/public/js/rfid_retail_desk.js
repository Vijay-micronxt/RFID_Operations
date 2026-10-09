// Desk helpers for item codes and labels. Both work with RFID switched off.
frappe.provide("rfid_retail");

rfid_retail.print_label_job = function (job) {
	const format = (frappe.boot.rfid_retail || {}).label_print_format || "Retail Barcode Label";
	const params = new URLSearchParams({
		doctype: "Label Print Job",
		name: job,
		format: format,
		no_letterhead: 1,
		trigger_print: 1,
	});
	window.open(`/printview?${params}`, "_blank");
	frappe.xcall("rfid_retail.labels.mark_printed", { job }).then(() => {
		if (cur_frm && cur_frm.doc.name === job) cur_frm.reload_doc();
	});
};

rfid_retail.open_label_job = function (method, args) {
	frappe.call({ method, args, freeze: true, freeze_message: __("Preparing labels...") }).then((r) => {
		if (r.message) frappe.set_route("Form", "Label Print Job", r.message);
	});
};

// Print Labels on received stock
["Purchase Receipt", "Purchase Invoice", "Stock Entry", "Stock Reconciliation"].forEach((doctype) => {
	frappe.ui.form.on(doctype, {
		refresh(frm) {
			if (frm.doc.docstatus !== 1 || frm.doc.is_return) return;
			if (doctype === "Purchase Invoice" && !frm.doc.update_stock) return;
			if (!frappe.model.can_create("Label Print Job")) return;
			frm.add_custom_button(__("Print Labels"), () =>
				rfid_retail.open_label_job("rfid_retail.labels.make_from_document", {
					source_doctype: frm.doctype,
					source_name: frm.doc.name,
				})
			);
		},
	});
});

// Item Code can stay blank: the server builds it from the pattern
frappe.ui.form.on("Item", {
	onload(frm) {
		rfid_retail.toggle_item_code(frm);
	},
	refresh(frm) {
		rfid_retail.toggle_item_code(frm);
	},
});

rfid_retail.toggle_item_code = function (frm) {
	if (!frm.is_new() || !(frappe.boot.rfid_retail || {}).item_codes) return;
	frm.set_df_property("item_code", "reqd", 0);
	frm.set_df_property("item_code", "description", __("Leave blank to build the code from the item code pattern."));
};
