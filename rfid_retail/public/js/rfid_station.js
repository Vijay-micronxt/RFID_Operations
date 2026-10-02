/* RFID stations: a thin, mobile-first client over rfid_retail.api.*. Every rule lives on the server. */
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
			.join("")}</div>`;
		back.addEventListener("click", (e) => {
			const btn = e.target.closest("[data-i]");
			back.remove();
			if (btn) items[btn.dataset.i].run();
		});
		document.body.appendChild(back);
	}

	// ------------------------------------------------------------ navigation (hash, so Android back works)
	const screens = [...document.querySelectorAll(".screen")];
	if (!screens.length) return; // RFID switched off: the page only shows a notice
	const onOpen = {};
	function show() {
		const name = location.hash.slice(1) || "home";
		const target = screens.find((s) => s.dataset.screen === name) || screens[0];
		if (!target) return;
		screens.forEach((s) => s.classList.toggle("active", s === target));
		const home = target.dataset.screen === "home";
		$("back").hidden = home;
		$("title").textContent = home ? "RFID Stations" : target.dataset.title;
		onOpen[target.dataset.screen]?.();
		target.querySelector(".scanbar input")?.focus();
	}
	window.addEventListener("hashchange", show);
	$("back").addEventListener("click", () => history.back());
	document.querySelectorAll("[data-go]").forEach((t) => t.addEventListener("click", () => (location.hash = t.dataset.go)));

	document.querySelectorAll(".device-select").forEach((sel) => {
		const opts = RFID.devices.filter((d) => d.device_type === sel.dataset.type);
		sel.innerHTML = '<option value="">None</option>' + opts.map((d) => `<option>${esc(d.name)}</option>`).join("");
		if (opts.length) sel.value = opts[0].name;
	});

	// ------------------------------------------------------------ tag garments
	const M = { unit: null, armed: null };

	function pieces(u) {
		if (u.components) return u.components;
		return [{ name: null, role: u.item_name, status: u.tag ? "Tagged" : "Pending Tag", rfid_tag: u.tag }];
	}

	function armNext() {
		const next = pieces(M.unit).find((p) => !p.rfid_tag && p.status !== "Out for Alteration");
		M.armed = next ? next.name || "__single__" : null;
	}

	function renderUnit() {
		const u = M.unit;
		const box = $("m-unit");
		if (!u) {
			box.innerHTML = "";
			return;
		}
		const ready = u.custom_rfid_status === "Ready";
		const statusPill = ready ? "ok" : u.custom_rfid_status === "Sold" ? "" : "warn";
		const rows = pieces(u)
			.map((p) => {
				const key = p.name || "__single__";
				const armed = M.armed === key;
				const away = p.status === "Out for Alteration";
				const lead = p.rfid_tag ? `<span class="lead ok">${icon("check")}</span>` : away ? `<span class="lead warn">${icon("content_cut")}</span>` : `<span class="lead">${icon("sell")}</span>`;
				const sub = p.rfid_tag ? esc(p.rfid_tag) : away ? "At alteration" : armed ? "Scan the tag now" : "Needs a tag";
				return `<div class="row ${armed ? "active" : ""} ${p.rfid_tag ? "done" : ""}" data-piece="${esc(key)}">
					${lead}<div class="grow"><b>${esc(p.role)}</b><small>${sub}</small></div>
					<button class="icon-btn" data-more="${esc(key)}" aria-label="More">${icon("more_vert")}</button></div>`;
			})
			.join("");
		box.innerHTML = `<div class="card">
			<div class="unit-head"><span class="lead ${ready ? "ok" : ""}">${icon(u.components ? "checkroom" : "apparel")}</span>
				<div class="grow"><b>${esc(u.item_name)}</b><small>${esc(u.name)} · ${esc(u.warehouse || "")}</small></div>
				<span class="pill ${statusPill}">${esc(u.custom_rfid_status || "")}</span></div>
			<div class="list">${rows}</div>
			${u.custom_rfid_status === "Returned - Hold" ? `<button class="btn tonal wide" id="m-release" style="margin-top:12px">${icon("assignment_turned_in")}Return checked - tag again</button>` : ""}
		</div>`;
		const bar = $("m-scan").parentElement;
		const armedPiece = pieces(u).find((p) => (p.name || "__single__") === M.armed);
		bar.classList.toggle("armed", !!armedPiece);
		$("m-scan").placeholder = armedPiece ? `Scan the tag for ${armedPiece.role}` : "Scan the next garment label";
		$("m-hint").textContent = armedPiece
			? "Hold the tag on the reader. The next piece is picked automatically."
			: ready
				? "All pieces tagged. This garment is ready to sell."
				: "Tap a piece to choose it, or scan another garment.";
	}

	async function openUnit(code) {
		M.unit = await call("mapping.unit_detail", { serial_no: code }, false);
		armNext();
		renderUnit();
	}

	async function loadPending() {
		const rows = await call("mapping.pending_units", {}, false).catch(() => []);
		$("m-count").textContent = rows.length;
		$("m-pending").innerHTML =
			rows
				.map(
					(r) => `<button class="row" data-open="${esc(r.name)}">
						<span class="lead">${icon(r.custom_is_garment_set ? "checkroom" : "apparel")}</span>
						<div class="grow"><b>${esc(r.item_name)}</b><small>${esc(r.name)}</small></div>
						<span class="pill ${r.custom_rfid_status === "Returned - Hold" ? "bad" : "warn"}">${esc(r.custom_rfid_status)}</span></button>`
				)
				.join("") || `<div class="hint">Nothing waiting. New stock appears here after a purchase receipt.</div>`;
	}
	onOpen.mapping = loadPending;

	onScan($("m-scan"), async (codes) => {
		const code = codes[0];
		if (M.unit && M.armed) {
			const piece = M.armed === "__single__" ? null : M.armed;
			M.unit = await call("mapping.map_tag", { epc: code, serial_no: M.unit.name, component: piece, device: null });
			snack("Tag saved");
			armNext();
			renderUnit();
			if (!M.armed) loadPending();
			return;
		}
		await openUnit(code);
	});

	$("m-pending").addEventListener("click", (e) => {
		const b = e.target.closest("[data-open]");
		if (b) openUnit(b.dataset.open).then(() => window.scrollTo({ top: 0, behavior: "smooth" }));
	});

	$("m-unit").addEventListener("click", async (e) => {
		if (e.target.closest("#m-release")) {
			M.unit = await call("mapping.release_return", { serial_no: M.unit.name });
			armNext();
			renderUnit();
			loadPending();
			return;
		}
		const more = e.target.closest("[data-more]");
		if (more) return pieceMenu(more.dataset.more);
		const row = e.target.closest("[data-piece]");
		if (row) {
			const p = pieces(M.unit).find((x) => (x.name || "__single__") === row.dataset.piece);
			if (p && !p.rfid_tag) {
				M.armed = row.dataset.piece;
				renderUnit();
				$("m-scan").focus();
			}
		}
	});

	function pieceMenu(key) {
		const p = pieces(M.unit).find((x) => (x.name || "__single__") === key);
		const items = [];
		if (p.rfid_tag)
			items.push({
				icon: "sync",
				label: "Replace damaged tag",
				run: async () => {
					const fresh = prompt(`Scan the NEW tag for ${p.role}`);
					if (!fresh) return;
					M.unit = await call("mapping.replace_tag", { old_epc: p.rfid_tag, new_epc: fresh.trim(), reason: "Tag damaged" });
					snack("Old tag retired, new tag saved");
					renderUnit();
				},
			});
		if (p.name && p.status === "Tagged")
			items.push({
				icon: "content_cut",
				label: "Send to alteration",
				run: async () => {
					const who = prompt("Who is altering it?");
					if (!who) return;
					M.unit = await call("mapping.set_alteration", { component: p.name, out: 1, custodian: who });
					renderUnit();
				},
			});
		if (p.name && p.status === "Out for Alteration")
			items.push({
				icon: "undo",
				label: "Back from alteration",
				run: async () => {
					M.unit = await call("mapping.set_alteration", { component: p.name, out: 0 });
					armNext();
					renderUnit();
				},
			});
		if (!items.length) return snack("No actions for this piece");
		sheet(p.role, items);
	}

	// ------------------------------------------------------------ remove tags
	onScan($("d-scan"), async (codes) => {
		const out = await call("mapping.detach", { codes });
		const ok = out.filter((r) => r.ok).length;
		snack(ok ? `${ok} tag${ok > 1 ? "s" : ""} released` : "Nothing released", !ok);
		$("d-out").insertAdjacentHTML(
			"afterbegin",
			out
				.map(
					(r) => `<div class="row"><span class="lead ${r.ok ? "ok" : "bad"}">${icon(r.ok ? "check" : "block")}</span>
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
				(e) => `<div class="row"><span class="lead ${e.alarm ? "bad" : "ok"}">${icon(e.alarm ? "priority_high" : "check")}</span>
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
		$("a-summary").innerHTML = `<div class="unit-head"><span class="lead">${icon("inventory")}</span>
			<div class="grow"><b>${esc(s.name)}</b><small>${esc(s.warehouse)} · ${s.expected_units} units expected · ${s.observations} tags received</small></div>
			<span class="pill">${esc(s.status)}</span></div>
			${s.note ? `<p class="hint">${esc(s.note)}</p>` : ""}
			${
				Object.keys(r).length
					? `<div class="stats">${stat("Found", r.Matched, "ok")}${stat("Not found yet", r.Unverified, "bad")}${stat(
							"Sets missing a piece",
							r["Incomplete Set"],
							"warn"
						)}${stat("Unknown / extra tags", (r.Unexpected || 0) + (r.Unmapped || 0), "warn")}</div>
						<a class="btn text" href="/app/rfid-audit-session/${encodeURIComponent(s.name)}">Open full result</a>`
					: `<p class="hint">Scan each area, upload, then press Compare.</p>`
			}`;
		$("a-submit").disabled = !["Comparison Ready", "Investigation Pending"].includes(s.status);
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
	$("a-submit").addEventListener("click", async () => {
		renderAudit(await call("audit.submit_for_review", { session: A.session }));
		snack("Sent to the manager for review");
	});

	show();
})();
