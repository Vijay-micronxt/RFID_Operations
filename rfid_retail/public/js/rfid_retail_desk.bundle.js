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
		rfid_retail.item_tag_summary(frm);
	},
});

// Stock against tags per warehouse. Ghost tags = tags still counted for pieces sold without their tag
// read (RFID Unread Sale open): which bill, who sold it and when, until a stock count settles them.
rfid_retail.item_tag_summary = async function (frm) {
	const $layout = $(frm.layout.wrapper);
	$layout.find(".rfid-tag-summary").remove();
	if (frm.is_new() || !(frappe.boot.rfid_retail || {}).enabled || !frm.doc.custom_rfid_tracked || frm.doc.has_serial_no) return;
	const item = frm.doc.name;
	const rows = await frappe.xcall("rfid_retail.api.mapping.item_rfid_summary", { item_code: item });
	if (frm.doc.name !== item) return; // moved to another item meanwhile
	$layout.find(".rfid-tag-summary").remove();
	if (!rows.length) return;
	const esc = frappe.utils.escape_html;
	const html = rows
		.map((r) => {
			let line = `<b>${esc(r.warehouse)}</b>: ${__("Stock")} <b>${r.stock}</b> · ${__("Tags")} <b>${r.tags}</b>`;
			if (r.ghost) line += ` · <span class="text-danger">${__("Ghost tags")} <b>${r.ghost}</b></span> · ${__("Real tags")} <b>${r.real_tags}</b>`;
			if (r.untagged) line += ` · ${__("Pieces without tag")} <b>${r.untagged}</b>`;
			if (r.no_tag_sales) line += ` · <a href="/app/rfid-unread-sale?item_code=${encodeURIComponent(frm.doc.name)}&status=No%20Tag">${__("Sold without tag: {0} bill(s)", [r.no_tag_sales])}</a>`;
			const sales = r.sales
				.map(
					(s) =>
						`<li>${s.qty} ${__("pc")} · <a href="/app/${frappe.router.slug(s.doctype || "Sales Invoice")}/${encodeURIComponent(s.invoice)}">${esc(s.invoice)}</a> · ${esc(s.reason || "")} · ${__("by")} ${esc(s.sold_by || "-")} · ${s.sold_on ? frappe.datetime.str_to_user(s.sold_on) : "-"} · <a href="/app/rfid-unread-sale/${encodeURIComponent(s.name)}">${esc(s.name)}</a></li>`
				)
				.join("");
			return `<div>${line}</div>${sales ? `<ul style="margin:4px 0 4px 18px">${sales}</ul><div class="text-muted small">${__("Ghost tags stand for pieces that left without their tag read. Count this item at the RFID station to find and free them.")}</div>` : ""}`;
		})
		.join("");
	const ghost = rows.some((r) => r.ghost);
	$(`<div class="form-message ${ghost ? "red" : "blue"} rfid-tag-summary">${html}</div>`).insertAfter(
		$layout.children(".form-message-container").first()
	);
};

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
			if (!(frappe.boot.rfid_retail || {}).enabled) return;
			if (doctype === "Purchase Invoice" && !frm.doc.update_stock) return;
			if (frm.doc.docstatus === 2) return rfid_retail.held_tags_button(frm);
			if (frm.doc.docstatus === 0 && frm.doc.amended_from) return rfid_retail.amend_tags_button(frm);
			if (frm.doc.docstatus !== 1) return;
			frm.add_custom_button(__("RFID Tags"), () => rfid_retail.tag_dialog(frm));
		},
		// Cancelling takes every tag this document put on garments off them: say so and ask again.
		before_cancel(frm) {
			if (!(frappe.boot.rfid_retail || {}).enabled) return;
			return rfid_retail.confirm_cancel_tags(frm);
		},
	});
});

