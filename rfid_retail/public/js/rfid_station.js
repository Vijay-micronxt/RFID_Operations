/* RFID stations: thin client over rfid_retail.api.*. Every rule lives server-side. */
(() => {
	const $ = (id) => document.getElementById(id);
	const esc = (v) =>
		String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
	const money = (v) => Number(v || 0).toLocaleString("en-IN", { style: "currency", currency: "INR" });
	const lines = (text) => text.split(/[\s,]+/).map((s) => s.trim()).filter(Boolean);

	async function call(method, args = {}, post = true) {
		const url = `/api/method/rfid_retail.api.${method}`;
		const opts = { headers: { Accept: "application/json", "X-Frappe-CSRF-Token": RFID.csrf } };
		let res;
		if (post) {
			opts.method = "POST";
			opts.headers["Content-Type"] = "application/json";
			opts.body = JSON.stringify(args);
			res = await fetch(url, opts);
		} else {
			res = await fetch(`${url}?${new URLSearchParams(args)}`, opts);
		}
		const data = await res.json().catch(() => ({}));
		if (!res.ok) {
			let msg = data.exception || res.statusText;
			try {
				msg = JSON.parse(JSON.parse(data._server_messages)[0]).message;
			} catch (e) {}
			toast(msg.replace(/<[^>]+>/g, ""), true);
			throw new Error(msg);
		}
		return data.message;
	}

	function toast(msg, bad) {
		const t = $("toast");
		t.textContent = msg;
		t.className = bad ? "show bad" : "show";
		clearTimeout(t._h);
		t._h = setTimeout(() => (t.className = ""), 4500);
	}

	// ------------------------------------------------------------ tabs + devices
	document.querySelectorAll("#tabs button").forEach((b) =>
		b.addEventListener("click", () => {
			document.querySelectorAll("#tabs button, .tab").forEach((x) => x.classList.remove("active"));
			b.classList.add("active");
			$(`tab-${b.dataset.tab}`).classList.add("active");
		})
	);
	document.querySelectorAll(".device-select").forEach((sel) => {
		const opts = RFID.devices.filter((d) => d.device_type === sel.dataset.type);
		sel.innerHTML = '<option value="">(none)</option>' + opts.map((d) => `<option>${esc(d.name)}</option>`).join("");
		if (opts.length) sel.value = opts[0].name;
	});

	// ------------------------------------------------------------ counter
	const C = { counters: [], basket: null, view: null, customer: null };

	async function loadCounters() {
		C.counters = await call("pos.get_counters", {}, false);
		$("c-counter").innerHTML = C.counters.map((c) => `<option>${esc(c.name)}</option>`).join("");
		renderPayments();
	}
	const counter = () => C.counters.find((c) => c.name === $("c-counter").value);

	function renderPayments() {
		const c = counter();
		$("c-payments").innerHTML = (c?.payments || [])
			.map(
				(p) => `<label class="pay">${esc(p.mode_of_payment)}
				<input type="number" min="0" step="0.01" data-mode="${esc(p.mode_of_payment)}" value="0"></label>`
			)
			.join("");
	}
	$("c-counter").addEventListener("change", renderPayments);

	function renderBasket(v) {
		C.view = v;
		C.basket = v.basket;
		$("c-basket").textContent = `${v.basket} · ${v.status} · ${v.read_count} unique reads`;
		$("c-lines").innerHTML = v.lines.length
			? v.lines
					.map((ln) => {
						const pieces = ln.is_set
							? ln.pieces.map((p) => `<span class="chip ${p.verified ? "ok" : "bad"}">${esc(p.role)}</span>`).join("")
							: '<span class="chip ok">single</span>';
						return `<tr class="${ln.complete ? "" : "blocked"}">
							<td><b>${esc(ln.item_name)}</b><br><small>${esc(ln.item_code)}</small></td>
							<td>${esc(ln.serial_no)}</td><td>${pieces}${ln.complete ? "" : '<div class="warn">Incomplete set - read missing pieces</div>'}</td>
							<td class="num">${ln.complete ? money(ln.rate) : "—"}</td>
							<td><button class="btn ghost small" data-remove="${esc(ln.serial_no)}">Remove</button></td></tr>`;
					})
					.join("")
			: '<tr><td colspan="5" class="muted">Tray is empty.</td></tr>';
		const t = v.totals || {};
		$("c-totals").innerHTML = `<dt>Net</dt><dd>${money(t.net_total)}</dd><dt>GST</dt><dd>${money(t.total_taxes)}</dd>
			<dt class="grand">Total</dt><dd class="grand">${money(t.rounded_total)}</dd>`;
		$("c-checkout").disabled = !v.lines.length || v.blocked || v.status !== "Open";
		const first = document.querySelector("#c-payments input");
		if (first && !C.touchedPay) first.value = t.rounded_total || 0;
	}

	function renderEvents(events) {
		$("c-events").innerHTML = (events || [])
			.map((e) => `<div class="event ${e.level}">${esc(e.code)}: ${esc(e.message)}</div>`)
			.join("");
	}

	$("c-open").onclick = async () => {
		C.touchedPay = false;
		$("c-result").innerHTML = "";
		const dev = counter()?.devices?.[0] || null;
		renderBasket(await call("pos.open_basket", { pos_profile: $("c-counter").value, device: dev }));
		$("c-reads").focus();
	};
	$("c-read").onclick = async () => {
		if (!C.basket) return toast("Start a basket first", true);
		const codes = lines($("c-reads").value);
		if (!codes.length) return;
		const v = await call("pos.scan", { basket: C.basket, codes, source: $("c-label").checked ? "Barcode" : "RFID" });
		$("c-reads").value = "";
		renderBasket(v);
		renderEvents(v.events);
	};
	$("c-reads").addEventListener("keydown", (e) => {
		if (e.key === "Enter" && !e.shiftKey) {
			e.preventDefault();
			$("c-read").click();
		}
	});
	$("c-lines").addEventListener("click", async (e) => {
		const s = e.target.dataset.remove;
		if (s) renderBasket(await call("pos.remove_unit", { basket: C.basket, serial_no: s }));
	});
	$("c-payments").addEventListener("input", () => (C.touchedPay = true));
	$("c-find").onclick = async () => {
		const found = await call("pos.find_customer", { mobile: $("c-mobile").value }, false);
		if (found.length) return pickCustomer(found[0]);
		$("c-cust").textContent = "Not found - add as new customer";
		$("c-newcust").classList.remove("hidden");
	};
	$("c-create").onclick = async () => {
		const name = await call("pos.create_customer", { customer_name: $("c-custname").value, mobile: $("c-mobile").value });
		$("c-newcust").classList.add("hidden");
		pickCustomer({ name, customer_name: $("c-custname").value });
	};
	async function pickCustomer(c) {
		C.customer = c.name;
		const lp = await call("pos.loyalty_balance", { customer: c.name }, false);
		$("c-cust").innerHTML = `<b>${esc(c.customer_name)}</b> · ${esc(c.mobile_no || "")}<br>
			${lp.program ? `${esc(lp.program)}: <b>${lp.points}</b> points` : "No loyalty program"}`;
		$("c-loyalty").classList.toggle("hidden", !lp.points);
	}
	$("c-checkout").onclick = async () => {
		const payments = [...document.querySelectorAll("#c-payments input")].map((i) => ({
			mode_of_payment: i.dataset.mode,
			amount: Number(i.value || 0),
		}));
		$("c-checkout").disabled = true;
		try {
			const r = await call("pos.checkout", {
				basket: C.basket,
				payments,
				customer: C.customer,
				redeem_points: Number($("c-redeem").value || 0),
			});
			$("c-result").innerHTML = `<div class="event ok">Sale complete: <b>${esc(r.sales_invoice)}</b>
				${r.change_amount ? ` · change ${money(r.change_amount)}` : ""}<br>
				<a target="_blank" href="/printview?doctype=Sales%20Invoice&name=${encodeURIComponent(r.sales_invoice)}&trigger_print=1">Print receipt</a> ·
				<a target="_blank" href="/app/sales-invoice/${encodeURIComponent(r.sales_invoice)}">Open invoice</a><br>
				Now detach the tags (Detach tab).</div>`;
			renderBasket(await call("pos.basket_view", { basket: C.basket }, false));
			C.customer = null;
			$("c-cust").textContent = "Walk-in customer";
		} finally {
			$("c-checkout").disabled = C.view?.status !== "Open";
		}
	};
	$("c-cancel").onclick = async () => {
		if (!C.basket || !confirm("Cancel this basket? Garments return to the floor.")) return;
		await call("pos.cancel_basket", { basket: C.basket });
		renderBasket(await call("pos.basket_view", { basket: C.basket }, false));
	};

	// ------------------------------------------------------------ mapping
	let M = null;
	function renderUnit(u) {
		M = u;
		$("m-component").value = "";
		const comps = u.components
			? `<table class="lines"><thead><tr><th>Piece</th><th>Label</th><th>Status</th><th>Tag</th><th></th></tr></thead><tbody>
				${u.components
					.map(
						(c) => `<tr><td>${esc(c.role)}</td><td>${esc(c.name)}</td><td>${esc(c.status)}</td><td>${esc(c.rfid_tag || "—")}</td>
						<td>${c.rfid_tag ? "" : `<button class="btn small" data-pick="${esc(c.name)}">Tag this</button>`}
						${c.status === "Tagged" ? `<button class="btn ghost small" data-alter="${esc(c.name)}">Send to alteration</button>` : ""}
						${c.status === "Out for Alteration" ? `<button class="btn ghost small" data-back="${esc(c.name)}">Back from alteration</button>` : ""}</td></tr>`
					)
					.join("")}</tbody></table>`
			: `<p>Standalone garment · tag: <b>${esc(u.tag || "none")}</b></p>`;
		$("m-detail").innerHTML = `<h3>${esc(u.item_name)} <small>${esc(u.item_code)}</small></h3>
			<p>Unit <b>${esc(u.name)}</b> · ${esc(u.warehouse || "")} · RFID status <span class="chip">${esc(u.custom_rfid_status)}</span>
			${u.custom_rfid_status === "Returned - Hold" ? '<button class="btn small" id="m-release">Return checked - release</button>' : ""}</p>
			${comps}
			<h4>Tag history</h4><table class="lines"><tbody>${u.history
				.map(
					(h) => `<tr><td>${esc(h.tag)}</td><td>${esc(h.role || "")}</td><td>${esc(h.status)}</td><td>${esc(h.assigned_on)}</td>
					<td>${esc(h.close_reason || "")}</td><td>${esc(h.sales_invoice || "")}</td></tr>`
				)
				.join("") || '<tr><td class="muted">No tags yet</td></tr>'}</tbody></table>`;
	}
	$("m-load").onclick = async () => renderUnit(await call("mapping.unit_detail", { serial_no: $("m-unit").value }, false));
	$("m-unit").addEventListener("keydown", (e) => e.key === "Enter" && $("m-load").click());
	$("m-pending").onclick = async () => {
		const rows = await call("mapping.pending_units", {}, false);
		$("m-detail").innerHTML = `<table class="lines"><thead><tr><th>Unit</th><th>Item</th><th>Status</th><th></th></tr></thead><tbody>
			${rows
				.map(
					(r) => `<tr><td>${esc(r.name)}</td><td>${esc(r.item_name)}</td><td>${esc(r.custom_rfid_status)}${r.custom_is_garment_set ? " · set" : ""}</td>
					<td><button class="btn small" data-open="${esc(r.name)}">Open</button></td></tr>`
				)
				.join("") || '<tr><td class="muted">Nothing waiting</td></tr>'}</tbody></table>`;
	};
	$("m-detail").addEventListener("click", async (e) => {
		const d = e.target.dataset;
		if (d.open) {
			$("m-unit").value = d.open;
			return $("m-load").click();
		}
		if (d.pick) {
			$("m-component").value = d.pick;
			return $("m-epc").focus();
		}
		if (d.alter) {
			const who = prompt("Tailor / custodian name?");
			if (who) renderUnit(await call("mapping.set_alteration", { component: d.alter, out: 1, custodian: who }));
		}
		if (d.back) renderUnit(await call("mapping.set_alteration", { component: d.back, out: 0 }));
		if (e.target.id === "m-release") renderUnit(await call("mapping.release_return", { serial_no: M.name }));
	});
	$("m-map").onclick = async () => {
		if (!M) return toast("Open a unit first", true);
		renderUnit(
			await call("mapping.map_tag", {
				epc: $("m-epc").value,
				serial_no: M.name,
				component: $("m-component").value || null,
				device: $("m-device").value || null,
			})
		);
		$("m-epc").value = "";
		toast("Tag bound");
	};
	$("m-epc").addEventListener("keydown", (e) => e.key === "Enter" && $("m-map").click());
	$("m-replace").onclick = async () => {
		renderUnit(
			await call("mapping.replace_tag", {
				old_epc: $("m-old").value,
				new_epc: $("m-new").value,
				reason: $("m-reason").value,
				device: $("m-device").value || null,
			})
		);
		toast("Tag replaced; old tag retired");
	};

	// ------------------------------------------------------------ detach
	$("d-go").onclick = async () => {
		const out = await call("mapping.detach", { codes: lines($("d-reads").value) });
		$("d-reads").value = "";
		$("d-out").innerHTML = out.map((r) => `<div class="event ${r.ok ? "ok" : "error"}">${esc(r.epc)}: ${esc(r.message)}</div>`).join("");
	};

	// ------------------------------------------------------------ gate
	$("g-go").onclick = async () => {
		const r = await call("gate.gate_read", { codes: lines($("g-reads").value), device: $("g-device").value || null });
		$("g-reads").value = "";
		$("g-light").className = `light ${r.alarm ? "alarm" : "clear"}`;
		$("g-light").textContent = r.alarm ? "ALARM - unpaid garment" : "Clear";
		$("g-out").innerHTML = r.events
			.map((e) => `<div class="event ${e.alarm ? "error" : "ok"}">${esc(e.epc)}: ${esc(e.message)}</div>`)
			.join("");
	};

	// ------------------------------------------------------------ audit
	let A = null;
	function renderAudit(s) {
		A = s.name;
		$("a-session").value = s.name;
		const res = Object.entries(s.results || {})
			.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${v}</dd>`)
			.join("");
		$("a-summary").innerHTML = `<b>${esc(s.name)}</b> · <span class="chip">${esc(s.status)}</span><br>
			${esc(s.warehouse)} · cut-off ${esc(s.cutoff || "")}<br>
			Expected units ${s.expected_units} (${s.expected_identities} tagged pieces) · ${s.observations} unique reads in ${s.uploads} uploads<br>
			${s.movements_after_cutoff ? `<span class="warn">${s.movements_after_cutoff} stock movements after cut-off</span><br>` : ""}
			${s.note ? `<span class="warn">${esc(s.note)}</span>` : ""}
			<dl class="totals">${res}</dl>
			<a target="_blank" href="/app/rfid-audit-session/${encodeURIComponent(s.name)}">Open full result &amp; investigate</a>`;
	}
	$("a-start").onclick = async () =>
		renderAudit(await call("audit.start", { warehouse: $("a-wh").value, device: $("a-device").value || null }));
	$("a-open").onclick = async () => renderAudit(await call("audit.summary", { session: $("a-session").value }, false));
	$("a-upload").onclick = async () => {
		if (!A) return toast("Start or open a session", true);
		renderAudit(
			await call("audit.upload", {
				session: A,
				batch_id: `${$("a-device").value || "HH"}-${Date.now()}`,
				codes: lines($("a-reads").value),
				area: $("a-area").value,
				device: $("a-device").value || null,
			})
		);
		$("a-reads").value = "";
	};
	$("a-compare").onclick = async () => A && renderAudit(await call("audit.compare", { session: A }));
	$("a-submit").onclick = async () => A && renderAudit(await call("audit.submit_for_review", { session: A }));

	const first = document.querySelector("#tabs button");
	if (first) $(`tab-${first.dataset.tab}`).classList.add("active");
	if (RFID.tabs.includes("counter")) loadCounters().catch(() => {});
})();
