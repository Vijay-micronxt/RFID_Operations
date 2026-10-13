// Bulk label printing from the Item list
(function () {
	const settings = (frappe.listview_settings["Item"] = frappe.listview_settings["Item"] || {});
	const previous = settings.onload;
	settings.onload = function (listview) {
		if (previous) previous(listview);
		if (!frappe.model.can_create("Sticker Print Job")) return;
		listview.page.add_actions_menu_item(__("Print Stickers"), () => {
			const items = listview.get_checked_items(true);
			if (!items.length) return frappe.msgprint(__("Select items first."));
			frappe.prompt(
				{ fieldname: "copies", fieldtype: "Int", label: __("Labels per item"), default: 1, reqd: 1 },
				(v) => rfid_retail.open_label_job("rfid_retail.labels.make_from_items", { items, copies: v.copies }),
				__("Print Stickers")
			);
		});
	};
})();