rfid_retail.confirm_cancel_tags = async function (frm) {
	const impact = await frappe.xcall("rfid_retail.overrides.stock.cancel_impact", { doctype: frm.doctype, name: frm.doc.name });
	if (!impact.tags) return;
	const esc = frappe.utils.escape_html;
	const list = impact.items.map((r) => `<li>${esc(r.item_name || r.item_code)} (${esc(r.item_code)}): <b>${r.tags}</b></li>`).join("");
	const text = impact.keep
		? __(
				"Cancelling {0} takes its <b>{1}</b> RFID tag(s) off stock, but keeps them on this document: when you amend it and submit the amendment, the same tags go back on the same rows. If you will not amend, free them from the cancelled document (RFID Tags button).",
				[esc(frm.doc.name), impact.tags]
		  )
		: __(
				"Cancelling {0} frees all <b>{1}</b> RFID tag(s) put on its garments. Those garments stop counting as tagged stock and the tags can go on other garments.",
				[esc(frm.doc.name), impact.tags]
		  );
	await new Promise((resolve) => {
		let answered = false;
		const d = new frappe.ui.Dialog({
			title: impact.keep ? __("Cancel keeps the RFID tags for the amendment") : __("Cancel will free RFID tags"),
			fields: [
				{
					fieldtype: "HTML",
					options: `<div class="alert alert-warning" style="margin-bottom:8px">${text}</div><ul style="margin:0 0 8px 16px">${list}</ul><p class="text-muted small">${__(
						"Only cancel if this document is wrong. To fix just a wrong tag, use Remove tags at the RFID station and tag the piece again with the RFID Tags button."
					)}</p>`,
				},
			],
			primary_action_label: impact.keep ? __("Cancel") : __("Cancel and free tags"),
			primary_action: () => {
				answered = true;
				d.hide();
				resolve();
			},
			secondary_action_label: __("Keep document"),
			secondary_action: () => d.hide(),
		});
		d.get_primary_btn().removeClass("btn-primary").addClass("btn-danger");
		d.onhide = () => {
			if (!answered) {
				frappe.validated = false;
				resolve();
			}
		};
		d.show();
	});
};

// Cancelled document: the tags it kept for its amendment. The amendment takes them back on submit; here
// they can be freed (all or some) for other garments.
rfid_retail.held_tags_button = async function (frm) {
	const r = await frappe.xcall("rfid_retail.overrides.stock.held_tag_rows", { doctype: frm.doctype, name: frm.doc.name });
	if (!r.tags || !r.tags.length) return;
	frm.dashboard.set_headline_alert(
		__("{0} RFID tag(s) are kept on this cancelled document. {1}", [
			r.tags.length,
			r.amended
				? __("Submitting amendment {0} puts them back on its rows.", [r.amended.name])
				: __("Amend and submit to put them back, or free them with RFID Tags."),
		]),
		"orange"
	);
	frm.add_custom_button(__("RFID Tags"), () => rfid_retail.held_tags_dialog(frm, r.tags));
};

