// Copyright (c) 2026, MicroNXT Solutions Pvt. Ltd. and contributors
// For license information, please see license.txt

frappe.ui.form.on("Sticker Print Job", {
	refresh(frm) {
		if (frm.is_new()) return;
		frm.add_custom_button(__("Print Stickers"), () => rfid_retail.print_label_job(frm.doc.name)).addClass("btn-primary");
		frm.add_custom_button(__("From Document"), () => add_from_document(frm), __("Add Labels"));
		frm.add_custom_button(__("From Stock"), () => add_from_stock(frm), __("Add Labels"));
	},
});

function add_rows(frm, args) {
	frappe.call({
		method: "rfid_retail.labels.add_rows",
		args: Object.assign({ job: frm.doc.name }, args),
		freeze: true,
		callback: (r) => {
			frappe.show_alert({ message: __("{0} rows added", [r.message]), indicator: "green" });
			frm.reload_doc();
		},
	});
}

function add_from_document(frm) {
	const d = new frappe.ui.Dialog({
		title: __("Labels for received stock"),
		fields: [
			{
				fieldname: "source_doctype",
				fieldtype: "Select",
				label: __("Document Type"),
				options: ["Purchase Receipt", "Purchase Invoice", "Stock Entry", "Stock Reconciliation"],
				default: "Purchase Receipt",
				reqd: 1,
			},
			{
				fieldname: "source_name",
				fieldtype: "Dynamic Link",
				options: "source_doctype",
				label: __("Document"),
				reqd: 1,
				get_query: () => ({ filters: { docstatus: 1 } }),
			},
		],
		primary_action_label: __("Add"),
		primary_action(values) {
			d.hide();
			add_rows(frm, values);
		},
	});
	d.show();
}

function add_from_stock(frm) {
	const d = new frappe.ui.Dialog({
		title: __("Labels for stock on hand"),
		fields: [
			{ fieldname: "warehouse", fieldtype: "Link", options: "Warehouse", label: __("Warehouse"), reqd: 1 },
			{ fieldname: "item_group", fieldtype: "Link", options: "Item Group", label: __("Item Group") },
			{ fieldname: "item_code", fieldtype: "Link", options: "Item", label: __("Item") },
		],
		primary_action_label: __("Add"),
		primary_action(values) {
			d.hide();
			add_rows(frm, values);
		},
	});
	d.show();
}
