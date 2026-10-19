// Purchase pricing and offers (RFID Settings > Purchase Pricing), each part behind its switch:
//   MRP         = rate x (1 + GST %) x (1 + Margin %), rounded up to a multiple of "Round MRP up to"
//   Offer Price = MRP less Offer %
// plus "Add Variants" (all variants of a template at once) and "Update Template" on purchase item tables,
// and Set Offer on Item / Item list / Item Price.
frappe.provide("rfid_retail.pricing");
const P = rfid_retail.pricing;

P.settings = () => frappe.boot.rfid_retail || {};
P.default_margin = () => flt(P.settings().default_margin ?? 10);
P.gst_cache = {};

P.round_up = (value) => {
	const step = Math.max(cint(P.settings().mrp_round_up_to ?? 10), 1);
	const rupees = value > 0 ? Math.floor(value + 0.5) : 0;
	return Math.ceil(rupees / step) * step;
};
P.offer_price = (mrp, offer) => {
	if (!(mrp > 0)) return 0;
	if (!P.settings().offers) return mrp;
	return Math.floor(mrp * (1 - flt(offer) / 100) + 0.5);
};
P.is_blank = (v) => v === null || v === undefined || v === "";

P.row_prices = async function (frm, cdt, cdn) {
	const row = locals[cdt][cdn];
	if (!row.item_code || !P.settings().purchase_pricing) return;
	let gst = flt(row.custom_gst_percent);
	if (row.item_tax_template) {
		if (!(row.item_tax_template in P.gst_cache)) {
			const r = await frappe.db.get_value("Item Tax Template", row.item_tax_template, "gst_rate");
			P.gst_cache[row.item_tax_template] = flt(r.message && r.message.gst_rate);
		}
		gst = P.gst_cache[row.item_tax_template];
	}
	const margin = P.is_blank(row.custom_margin_percent) ? P.default_margin() : flt(row.custom_margin_percent);
	const base = (flt(row.rate) * flt(frm.doc.conversion_rate || 1)) / (flt(row.conversion_factor) || 1);
	const mrp = base > 0 ? P.round_up(base * (1 + gst / 100) * (1 + margin / 100)) : 0;
	await frappe.model.set_value(cdt, cdn, {
		custom_gst_percent: gst,
		custom_selling_price: mrp,
		custom_offer_price: P.offer_price(mrp, row.custom_offer_percent),
	});
};

// a new row starts with the offer the item already sells with
P.current_offer = async function (frm, cdt, cdn) {
	const row = locals[cdt][cdn];
	if (!row.item_code || !P.settings().offers) return;
	const price = await frappe.xcall("rfid_retail.api.pricing.selling_price", { item_code: row.item_code });
	row.__offer_current = price ? price.offer : 0;
	row.__offer_set = 0;
	await frappe.model.set_value(cdt, cdn, "custom_offer_percent", row.__offer_current);
};

// an offer the user typed (also 0) is kept on save; see purchase.py set_row_prices
P.offer_changed = function (frm, cdt, cdn) {
	const row = locals[cdt][cdn];
	if (flt(row.custom_offer_percent) !== flt(row.__offer_current)) row.__offer_set = 1;
	return P.row_prices(frm, cdt, cdn);
};

// set item_code first and let ERPNext fetch the item, then the values
P.add_item_row = async function (frm, values) {
	let row = (frm.doc.items || []).find((r) => !r.item_code);
	if (!row) row = frm.add_child("items");
	await frappe.model.set_value(row.doctype, row.name, "item_code", values.item_code);
	// qty re-applies the price list rate, so the rate goes in after it
	await frappe.model.set_value(row.doctype, row.name, "qty", flt(values.qty) || 1);
	const rest = {};
	if (values.template) rest.custom_variant_of = values.template;
	if (!P.is_blank(values.margin)) rest.custom_margin_percent = values.margin;
	if (!P.is_blank(values.offer)) rest.custom_offer_percent = values.offer;
	if (Object.keys(rest).length) await frappe.model.set_value(row.doctype, row.name, rest);
	if (!P.is_blank(values.rate) && flt(values.rate) > 0) await frappe.model.set_value(row.doctype, row.name, "rate", values.rate);
	return row;
};