rfid_retail.held_tags_dialog = function (frm, tags) {
	const esc = frappe.utils.escape_html;
	const rows = tags
		.map(
			(t) =>
				`<tr data-tag="${esc(t.tag)}"><td><input type="checkbox" class="held-tag" value="${esc(t.tag)}"></td><td>${esc(t.tag)}</td><td>${
					t.idx ? __("Row {0}", [t.idx]) : ""
				}</td><td>${esc(t.item_name || t.item_code || "")}</td></tr>`
		)
		.join("");
	const d = new frappe.ui.Dialog({
		title: __("RFID tags kept on {0}", [frm.doc.name]),
		size: "large",
		fields: [
			{
				fieldtype: "HTML",
				options: `<p class="text-muted">${__(
					"These tags left stock when this document was cancelled. Submitting its amendment puts them back on the same rows. Free the ticked tags if they will not be used for the amendment; a tag read on any other row also moves there by itself."
				)}</p><input type="text" class="form-control held-scan" autocomplete="off" placeholder="${__(
					"Scan the tags to free here (or tick them below)"
				)}"><div class="held-scan-msg small" style="margin:4px 0 8px"></div><div style="margin-bottom:6px"><a class="held-all">${__(
					"Tick all"
				)}</a> · <a class="held-none">${__("Untick all")}</a> · <span class="held-ticked text-muted"></span></div><table class="table table-bordered table-sm"><thead><tr><th></th><th>${__("Tag")}</th><th>${__("Row")}</th><th>${__(
					"Item"
				)}</th></tr></thead><tbody>${rows}</tbody></table>`,
			},
		],
		primary_action_label: __("Free ticked tags"),
		primary_action: async () => {
			const codes = [...d.$wrapper.find(".held-tag:checked")].map((el) => el.value);
			if (!codes.length) return frappe.msgprint(__("Tick the tags to free."));
			const res = await frappe.xcall("rfid_retail.overrides.stock.free_held_tags", {
				doctype: frm.doctype,
				name: frm.doc.name,
				codes,
			});
			d.hide();
			frappe.show_alert({ message: __("{0} tag(s) freed", [res.freed]), indicator: "green" });
			frm.reload_doc();
		},
	});
	d.get_primary_btn().removeClass("btn-primary").addClass("btn-danger");
	const $w = d.$wrapper;
	const count = () => $w.find(".held-ticked").text(__("{0} of {1} ticked", [$w.find(".held-tag:checked").length, tags.length]));
	const mark = (code) => {
		const $row = $w.find(`tr[data-tag="${CSS.escape(code)}"]`);
		if (!$row.length) return false;
		$row.find(".held-tag").prop("checked", true);
		$row.css("background", "var(--green-highlight-color, #e4f5e9)");
		return true;
	};
	// the RFID reader types codes like a keyboard: take every code in the box a moment after the last key
	let timer = null;
	const take = () => {
		const $in = $w.find(".held-scan");
		const codes = [...new Set(($in.val() || "").split(/[\s,]+/).map((c) => c.trim().toUpperCase()).filter(Boolean))];
		$in.val("");
		if (!codes.length) return;
		const unknown = codes.filter((c) => !mark(c));
		$w.find(".held-scan-msg")
			.toggleClass("text-danger", !!unknown.length)
			.toggleClass("text-success", !unknown.length)
			.text(unknown.length ? __("Not kept on this document: {0}", [unknown.join(", ")]) : __("{0} tag(s) ticked", [codes.length]));
		count();
	};
	$w.on("input", ".held-scan", () => {
		clearTimeout(timer);
		timer = setTimeout(take, 400);
	});
	$w.on("keydown", ".held-scan", (e) => {
		if (e.key !== "Enter") return;
		e.preventDefault();
		clearTimeout(timer);
		take();
	});
	$w.on("change", ".held-tag", count);
	$w.on("click", ".held-all", () => ($w.find(".held-tag").prop("checked", true), count()));
	$w.on("click", ".held-none", () => ($w.find(".held-tag").prop("checked", false), count()));
	count();
	d.show();
	setTimeout(() => $w.find(".held-scan").trigger("focus"), 300);
};

// Amendment draft: the original's tags come along on the same rows. Before submit they can be put on
// another row, taken off (they stay kept on the cancelled original) or put on a row from the original's
// kept tags. Saved with the draft; submit puts them on the garments.
rfid_retail.amend_tag_rows = function (frm) {
	const split = (v) => (v || "").split(/[\s,]+/).map((c) => c.trim().toUpperCase()).filter(Boolean);
	return (frm.doc.items || []).map((r) => ({ row: r, tags: split(r.custom_rfid_tags), qty: Math.abs(cint(flt(r.stock_qty || r.qty))) }));
};

