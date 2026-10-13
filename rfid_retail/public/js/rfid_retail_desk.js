// Desk helpers for item codes and labels. Both work with RFID switched off.
frappe.provide("rfid_retail");

rfid_retail.print_label_job = async function (job) {
	const doc = await frappe.db.get_value("Label Print Job", job, "total_labels");
	if (!cint(doc.message && doc.message.total_labels)) {
		frappe.msgprint(__("Enter how many stickers to print (Copies) on at least one row, then save."));
		return;
	}
	const fallback = (frappe.boot.rfid_retail || {}).label_print_format || "Retail Barcode Label";
	const formats = await frappe.db.get_list("Print Format", {
		filters: { doc_type: "Label Print Job", disabled: 0 },
		pluck: "name",
	});
	const open = (format) => {
		const params = new URLSearchParams({
			doctype: "Label Print Job",
			name: job,
			format,
			no_letterhead: 1,
			trigger_print: 1,
		});
		window.open(`/printview?${params}`, "_blank");
		frappe.xcall("rfid_retail.labels.mark_printed", { job }).then(() => {
			if (cur_frm && cur_frm.doc.name === job) cur_frm.reload_doc();
		});
	};
	if (formats.length < 2) return open(fallback);
	frappe.prompt(
		{
			fieldname: "format",
			fieldtype: "Select",
			label: __("Sticker layout"),
			options: formats,
			default: formats.includes(fallback) ? fallback : formats[0],
		},
		({ format }) => open(format),
		__("Print Labels"),
		__("Print")
	);
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

// ------------------------------------------------------------ RFID tags on stock rows
// Put all tags of one row's item on the reader at once; each read code lands in the list.
const RFID_TAG_DOCTYPES = ["Purchase Receipt", "Purchase Invoice", "Stock Entry"];

RFID_TAG_DOCTYPES.forEach((doctype) => {
	frappe.ui.form.on(doctype, {
		refresh(frm) {
			if (!(frappe.boot.rfid_retail || {}).enabled || frm.doc.docstatus !== 0) return;
			if (doctype === "Purchase Invoice" && !frm.doc.update_stock) return;
			frm.add_custom_button(__("RFID Tags"), () => rfid_retail.tag_dialog(frm));
		},
	});
});

rfid_retail.tag_dialog = async function (frm) {
	if (frm.is_new() || frm.is_dirty()) await frm.save();
	const codes = [...new Set((frm.doc.items || []).map((r) => r.item_code).filter(Boolean))];
	const items = await frappe.db.get_list("Item", {
		filters: { name: ["in", codes], custom_rfid_tracked: 1 },
		fields: ["name", "item_name", "has_serial_no"],
		limit: codes.length || 1,
	});
	const rfidItems = Object.fromEntries(items.map((i) => [i.name, i]));
	const rows = (frm.doc.items || []).filter((r) => rfidItems[r.item_code]);
	if (!rows.length) {
		frappe.msgprint(__("No RFID item on this document. Tick RFID Tracked Garment on the item first."));
		return;
	}
	const label = (r) => `${__("Row")} ${r.idx}: ${rfidItems[r.item_code].item_name} (${r.item_code}) · ${r.transfer_qty || r.stock_qty || r.qty}`;
	const state = { row: rows[0], codes: [], verdicts: {} };

	const d = new frappe.ui.Dialog({
		title: __("RFID Tags"),
		size: "large",
		fields: [
			{ fieldname: "row", fieldtype: "Select", label: __("Item row"), options: rows.map(label), default: label(rows[0]), onchange: () => loadRow() },
			{ fieldname: "summary", fieldtype: "HTML" },
			{
				fieldname: "read",
				fieldtype: "Data",
				label: __("Tags read"),
				description: __("Put all tags of this item on the reader. Every tag counts once; repeated reads are ignored."),
			},
			{ fieldname: "list", fieldtype: "HTML" },
		],
		primary_action_label: __("Save tags"),
		primary_action: async () => {
			const bad = state.codes.filter((c) => state.verdicts[c] && !state.verdicts[c].ok);
			if (bad.length) {
				frappe.msgprint(__("Remove the tags marked red first."));
				return;
			}
			frappe.model.set_value(state.row.doctype, state.row.name, "custom_rfid_tags", state.codes.join("\n"));
			await frm.save();
			frappe.show_alert({ message: __("{0} tags saved on row {1}", [state.codes.length, state.row.idx]), indicator: "green" });
			d.hide();
		},
		secondary_action_label: __("Clear reads"),
		secondary_action: () => {
			state.codes = [];
			render();
		},
	});

	const qtyOf = (r) => cint(r.transfer_qty || r.stock_qty || r.qty);

	function render() {
		const qty = qtyOf(state.row);
		const n = state.codes.length;
		const bad = state.codes.filter((c) => state.verdicts[c] && !state.verdicts[c].ok).length;
		const color = n > qty || bad ? "red" : n === qty ? "green" : "orange";
		const isSerial = rfidItems[state.row.item_code].has_serial_no;
		d.fields_dict.summary.$wrapper.html(`
			<div style="display:flex;align-items:baseline;gap:12px;margin:4px 0 8px">
				<span style="font-size:28px;font-weight:700;color:var(--${color}-600)">${n} / ${qty}</span>
				<span class="text-muted">${__("tags read for this row")}</span>
			</div>
			${n > qty ? `<p class="text-danger">${__("More tags than units. Take out {0} tag(s).", [n - qty])}</p>` : ""}
			${isSerial ? `<p class="text-muted small">${__("Serial numbers are created on submit; each tag goes to the next new serial.")}</p>` : ""}`);
		d.fields_dict.list.$wrapper.html(
			state.codes.length
				? `<div style="max-height:320px;overflow:auto;border:1px solid var(--border-color);border-radius:8px">${state.codes
						.map((c) => {
							const v = state.verdicts[c];
							const ok = !v || v.ok;
							return `<div style="display:flex;align-items:center;gap:8px;padding:6px 10px;border-bottom:1px solid var(--border-color)">
								<span class="indicator-pill ${v ? (ok ? "green" : "red") : "gray"}">${frappe.utils.escape_html(c)}</span>
								<span class="small ${ok ? "text-muted" : "text-danger"}" style="flex:1">${frappe.utils.escape_html(v ? v.message : __("Checking..."))}</span>
								<a class="small" data-drop="${frappe.utils.escape_html(c)}">${__("Remove")}</a></div>`;
						})
						.join("")}</div>`
				: `<p class="text-muted">${__("No tags read yet.")}</p>`
		);
	}

	async function addCodes(text) {
		const fresh = [...new Set(text.split(/[\s,]+/).map((s) => s.trim().toUpperCase()))].filter(
			(s) => s && !state.codes.includes(s)
		);
		if (!fresh.length) return;
		state.codes.push(...fresh);
		render();
		const r = await frappe.xcall("rfid_retail.overrides.stock.check_row_tags", {
			doctype: frm.doctype,
			name: frm.doc.name,
			row: state.row.name,
			codes: fresh,
		});
		r.codes.forEach((v) => (state.verdicts[v.code] = v));
		render();
	}

	function loadRow() {
		state.row = rows.find((r) => label(r) === d.get_value("row")) || rows[0];
		state.codes = (state.row.custom_rfid_tags || "").split(/\s+/).filter(Boolean);
		state.verdicts = {};
		render();
		if (state.codes.length) recheck();
	}

	async function recheck() {
		const r = await frappe.xcall("rfid_retail.overrides.stock.check_row_tags", {
			doctype: frm.doctype,
			name: frm.doc.name,
			row: state.row.name,
			codes: state.codes,
		});
		r.codes.forEach((v) => (state.verdicts[v.code] = v));
		render();
	}

	const input = d.fields_dict.read.$input;
	input.on("keydown", (e) => {
		if (e.key !== "Enter") return;
		e.preventDefault();
		const text = input.val();
		input.val("");
		addCodes(text);
	});
	input.on("paste", () =>
		setTimeout(() => {
			const text = input.val();
			if (text.split(/[\s,]+/).filter(Boolean).length > 1) {
				input.val("");
				addCodes(text);
			}
		}, 0)
	);
	d.fields_dict.list.$wrapper.on("click", "[data-drop]", (e) => {
		const code = $(e.currentTarget).attr("data-drop");
		state.codes = state.codes.filter((c) => c !== code);
		render();
	});
	d.show();
	loadRow();
	setTimeout(() => input.focus(), 300);
};