P.add_variants_dialog = function (frm) {
	const COMMON = ["qty", "rate", "margin", "offer"];
	const d = new frappe.ui.Dialog({
		title: __("Add Variants"),
		size: "extra-large",
		fields: [
			{
				fieldname: "template",
				fieldtype: "Link",
				options: "Item",
				label: __("Template Item"),
				reqd: 1,
				get_query: () => ({ filters: { has_variants: 1, disabled: 0 } }),
				onchange: () => load(),
			},
			{ fieldtype: "Section Break", label: __("For all variants"), description: __("Changing a value here changes it on every row below.") },
			{ fieldname: "qty", fieldtype: "Float", label: __("Qty"), default: 1, onchange: () => apply("qty") },
			{ fieldtype: "Column Break" },
			{ fieldname: "rate", fieldtype: "Currency", label: __("Rate"), onchange: () => apply("rate") },
			{ fieldtype: "Column Break" },
			{ fieldname: "margin", fieldtype: "Percent", label: __("Margin %"), default: P.default_margin(), onchange: () => apply("margin") },
			{ fieldtype: "Column Break" },
			{ fieldname: "offer", fieldtype: "Percent", label: __("Offer %"), onchange: () => apply("offer") },
			{ fieldtype: "Section Break", label: __("Variants"), description: __("Change any row on its own. Select rows and press Delete to leave them out.") },
			{
				fieldname: "variants",
				fieldtype: "Table",
				cannot_add_rows: true,
				in_place_edit: true,
				data: [],
				fields: [
					{ fieldname: "item_code", fieldtype: "Link", options: "Item", label: __("Item"), read_only: 1, in_list_view: 1, columns: 3 },
					{ fieldname: "attributes", fieldtype: "Data", label: __("Variant"), read_only: 1, in_list_view: 1, columns: 3 },
					{ fieldname: "qty", fieldtype: "Float", label: __("Qty"), in_list_view: 1, columns: 1 },
					{ fieldname: "rate", fieldtype: "Currency", label: __("Rate"), in_list_view: 1, columns: 1 },
					{ fieldname: "margin", fieldtype: "Percent", label: __("Margin %"), in_list_view: 1, columns: 1 },
					{ fieldname: "offer", fieldtype: "Percent", label: __("Offer %"), in_list_view: 1, columns: 1 },
				],
			},
		],
		primary_action_label: __("Add to Items"),
		primary_action: () => add(),
	});

	const table = () => d.fields_dict.variants;
	const common = () => {
		const v = {};
		COMMON.forEach((f) => (v[f] = d.get_value(f)));
		return v;
	};

	async function load() {
		const template = d.get_value("template");
		table().df.data = [];
		if (template) {
			const variants = await frappe.xcall("rfid_retail.api.pricing.get_variants", { template });
			if (!variants.length) frappe.show_alert({ message: __("{0} has no active variants.", [template]), indicator: "orange" });
			const v = common();
			table().df.data = variants.map((x) => ({ ...x, ...v }));
		}
		table().grid.refresh();
	}

	function apply(field) {
		const value = d.get_value(field);
		(table().df.data || []).forEach((row) => (row[field] = value));
		table().grid.refresh();
	}

	async function add() {
		const rows = (table().df.data || []).filter((r) => r.item_code && flt(r.qty) > 0);
		if (!rows.length) return frappe.msgprint(__("No variant with a quantity to add."));
		d.hide();
		frappe.dom.freeze(__("Adding {0} variants...", [rows.length]));
		try {
			const template = d.get_value("template");
			for (const r of rows) await P.add_item_row(frm, { ...r, template });
		} finally {
			frappe.dom.unfreeze();
		}
		frm.refresh_field("items");
		frappe.show_alert({ message: __("{0} variants added", [rows.length]), indicator: "green" });
	}

	d.show();
};

P.update_template_dialog = function (frm) {
	const templates = [...new Set((frm.doc.items || []).map((r) => r.custom_variant_of).filter(Boolean))];
	if (!templates.length) return frappe.msgprint(__("No variant rows in the items table yet. Use Add Variants first."));
	const number = (fieldname, label) => ({ fieldname, label, fieldtype: "Data", description: __("Blank = keep as it is") });
	const d = new frappe.ui.Dialog({
		title: __("Update Template Rows"),
		fields: [
			{ fieldname: "template", fieldtype: "Select", label: __("Template Item"), options: templates, default: templates[0], reqd: 1 },
			number("qty", __("Qty")),
			number("rate", __("Rate")),
			number("margin", __("Margin %")),
			number("offer", __("Offer %")),
		],
		primary_action_label: __("Update"),
		primary_action: async (v) => {
			const map = { qty: "qty", rate: "rate", margin: "custom_margin_percent", offer: "custom_offer_percent" };
			const values = {};
			for (const [key, field] of Object.entries(map)) {
				const raw = (v[key] || "").toString().trim();
				if (!raw) continue;
				if (isNaN(Number(raw))) return frappe.msgprint(__("{0} must be a number.", [key]));
				values[field] = flt(raw);
			}
			if (!Object.keys(values).length) return frappe.msgprint(__("Enter at least one value."));
			const rows = frm.doc.items.filter((r) => r.custom_variant_of === v.template);
			d.hide();
			for (const r of rows) await frappe.model.set_value(r.doctype, r.name, values);
			frappe.show_alert({ message: __("{0} rows of {1} updated", [rows.length, v.template]), indicator: "green" });
		},
	});
	d.show();
};