rfid_retail.amend_tags_button = async function (frm) {
	const rows = rfid_retail.amend_tag_rows(frm);
	const held = await frappe.xcall("rfid_retail.overrides.stock.held_tag_rows", { doctype: frm.doctype, name: frm.doc.amended_from });
	const on_rows = new Set(rows.flatMap((r) => r.tags));
	const spare = (held.tags || []).filter((t) => !on_rows.has(t.tag));
	const total = rows.reduce((n, r) => n + r.tags.length, 0);
	if (!total && !spare.length) return;
	const over = rows.filter((r) => r.tags.length > r.qty);
	const short = rows.filter((r) => r.tags.length && r.tags.length < r.qty);
	let note = __("{0} RFID tag(s) of {1} are on these rows; submit puts them back on the garments.", [total, frm.doc.amended_from]);
	if (over.length) note += " " + __("Rows {0} have more tags than pieces.", [over.map((r) => r.row.idx).join(", ")]);
	if (spare.length) note += " " + __("{0} kept tag(s) are on no row.", [spare.length]);
	if (short.length) note += " " + __("Rows {0} have fewer tags than pieces (tag the rest after submit).", [short.map((r) => r.row.idx).join(", ")]);
	frm.dashboard.set_headline_alert(note, over.length ? "red" : spare.length || short.length ? "orange" : "blue");
	frm.add_custom_button(__("RFID Tags"), () => rfid_retail.amend_tags_dialog(frm, spare));
};

rfid_retail.amend_tags_dialog = function (frm, spare) {
	const esc = frappe.utils.escape_html;
	const rows = rfid_retail.amend_tag_rows(frm);
	const KEEP = "__keep__";
	const options = (selected) =>
		rows
			.map(
				(r) =>
					`<option value="${esc(r.row.name)}" ${selected === r.row.name ? "selected" : ""}>${__("Row {0}", [r.row.idx])}: ${esc(
						r.row.item_name || r.row.item_code
					)} (${r.qty} ${__("pcs")})</option>`
			)
			.join("") +
		`<option value="${KEEP}" ${selected === KEEP ? "selected" : ""}>${__("Take off (stays kept on {0})", [esc(frm.doc.amended_from)])}</option>`;
	const line = (code, selected, from) =>
		`<tr><td style="font-family:monospace">${esc(code)}</td><td class="text-muted">${esc(from)}</td><td><select class="form-control input-xs amend-tag" data-code="${esc(
			code
		)}">${options(selected)}</select></td></tr>`;
	const body =
		rows.flatMap((r) => r.tags.map((c) => line(c, r.row.name, __("Row {0}", [r.row.idx])))).join("") +
		spare.map((t) => line(t.tag, KEEP, __("Kept, no row"))).join("");
	const d = new frappe.ui.Dialog({
		title: __("RFID tags of this amendment"),
		size: "large",
		fields: [
			{
				fieldtype: "HTML",
				options: `<p class="text-muted">${__(
					"Pick the row each tag goes on. Same pieces: leave everything as it is. Fewer pieces: take the extra tags off; they stay kept on {0} until you free them there or read them on another row. More pieces: tag the new ones with the RFID Tags button after submit.",
					[esc(frm.doc.amended_from)]
				)}</p><div class="amend-count text-muted small" style="margin-bottom:6px"></div><table class="table table-bordered table-sm"><thead><tr><th>${__("Tag")}</th><th>${__(
					"Now"
				)}</th><th>${__("Goes on")}</th></tr></thead><tbody>${body}</tbody></table>`,
			},
		],
		primary_action_label: __("Apply"),
		primary_action: () => {
			const plan = {};
			d.$wrapper.find(".amend-tag").each((_, el) => {
				if (el.value !== KEEP) (plan[el.value] = plan[el.value] || []).push(el.dataset.code);
			});
			const over = rows.filter((r) => (plan[r.row.name] || []).length > r.qty);
			if (over.length) {
				frappe.msgprint(
					over.map((r) => __("Row {0}: {1} tags for {2} pieces.", [r.row.idx, (plan[r.row.name] || []).length, r.qty])).join("<br>")
				);
				return;
			}
			rows.forEach((r) => {
				const tags = plan[r.row.name] || [];
				frappe.model.set_value(r.row.doctype, r.row.name, "custom_rfid_tags", tags.join("\n"));
				frappe.model.set_value(r.row.doctype, r.row.name, "custom_rfid_tag_count", tags.length);
			});
			d.hide();
			frm.save();
		},
	});
	const recount = () => {
		const n = {};
		d.$wrapper.find(".amend-tag").each((_, el) => (n[el.value] = (n[el.value] || 0) + 1));
		d.$wrapper.find(".amend-count").html(
			rows
				.map((r) => {
					const c = n[r.row.name] || 0;
					const cls = c > r.qty ? "text-danger" : c < r.qty ? "text-warning" : "text-success";
					return `<span class="${cls}">${__("Row {0}", [r.row.idx])}: ${c}/${r.qty}</span>`;
				})
				.join(" · ")
		);
	};
	d.$wrapper.on("change", ".amend-tag", recount);
	recount();
	d.show();
};

