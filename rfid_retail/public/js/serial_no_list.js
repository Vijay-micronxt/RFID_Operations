// One label per selected serial (and per set piece when switched on)
(function () {
	const settings = (frappe.listview_settings["Serial No"] = frappe.listview_settings["Serial No"] || {});
	const previous = settings.onload;
	settings.onload = function (listview) {
		if (previous) previous(listview);
		if (!frappe.model.can_create("Sticker Print Job")) return;
		listview.page.add_actions_menu_item(__("Print Stickers"), () => {
			const serials = listview.get_checked_items(true);
			if (!serials.length) return frappe.msgprint(__("Select serial numbers first."));
			rfid_retail.open_label_job("rfid_retail.labels.make_from_serials", { serials });
		});
	};
})();
