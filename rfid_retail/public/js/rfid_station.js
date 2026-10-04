/* RFID stations: a thin client over rfid_retail.api.*. Every rule lives on the server. */
(() => {
	const $ = (id) => document.getElementById(id);
	const esc = (v) =>
		String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
	const codesOf = (text) => text.split(/[\s,]+/).map((s) => s.trim()).filter(Boolean);
	const icon = (name, cls = "") => `<span class="ms ${cls}">${name}</span>`;

	async function call(method, args = {}, post = true) {
		const url = `/api/method/rfid_retail.api.${method}`;
		const headers = { Accept: "application/json", "X-Frappe-CSRF-Token": RFID.csrf };
		const res = post
			? await fetch(url, { method: "POST", headers: { ...headers, "Content-Type": "application/json" }, body: JSON.stringify(args) })
			: await fetch(`${url}?${new URLSearchParams(args)}`, { headers });
		const data = await res.json().catch(() => ({}));
		if (!res.ok) {
			let msg = data.exception || res.statusText;
			try {
				msg = JSON.parse(JSON.parse(data._server_messages)[0]).message;
			} catch (e) {}
			msg = String(msg).replace(/<[^>]+>/g, "").replace(/^[\w.]+Error: /, "");
			snack(msg, true);
			throw new Error(msg);
		}
		return data.message;
	}

	function snack(msg, bad = false) {
		const s = $("snackbar");
		s.textContent = msg;
		s.className = `show${bad ? " bad" : ""}`;
		clearTimeout(s._t);
		s._t = setTimeout(() => (s.className = ""), 3500);
		if (bad && navigator.vibrate) navigator.vibrate(200);
	}

	/** Enter (or a reader's trailing newline) submits; pasting many codes submits at once. */
	function onScan(input, handler) {
		const fire = async () => {
			const codes = codesOf(input.value);
			input.value = "";
			if (!codes.length) return;
			input.disabled = true;
			try {
				await handler(codes);
			} catch (e) {
				/* already shown */
			} finally {
				input.disabled = false;
				input.focus();
			}
		};
		input.addEventListener("keydown", (e) => {
			if (e.key === "Enter") {
				e.preventDefault();
				fire();
			}
		});
		input.addEventListener("paste", () => setTimeout(() => codesOf(input.value).length > 1 && fire(), 0));
	}

	function sheet(title, items) {
		const back = document.createElement("div");
		back.className = "sheet-back";
		back.innerHTML = `<div class="sheet"><h3>${esc(title)}</h3>${items
			.map((it, i) => `<button class="menu-item" data-i="${i}">${icon(it.icon)}<span>${esc(it.label)}</span></button>`)
			.join("")}<div class="sheet-foot"><button class="btn ghost">Cancel</button></div></div>`;
		back.addEventListener("click", (e) => {
			const btn = e.target.closest("[data-i]");
			back.remove();
			if (btn) items[btn.dataset.i].run();
		});
		document.body.appendChild(back);
	}

	/** Material dialog with one text field. Resolves to the trimmed text, or null on cancel. */
	function ask(title, placeholder) {
		return new Promise((resolve) => {
			const back = document.createElement("div");
			back.className = "dialog-back";
			back.innerHTML = `<div class="dialog"><h3>${esc(title)}</h3>
				<input class="field-lg" placeholder="${esc(placeholder)}">
				<div class="dialog-actions"><button class="btn text" data-r="0">Cancel</button><button class="btn text" data-r="1">OK</button></div></div>`;
			const input = back.querySelector("input");
			const close = (ok) => {
				back.remove();
				resolve(ok && input.value.trim() ? input.value.trim() : null);
			};
			back.addEventListener("click", (e) => {
				if (e.target === back) close(false);
				const btn = e.target.closest("[data-r]");
				if (btn) close(btn.dataset.r === "1");
			});
			input.addEventListener("keydown", (e) => e.key === "Enter" && close(true));
			document.body.appendChild(back);
			input.focus();
		});
	}

	// ------------------------------------------------------------ navigation: header tabs + hash (browser back works)
	const screens = [...document.querySelectorAll(".screen")];
	if (!screens.length) return; // RFID off, or billing only: the page shows a notice
	const onOpen = {};
	function show() {
		const name = location.hash.slice(1);
		const target = screens.find((s) => s.dataset.screen === name) || screens[0];
		screens.forEach((s) => s.classList.toggle("active", s === target));
		document.querySelectorAll("#tabs [data-tab]").forEach((t) => t.classList.toggle("active", t.dataset.tab === target.dataset.screen));
		onOpen[target.dataset.screen]?.();
		target.querySelector(".scan input")?.focus();
	}
	window.addEventListener("hashchange", show);

	document.querySelectorAll(".device-select").forEach((sel) => {
		const opts = RFID.devices.filter((d) => d.device_type === sel.dataset.type);
		sel.innerHTML = '<option value="">None</option>' + opts.map((d) => `<option>${esc(d.name)}</option>`).join("");
		if (opts.length) sel.value = opts[0].name;
	});

	// ------------------------------------------------------------ tag garments
	// Left: garments waiting for tags. Right: the chosen garment, one piece at a time.
	const M = { unit: null, current: null };
	const keyOf = (p) => p.name || "__single__";

	function pieces(u) {
		if (u.components) return u.components;
		return [{ name: null, role: u.item_name, status: u.tag ? "Tagged" : "Pending Tag", rfid_tag: u.tag }];
	}

	function pickNext() {
		const next = pieces(M.unit).find((p) => !p.rfid_tag && p.status !== "Out for Alteration");
		M.current = next ? keyOf(next) : null;
	}

	function highlightPending() {
		document.querySelectorAll("#m-pending [data-open]").forEach((b) => b.classList.toggle("selected", b.dataset.open === M.unit?.name));
	}

	function renderUnit() {
		const u = M.unit;
		$("m-empty").hidden = !!u;
		$("m-tagging").hidden = !u;
		highlightPending();
		if (!u) return;
		const list = pieces(u);
		const done = list.filter((p) => p.rfid_tag).length;
		const ready = u.custom_rfid_status === "Ready";
		$("m-name").textContent = u.item_name;
		$("m-meta").textContent = `${u.name} · ${u.warehouse || ""}`;
		$("m-status").textContent = u.custom_rfid_status || "";
		$("m-status").className = `badge${ready ? " ok" : ""}`;
		$("m-bar").style.width = `${(done / list.length) * 100}%`;
		$("m-progress").textContent = `${done} of ${list.length} piece${list.length > 1 ? "s" : ""} tagged`;

		// A customer return is checked first; tagging only starts after "All pieces checked".
		const returned = u.custom_rfid_status === "Returned - Hold";
		if (returned) M.current = null;
		const current = list.find((p) => keyOf(p) === M.current);
		$("m-return").hidden = !returned;
		$("m-now").hidden = !current;
		$("m-done").hidden = !!current || !ready;
		if (current) {
			$("m-piece").textContent = current.role;
			$("m-tag").value = "";
			setTimeout(() => $("m-tag").focus(), 0);
		}

		$("m-tip").hidden = !list.some((p) => p.rfid_tag);

		$("m-steps").innerHTML = list
			.map((p, n) => {
				const k = keyOf(p);
				const away = p.status === "Out for Alteration";
				const state = p.rfid_tag ? "done" : k === M.current ? "now" : away ? "away" : "todo";
				const dot = state === "done" ? icon("check", "small") : state === "away" ? icon("content_cut", "small") : n + 1;
				const sub = p.rfid_tag
					? "Tagged"
					: away
						? "At alteration"
						: returned
							? "Check this piece came back"
							: state === "now"
								? "Waiting for its tag"
								: "Click to tag this one next";
				const right = p.rfid_tag || away
					? `${p.rfid_tag ? `<span class="tagchip">${esc(p.rfid_tag)}</span>` : ""}${icon("more_vert", "muted")}`
					: state === "now"
						? '<span class="nowchip">NOW</span>'
						: "";
				return `<button class="item ${state}" data-piece="${esc(k)}"><span class="dot">${dot}</span>
					<span class="grow"><b>${esc(p.role)}</b><small>${sub}</small></span>${right}</button>`;
			})
			.join("");
	}

	async function openUnit(code) {
		M.unit = await call("mapping.unit_detail", { serial_no: code }, false);
		pickNext();
		renderUnit();
		// On phones the work panel sits below the list.
		if (window.matchMedia("(max-width: 860px)").matches) $("m-tagging").scrollIntoView({ behavior: "smooth" });
	}

	async function loadPending() {
		const rows = await call("mapping.pending_units", {}, false).catch(() => []);
		$("m-count").textContent = rows.length;
		$("m-pending").innerHTML =
			rows
				.map(
					(r) => `<button class="item" data-open="${esc(r.name)}">
						<span class="thumb">${icon(r.custom_is_garment_set ? "checkroom" : "apparel")}</span>
						<span class="grow"><b>${esc(r.item_name)}</b><small>${esc(r.name)}</small></span>
						${r.custom_rfid_status === "Returned - Hold" ? '<span class="chip-s ret">Returned</span>' : r.retag ? '<span class="chip-s ret">Re-tag</span>' : '<span class="chip-s">New</span>'}
						${icon("chevron_right", "muted")}</button>`
				)
				.join("") || `<div class="empty-row">Nothing waiting. New stock appears here after a purchase receipt.</div>`;
		highlightPending();
	}
	onOpen.mapping = () => {
		loadPending();
		renderUnit();
	};

	onScan($("m-code"), (codes) => openUnit(codes[0]));
	onScan($("m-tag"), async (codes) => {
		if (!M.unit || !M.current) return;
		const piece = M.current === "__single__" ? null : M.current;
		M.unit = await call("mapping.map_tag", { epc: codes[0], serial_no: M.unit.name, component: piece, device: null });
		snack("Tag saved");
		pickNext();
		renderUnit();
		if (!M.current) loadPending();
	});

	$("m-pending").addEventListener("click", (e) => {
		const b = e.target.closest("[data-open]");
		if (b) openUnit(b.dataset.open);
	});
	$("m-steps").addEventListener("click", (e) => {
		const row = e.target.closest("[data-piece]");
		const p = row && pieces(M.unit).find((x) => keyOf(x) === row.dataset.piece);
		if (!p || M.unit.custom_rfid_status === "Returned - Hold") return;
		if (p.rfid_tag || p.status === "Out for Alteration") return pieceMenu(p);
		M.current = row.dataset.piece;
		renderUnit();
	});
	$("m-release").addEventListener("click", async () => {
		M.unit = await call("mapping.release_return", { serial_no: M.unit.name });
		snack("Return accepted. Now tag the pieces again.");
		pickNext();
		renderUnit();
		loadPending();
	});
	$("m-next").addEventListener("click", () => {
		M.unit = null;
		renderUnit();
		loadPending();
		$("m-code").focus();
	});
	/** Actions for one piece, opened by clicking its row. */
	function pieceMenu(p) {
		const items = [];
		if (p.rfid_tag)
			items.push({ icon: "sync", label: "Tag is damaged - put a new tag", run: async () => {
				const fresh = await ask(`New tag for ${p.role}`, "Scan or type the new tag");
				if (!fresh) return;
				M.unit = await call("mapping.replace_tag", { old_epc: p.rfid_tag, new_epc: fresh, reason: "Tag damaged" });
				snack("Old tag retired, new tag saved");
				renderUnit();
			} });
		if (p.name && p.status === "Tagged")
			items.push({ icon: "content_cut", label: "Send for alteration", run: async () => {
				const who = await ask("Who is altering it?", "Tailor name");
				if (!who) return;
				M.unit = await call("mapping.set_alteration", { component: p.name, out: 1, custodian: who });
				renderUnit();
			} });
		if (p.name && p.status === "Out for Alteration")
			items.push({ icon: "undo", label: "Back from alteration", run: async () => {
				M.unit = await call("mapping.set_alteration", { component: p.name, out: 0 });
				pickNext();
				renderUnit();
			} });
		if (items.length) sheet(p.rfid_tag ? `${p.role} · tag ${p.rfid_tag}` : p.role, items);
	}

	// ------------------------------------------------------------ remove tags
	let released = 0;
	onScan($("d-scan"), async (codes) => {
		const out = await call("mapping.detach", { codes });
		const ok = out.filter((r) => r.ok).length;
		released += ok;
		$("d-count").textContent = released;
		$("d-empty").hidden = true;
		snack(ok ? `${ok} tag${ok > 1 ? "s" : ""} released` : "Nothing released", !ok);
		$("d-out").insertAdjacentHTML(
			"afterbegin",
			out
				.map(
					(r) => `<div class="row"><span class="lead ${r.ok ? "ok" : "bad"}">${icon(r.ok ? "check" : "block", "small")}</span>
					<div class="grow"><b>${esc(r.message)}</b><small>${esc(r.epc)}</small></div></div>`
				)
				.join("")
		);
	});

	// ------------------------------------------------------------ exit gate
	onScan($("g-scan"), async (codes) => {
		const r = await call("gate.gate_read", { codes, device: $("g-device").value || null });
		const light = $("g-light");
		light.className = `gate ${r.alarm ? "alarm" : "clear"}`;
		light.innerHTML = r.alarm ? `${icon("warning")}<b>Unpaid item - stop</b>` : `${icon("verified_user")}<b>All clear</b>`;
		if (r.alarm && navigator.vibrate) navigator.vibrate([300, 100, 300]);
		$("g-out").innerHTML = r.events
			.map(
				(e) => `<div class="row"><span class="lead ${e.alarm ? "bad" : "ok"}">${icon(e.alarm ? "priority_high" : "check", "small")}</span>
				<div class="grow"><b>${esc(e.message)}</b><small>${esc(e.epc)}</small></div></div>`
			)
			.join("");
	});

	// ------------------------------------------------------------ stock count
	const A = { session: null, area: "Sales Floor", reads: new Set() };
	const store = {
		get: () => {
			try {
				return JSON.parse(localStorage.getItem("rfid_audit") || "null");
			} catch (e) {
				return null;
			}
		},
		set: (v) => {
			try {
				localStorage.setItem("rfid_audit", JSON.stringify(v));
			} catch (e) {}
		},
	};

	function renderAudit(s) {
		A.session = s.name;
		store.set({ session: s.name, day: new Date().toDateString() });
		$("a-start").hidden = true;
		$("a-run").hidden = false;
		const r = s.results || {};
		const stat = (label, n, cls) => `<div class="stat ${n ? cls : ""}"><b>${n || 0}</b><small>${label}</small></div>`;
		$("a-summary").innerHTML = `<div class="work-head"><div><h2>${esc(s.name)}</h2>
				<p class="muted">${esc(s.warehouse)} · ${s.expected_units} units expected · ${s.observations} tags received</p></div>
				<span class="badge">${esc(s.status)}</span></div>
			${s.note ? `<p class="muted">${esc(s.note)}</p>` : ""}
			${
				Object.keys(r).length
					? `<div class="stats">${stat("Found", r.Matched, "ok")}${stat("Not found yet", r.Unverified, "bad")}${stat(
							"Sets missing a piece",
							r["Incomplete Set"],
							"warn"
						)}${stat("Unknown or extra tags", (r.Unexpected || 0) + (r.Unmapped || 0), "warn")}</div>
						<a class="btn ghost" href="/app/rfid-audit-session/${encodeURIComponent(s.name)}">${icon("open_in_new", "small")}Open full result</a>`
					: `<div class="placeholder"><span class="ms">barcode_reader</span><h3>Counting</h3><p>Scan each area, upload, then press Compare.</p></div>`
			}`;
		$("a-submit").disabled = !["Comparison Ready", "Investigation Pending"].includes(s.status);
		$("a-discard").hidden = ["Submitted for Review", "Reviewed"].includes(s.status);
	}

	onOpen.audit = async () => {
		const saved = store.get();
		if (!A.session && saved && saved.day === new Date().toDateString()) {
			const s = await call("audit.summary", { session: saved.session }, false).catch(() => null);
			if (s && !["Submitted for Review", "Reviewed"].includes(s.status)) renderAudit(s);
		}
	};

	$("a-begin").addEventListener("click", async () => {
		renderAudit(await call("audit.start", { warehouse: $("a-wh").value, device: $("a-device").value || null }));
	});
	$("a-areas").addEventListener("click", (e) => {
		const chip = e.target.closest("[data-area]");
		if (!chip) return;
		document.querySelectorAll("#a-areas .chip").forEach((c) => c.classList.toggle("on", c === chip));
		A.area = chip.dataset.area;
	});
	onScan($("a-scan"), async (codes) => {
		codes.forEach((c) => A.reads.add(c.toUpperCase()));
		$("a-pending").textContent = A.reads.size;
	});
	$("a-upload").addEventListener("click", async () => {
		if (!A.reads.size) return snack("Scan some tags first", true);
		const s = await call("audit.upload", {
			session: A.session,
			batch_id: `${$("a-device").value || "HH"}-${Date.now()}`,
			codes: [...A.reads],
			area: A.area,
			device: $("a-device").value || null,
		});
		A.reads.clear();
		$("a-pending").textContent = 0;
		snack("Uploaded");
		renderAudit(s);
	});
	$("a-compare").addEventListener("click", async () => renderAudit(await call("audit.compare", { session: A.session })));
	$("a-discard").addEventListener("click", async () => {
		if (!confirm("Discard this count? Scanned reads in it are thrown away.")) return;
		await call("audit.discard", { session: A.session });
		A.session = null;
		A.reads.clear();
		store.set(null);
		$("a-pending").textContent = 0;
		$("a-run").hidden = true;
		$("a-start").hidden = false;
		$("a-summary").innerHTML = '<div class="placeholder"><span class="ms">inventory</span><h3>No count running</h3><p>Start today\'s count on the left.</p></div>';
		snack("Count discarded");
	});
	$("a-submit").addEventListener("click", async () => {
		renderAudit(await call("audit.submit_for_review", { session: A.session }));
		snack("Sent to the manager for review");
	});

	show();
})();
