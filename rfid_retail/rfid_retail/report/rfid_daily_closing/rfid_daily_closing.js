frappe.query_reports["RFID Daily Closing"] = {
	filters: [
		{ fieldname: "date", label: __("Date"), fieldtype: "Date", default: frappe.datetime.get_today(), reqd: 1 },
		{ fieldname: "company", label: __("Company"), fieldtype: "Link", options: "Company",
			default: frappe.defaults.get_user_default("Company"), reqd: 1 },
	],
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "status" && data) {
			const color = { OK: "green", Check: "orange", Alert: "red" }[data.status];
			if (color) value = `<span class="indicator-pill ${color}">${data.status}</span>`;
		}
		if (data && data.is_header) value = `<b>${value}</b>`;
		return value;
	},
};
