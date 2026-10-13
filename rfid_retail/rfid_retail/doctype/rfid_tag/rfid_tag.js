// Copyright (c) 2026, MicroNXT Solutions Pvt. Ltd. and contributors
// For license information, please see license.txt

const RFID_EVENT_COLORS = {
	Assigned: "green",
	Restored: "green",
	Found: "green",
	Returned: "blue",
	Sold: "blue",
	Freed: "gray",
	Detached: "gray",
	Transferred: "purple",
	Reserved: "yellow",
	"Reservation Released": "gray",
	Replaced: "orange",
	Damaged: "red",
	Lost: "red",
	Retired: "red",
	Rejected: "red",
	"Exit Alert": "red",
};

frappe.ui.form.on("RFID Tag", {
	refresh(frm) {
		frm.disable_save();
		render_history(frm);
		const repair = (status, label) =>
			frm.add_custom_button(
				label,
				() =>
					frappe.prompt(
						{ fieldname: "reason", fieldtype: "Small Text", label: __("Why?"), reqd: 1 },
						({ reason }) =>
							frappe
								.xcall("rfid_retail.api.mapping.set_condition", { epc: frm.doc.name, status, reason })
								.then(() => frm.reload_doc()),
						label
					),
				__("Tag condition")
			);
		if (frm.doc.status === "Lost") repair("Found", __("Found again"));
		else if (!["Damaged", "Retired"].includes(frm.doc.status)) {
			repair("Damaged", __("Damaged"));
			repair("Lost", __("Lost"));
			repair("Retired", __("Retire"));
		}
	},
});

function render_history(frm) {
	const wrapper = frm.get_field("history_html").$wrapper;
	wrapper.html(`<p class="text-muted">${__("Loading history...")}</p>`);
	frappe.xcall("rfid_retail.api.mapping.tag_story", { code: frm.doc.name }).then((story) => {
		const esc = frappe.utils.escape_html;
		const link = (dt, dn) => (dt && dn ? `<a href="/app/${frappe.router.slug(dt)}/${encodeURIComponent(dn)}">${esc(dn)}</a>` : "");
		const rows = story.events
			.map((e) => {
				const garment = [e.item_name || e.item_code, e.serial_no, e.component].filter(Boolean).map(esc).join(" · ");
				const place = e.to_warehouse ? `${esc(e.warehouse)} → ${esc(e.to_warehouse)}` : esc(e.warehouse || "");
				return `<div style="display:flex;gap:12px;padding:8px 0;border-bottom:1px solid var(--border-color)">
					<span class="indicator-pill ${RFID_EVENT_COLORS[e.action] || "gray"}" style="min-width:110px;justify-content:center">${esc(__(e.action))}</span>
					<div style="flex:1">
						<div>${garment || `<span class="text-muted">${__("no garment")}</span>`}${place ? ` <span class="text-muted">· ${place}</span>` : ""}</div>
						<div class="small text-muted">${frappe.datetime.str_to_user(e.event_time)} · ${esc(e.by || "")}
							${e.reference_name ? ` · ${link(e.reference_doctype, e.reference_name)}` : ""}
							${e.amount ? ` · ${format_currency(e.amount)}` : ""}
							${e.cycle ? ` · ${__("use")} ${e.cycle}` : ""}</div>
						${e.reason ? `<div class="small">${esc(e.reason)}</div>` : ""}
					</div></div>`;
			})
			.join("");
		wrapper.html(rows || `<p class="text-muted">${__("No history yet.")}</p>`);
	});
}