rfid_retail.tag_dialog = async function (frm) {
	let rows = await frappe.xcall("rfid_retail.overrides.stock.row_tag_rows", { doctype: frm.doctype, name: frm.doc.name });
	if (!rows.length) {
		frappe.msgprint(
			__("No row to tag here. Tick RFID Tracked Garment on the item first. Sets (suits and other multi-piece items) are tagged piece by piece at the Tag garments station.")
		);
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
			{
				fieldname: "review",
				fieldtype: "Button",
				label: __("Check stickers / move tags"),
				click: () => rfid_retail.review_tags_dialog(frm, () => refreshRows()),
				hidden: !rows.some((r) => r.direction === "in"),
			},
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
				<span class="text-muted">${esc(verb[S.row.direction])} · ${esc(place || "")}${
					S.row.done ? ` · ${esc(__("{0} tag(s) already on this row", [S.row.done]))}` : ""
				}</span>
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
								${v && v.move_from ? `<a class="small" style="font-weight:600" data-move="${esc(c)}">${__("Move here")}</a>` : ""}
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
	async function refreshRows() {
		rows = await frappe.xcall("rfid_retail.overrides.stock.row_tag_rows", { doctype: frm.doctype, name: frm.doc.name });
		S.row = rows.find((x) => x.row === S.row.row) || S.row;
		d.set_df_property("row", "options", rows.map(label));
		d.set_value("row", label(S.row));
		frm.reload_doc();
		render();
	}

	d.fields_dict.list.$wrapper.on("click", "[data-move]", async (e) => {
		const code = $(e.currentTarget).attr("data-move");
		await frappe.xcall("rfid_retail.overrides.stock.move_row_tags", {
			doctype: frm.doctype,
			name: frm.doc.name,
			moves: [{ code, row: S.row.row }],
		});
		frappe.show_alert({ message: __("Tag {0} moved to row {1}", [code, S.row.idx]), indicator: "green" }, 5);
		S.codes = S.codes.filter((c) => c !== code);
		delete S.verdicts[code];
		await refreshRows();
	});
	d.fields_dict.list.$wrapper.on("click", "[data-drop]", (e) => {
		const code = $(e.currentTarget).attr("data-drop");
		S.codes = S.codes.filter((c) => c !== code);
		render();
	});
	d.show();
	render();
	setTimeout(() => input.focus(), 300);
};

// Sticker check: read the tags of the garments in hand and see which item / row each one was read for.
// A tag read for the wrong row (sticker of B stuck on A) is moved to the right row; all moves are saved
// together so two rows can swap tags.
rfid_retail.review_tags_dialog = async function (frm, after) {
	const esc = frappe.utils.escape_html;
	let rows = (await frappe.xcall("rfid_retail.overrides.stock.row_tag_rows", { doctype: frm.doctype, name: frm.doc.name })).filter(
		(r) => r.direction === "in"
	);
	const R = { codes: [], info: {}, target: {} };
	const rowLabel = (r) => `${__("Row")} ${r.idx}: ${r.item_name} (${r.item_code})`;

	const d = new frappe.ui.Dialog({
		title: __("Check stickers / move tags"),
		size: "extra-large",
		fields: [
			{
				fieldtype: "HTML",
				options: `<p class="text-muted small" style="margin:0 0 8px">${__(
					"Read the tag of each garment in hand. Each line shows the item and row the tag was read for. If the garment is a different item, pick its row and press Save moves."
				)}</p>`,
			},
			{ fieldname: "read", fieldtype: "Data", label: __("Tags on the reader") },
			{ fieldname: "list", fieldtype: "HTML" },
		],
		primary_action_label: __("Save moves"),
		primary_action: () => save(),
		secondary_action_label: __("Clear reads"),
		secondary_action: () => {
			R.codes = [];
			R.info = {};
			R.target = {};
			render();
		},
	});

	const pending = () => R.codes.filter((c) => R.info[c] && R.info[c].movable && R.target[c] && R.target[c] !== R.info[c].row);

	function render() {
		const html = R.codes
			.slice()
			.reverse()
			.map((c) => {
				const v = R.info[c];
				if (!v) return `<tr><td><span class="indicator-pill gray">${esc(c)}</span></td><td colspan="2" class="text-muted">${__("Checking...")}</td></tr>`;
				const moved = v.movable && R.target[c] && R.target[c] !== v.row;
				const select = v.movable
					? `<div class="control-input flex align-center" style="position:relative"><select class="form-control ellipsis" style="padding-right:28px;cursor:pointer" title="${esc(
							__("Pick the row of the item this garment really is")
						)}" data-target="${esc(c)}">${rows
							.map(
								(r) =>
									`<option value="${esc(r.row)}" ${r.row === (R.target[c] || v.row) ? "selected" : ""}>${esc(rowLabel(r))}${r.row === v.row ? " ✓" : ""}</option>`
							)
							.join("")}</select><span style="position:absolute;right:10px;pointer-events:none">${frappe.utils.icon(
							"select",
							"xs"
						)}</span></div>`
					: `<span class="text-muted small">${__("cannot be moved")}</span>`;
				return `<tr ${moved ? 'style="background:var(--orange-50)"' : ""}>
					<td><span class="indicator-pill ${v.row ? "green" : "red"}">${esc(c)}</span></td>
					<td>${
						v.item_name
							? `<b>${esc(v.item_name)}</b><br><span class="small text-muted">${esc(v.item_code)} · ${esc(v.message)}</span>`
							: `<span class="text-danger small">${esc(v.message)}</span>`
					}</td>
					<td style="min-width:260px">${select}</td></tr>`;
			})
			.join("");
		d.fields_dict.list.$wrapper.html(
			R.codes.length
				? `<table class="table table-bordered" style="margin:0"><thead><tr><th>${__("Tag")}</th><th>${__("Read for")}</th><th>${__(
						"Garment really is (click to change)"
					)}</th></tr></thead><tbody>${html}</tbody></table>
					<p class="small text-muted" style="margin-top:6px">${__("{0} tag(s) to move", [pending().length])}</p>`
				: `<p class="text-muted">${__("No tags read yet.")}</p>`
		);
		d.get_primary_btn().prop("disabled", !pending().length);
	}

	async function check(fresh) {
		const r = await frappe.xcall("rfid_retail.overrides.stock.review_tags", { doctype: frm.doctype, name: frm.doc.name, codes: fresh });
		r.forEach((v) => (R.info[v.code] = v));
		render();
	}

	function add(text) {
		const fresh = [...new Set(text.split(/[\s,]+/).map((x) => x.trim().toUpperCase()))].filter((x) => x && !R.codes.includes(x));
		if (!fresh.length) return;
		R.codes.push(...fresh);
		render();
		check(fresh);
	}

	async function save() {
		const moves = pending().map((c) => ({ code: c, row: R.target[c] }));
		if (!moves.length) return;
		const r = await frappe.xcall("rfid_retail.overrides.stock.move_row_tags", { doctype: frm.doctype, name: frm.doc.name, moves });
		frappe.show_alert({ message: __("{0} tag(s) moved", [r.moved]), indicator: "green" }, 5);
		rows = r.rows.filter((x) => x.direction === "in");
		R.target = {};
		await check([...R.codes]);
		frm.reload_doc();
		after && after();
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
	d.fields_dict.list.$wrapper.on("change", "[data-target]", (e) => {
		R.target[$(e.currentTarget).attr("data-target")] = e.currentTarget.value;
		render();
	});
	d.show();
	render();
	setTimeout(() => input.focus(), 300);
};