["Purchase Order", "Purchase Receipt", "Purchase Invoice"].forEach((doctype) => {
	frappe.ui.form.on(doctype, {
		refresh(frm) {
			if (frm.doc.docstatus !== 0 || !P.settings().variant_buttons) return;
			const grid = frm.fields_dict.items.grid;
			grid.add_custom_button(__("Add Variants"), () => P.add_variants_dialog(frm));
			grid.add_custom_button(__("Update Template"), () => P.update_template_dialog(frm));
		},
	});
	frappe.ui.form.on(`${doctype} Item`, {
		item_code: (frm, cdt, cdn) => P.settings().purchase_pricing && P.current_offer(frm, cdt, cdn),
		rate: P.row_prices,
		custom_margin_percent: P.row_prices,
		custom_offer_percent: P.offer_changed,
		item_tax_template: P.row_prices,
		conversion_factor: P.row_prices,
	});
});

// ------------------------------------------------------------ offers on items

P.offer_dialog = function (items, on_done) {
	frappe.prompt(
		{
			fieldname: "offer",
			fieldtype: "Percent",
			label: __("Offer %"),
			reqd: 1,
			description: __("Selling rate = MRP less this. 0 ends the offer. A template item changes all its variants."),
		},
		async ({ offer }) => {
			const r = await frappe.xcall("rfid_retail.api.pricing.set_offer", { items, offer });
			let msg = __("Offer {0}% set on {1} item(s).", [offer, r.updated.length]);
			if (r.missing.length) msg += "<br>" + __("No selling price yet (skipped): {0}", [r.missing.join(", ")]);
			frappe.msgprint(msg, __("Offer"));
			on_done && on_done();
		},
		__("Set Offer"),
		__("Set")
	);
};

frappe.ui.form.on("Item", {
	refresh(frm) {
		if (!frm.is_new() && P.settings().offers) P.show_selling_price(frm);
	},
});

P.show_selling_price = async function (frm) {
	if (frm.doc.has_variants) {
		frm.add_custom_button(__("Set Offer on Variants"), () => P.offer_dialog([frm.doc.name]), __("Selling Price"));
		return;
	}
	const p = await frappe.xcall("rfid_retail.api.pricing.selling_price", { item_code: frm.doc.name });
	$(frm.layout.wrapper).find(".rfid-selling-price").remove();
	if (!p) return;
	const money = (v) => format_currency(v);
	const offer = p.offer ? ` · ${__("Offer")} <b>${p.offer}%</b> · ${__("Selling")} <b>${money(p.rate)}</b>` : "";
	// own element under the form messages: ERPNext's variant / template note resets those on refresh
	if (frm.doc.name !== p.item_code) return;
	const $layout = $(frm.layout.wrapper);
	$layout.find(".rfid-selling-price").remove();
	$(`<div class="form-message ${p.offer ? "green" : "blue"} rfid-selling-price">
		${__("MRP")} <b>${money(p.mrp)}</b>${offer} <span class="text-muted">(${p.price_list})</span>
	</div>`).insertAfter($layout.children(".form-message-container").first());
	frm.add_custom_button(__("Change Offer"), () => P.offer_dialog([frm.doc.name], () => frm.refresh()), __("Selling Price"));
	frm.add_custom_button(__("Open Item Price"), () => frappe.set_route("Form", "Item Price", p.name), __("Selling Price"));
};

// Selling Item Price: Rate = MRP less Offer % (worked out again on save)
P.item_price_rate = function (frm) {
	if (!frm.doc.selling || !P.settings().offers || !(flt(frm.doc.custom_mrp) > 0)) return;
	frm.set_value("price_list_rate", P.offer_price(flt(frm.doc.custom_mrp), frm.doc.custom_offer_percent));
};
frappe.ui.form.on("Item Price", {
	custom_mrp: P.item_price_rate,
	custom_offer_percent: P.item_price_rate,
});
