// Desk helpers for item codes and labels. Both work with RFID switched off.
frappe.provide("rfid_retail");

rfid_retail.print_label_job = async function (job) {
	const doc = await frappe.db.get_value("Sticker Print Job", job, "total_labels");
	if (!cint(doc.message && doc.message.total_labels)) {
		frappe.msgprint(__("Enter how many stickers to print (Copies) on at least one row, then save."));
		return;
	}
	const fallback = (frappe.boot.rfid_retail || {}).label_print_format || "Retail Barcode Label";
	const formats = await frappe.db.get_list("Print Format", {
		filters: { doc_type: "Sticker Print Job", disabled: 0 },
		pluck: "name",
	});
	const open = (format) => {
		const params = new URLSearchParams({
			doctype: "Sticker Print Job",
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
		__("Print Stickers"),
		__("Print")
	);
};

rfid_retail.open_label_job = function (method, args) {
	frappe.call({ method, args, freeze: true, freeze_message: __("Preparing labels...") }).then((r) => {
		if (r.message) frappe.set_route("Form", "Sticker Print Job", r.message);
	});
};

// Print Stickers on received stock
["Purchase Receipt", "Purchase Invoice", "Stock Entry", "Stock Reconciliation"].forEach((doctype) => {
	frappe.ui.form.on(doctype, {
		refresh(frm) {
			if (frm.doc.docstatus !== 1 || frm.doc.is_return) return;
			if (doctype === "Purchase Invoice" && !frm.doc.update_stock) return;
			if (!frappe.model.can_create("Sticker Print Job")) return;
			frm.add_custom_button(__("Print Stickers"), () =>
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
// After submit: pick a row, put all tags of its pieces on the reader at once. The tags are saved
// only when their number is exactly what the row needs and every tag is usable.
const RFID_TAG_DOCTYPES = ["Purchase Receipt", "Purchase Invoice", "Stock Entry"];

RFID_TAG_DOCTYPES.forEach((doctype) => {
	frappe.ui.form.on(doctype, {
		refresh(frm) {
			if (!(frappe.boot.rfid_retail || {}).enabled || frm.doc.docstatus !== 1) return;
			if (doctype === "Purchase Invoice" && !frm.doc.update_stock) return;
			frm.add_custom_button(__("RFID Tags"), () => rfid_retail.tag_dialog(frm));
		},
	});
});

rfid_retail.tag_dialog = async function (frm) {
	let rows = await frappe.xcall("rfid_retail.overrides.stock.row_tag_rows", { doctype: frm.doctype, name: frm.doc.name });
	if (!rows.length) {
		frappe.msgprint(__("No RFID item on this document. Tick RFID Tracked Garment on the item first."));
		return;
	}
	const esc = frappe.utils.escape_html;
	const verb = { in: __("to tag"), transfer: __("to move"), out: __("to free") };
	const label = (r) =>
		`${__("Row")} ${r.idx}: ${r.item_name} (${r.item_code}) · ${r.to_tag ? `${r.to_tag} ${verb[r.direction]}` : __("done")}`;
	const S = { row: rows.find((r) => r.to_tag) || rows[0], codes: [], verdicts: {}, timer: null, saving: false };

	const d = new frappe.ui.Dialog({
		title: __("RFID Tags"),
		size: "large",
		fields: [
			{ fieldname: "row", fieldtype: "Select", label: __("Item row"), options: rows.map(label), default: label(S.row), onchange: () => pick() },
			{ fieldname: "summary", fieldtype: "HTML" },
			{
				fieldname: "read",
				fieldtype: "Data",
				label: __("Tags on the reader"),
				description: __("Put the tags of all pieces of this row on the station together. A tag read twice counts once."),
			},
			{ fieldname: "list", fieldtype: "HTML" },
		],
		primary_action_label: __("Tag now"),
		primary_action: () => save(),
		secondary_action_label: __("Clear reads"),
		secondary_action: () => {
			S.codes = [];
			S.verdicts = {};
			render();
		},
	});

	const isReady = () => {
		const need = S.row.to_tag;
		const checked = S.codes.every((c) => S.verdicts[c]);
		const bad = S.codes.filter((c) => S.verdicts[c] && !S.verdicts[c].ok);
		// Incoming stock can be tagged in part; moving or sending out needs every unit's tag.
		const n = S.codes.length;
		const enough = S.row.direction === "in" ? n > 0 && n <= need : n === need;
		return need > 0 && enough && checked && !bad.length;
	};

	function render() {
		const need = S.row.to_tag;
		const n = S.codes.length;
		const bad = S.codes.filter((c) => S.verdicts[c] && !S.verdicts[c].ok);
		const checking = S.codes.some((c) => !S.verdicts[c]);
		let msg = "";
		let color = "gray";
		if (!need) {
			msg = __("Every piece of this row already has its tag.");
			color = "green";
		} else if (!n) {
			msg = __("Put all {0} tags on the reader.", [need]);
		} else if (bad.length) {
			msg = __("{0} tag(s) cannot be used (red below). Take them off the reader. Nothing is tagged yet.", [bad.length]);
			color = "red";
		} else if (n > need) {
			msg = __("{0} tag(s) too many: this row has {1} pieces. Take the extra tags off. Nothing is tagged yet.", [n - need, need]);
			color = "red";
		} else if (n < need && S.row.direction === "in") {
			msg = __("{0} of {1} pieces will be tagged; {2} stay untagged for later. Put more tags on the reader, or press Tag now.", [n, need, need - n]);
			color = "orange";
		} else if (n < need) {
			msg = __("{0} tag(s) missing: this row has {1} pieces. Put the rest on the reader. Nothing is tagged yet.", [need - n, need]);
			color = "orange";
		} else if (checking) {
			msg = __("Checking tags...");
		} else {
			msg = __("{0} of {0} tags are right. Tagging...", [need]);
			color = "green";
		}
		const place = S.row.direction === "transfer" ? `${S.row.from_warehouse} → ${S.row.warehouse}` : S.row.warehouse;
		d.fields_dict.summary.$wrapper.html(`
			<div style="display:flex;align-items:baseline;gap:12px;margin:4px 0">
				<span style="font-size:32px;font-weight:700;color:var(--${color}-600)">${n} / ${need}</span>
				<span class="text-muted">${esc(verb[S.row.direction])} · ${esc(place || "")}</span>
			</div>
			<div class="alert" style="margin:6px 0 4px;padding:8px 12px;border-radius:8px;background:var(--${color}-50);color:var(--${color}-700)">${esc(msg)}</div>`);
		d.fields_dict.list.$wrapper.html(
			n
				? `<div style="max-height:300px;overflow:auto;border:1px solid var(--border-color);border-radius:8px">${S.codes
						.slice()
						.reverse()
						.map((c) => {
							const v = S.verdicts[c];
							const ok = !v || v.ok;
							return `<div style="display:flex;align-items:center;gap:8px;padding:6px 10px;border-bottom:1px solid var(--border-color)">
								<span class="indicator-pill ${v ? (ok ? "green" : "red") : "gray"}">${esc(c)}</span>
								<span class="small ${ok ? "text-muted" : "text-danger"}" style="flex:1">${esc(v ? v.message : __("Checking..."))}</span>
								<a class="small" data-drop="${esc(c)}">${__("Remove")}</a></div>`;
						})
						.join("")}</div>`
				: ""
		);
		d.get_primary_btn().prop("disabled", !isReady() || S.saving);
		clearTimeout(S.timer);
		// Everything right: tag by itself once the reader has been quiet for a moment.
		if (isReady() && S.codes.length === need && !S.saving) S.timer = setTimeout(() => isReady() && save(), 1200);
	}

	async function check(fresh) {
		if (!fresh.length) return;
		const r = await frappe.xcall("rfid_retail.overrides.stock.check_row_tags", {
			doctype: frm.doctype,
			name: frm.doc.name,
			row: S.row.row,
			codes: fresh,
		});
		S.row = r.state;
		r.codes.forEach((v) => (S.verdicts[v.code] = v));
		render();
	}

	function add(text) {
		const fresh = [...new Set(text.split(/[\s,]+/).map((x) => x.trim().toUpperCase()))].filter((x) => x && !S.codes.includes(x));
		if (!fresh.length) return;
		S.codes.push(...fresh);
		render();
		check(fresh);
	}

	async function save() {
		if (!isReady() || S.saving) return;
		S.saving = true;
		render();
		try {
			const r = await frappe.xcall("rfid_retail.overrides.stock.assign_row_tags", {
				doctype: frm.doctype,
				name: frm.doc.name,
				row: S.row.row,
				codes: S.codes,
			});
			frappe.show_alert({ message: __("{0} pieces tagged on row {1}", [r.tagged, S.row.idx]), indicator: "green" }, 6);
			rows = await frappe.xcall("rfid_retail.overrides.stock.row_tag_rows", { doctype: frm.doctype, name: frm.doc.name });
			frm.reload_doc();
			// This row is done: forget its reads so nothing is sent twice.
			S.row = rows.find((x) => x.row === S.row.row) || S.row;
			S.codes = [];
			S.verdicts = {};
			const next = rows.find((x) => x.to_tag);
			if (!next) {
				d.hide();
				frappe.msgprint({ message: __("All RFID rows of {0} are tagged.", [frm.doc.name]), indicator: "green" });
				return;
			}
			d.set_df_property("row", "options", rows.map(label));
			S.row = next;
			d.set_value("row", label(next));
		} finally {
			S.saving = false;
			render();
		}
	}

	function pick() {
		const chosen = rows.find((r) => label(r) === d.get_value("row"));
		if (chosen && chosen.row !== S.row.row) {
			S.row = chosen;
			S.codes = [];
			S.verdicts = {};
		}
		render();
		input.focus();
	}

	const input = d.fields_dict.read.$input;
	input.on("keydown", (e) => {
		if (e.key !== "Enter") return;
		e.preventDefault();
		const text = input.val();
		input.val("");
		add(text);
	});
	input.on("paste", () =>
		setTimeout(() => {
			const text = input.val();
			if (text.split(/[\s,]+/).filter(Boolean).length > 1) {
				input.val("");
				add(text);
			}
		}, 0)
	);
	d.fields_dict.list.$wrapper.on("click", "[data-drop]", (e) => {
		const code = $(e.currentTarget).attr("data-drop");
		S.codes = S.codes.filter((c) => c !== code);
		render();
	});
	d.show();
	render();
	setTimeout(() => input.focus(), 300);
};
