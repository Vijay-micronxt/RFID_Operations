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

	/** Material dialog with one text field. Resolves to the trimmed text, or null on cancel (or empty, unless optional). */
	function ask(title, placeholder, optional = false) {
		return new Promise((resolve) => {
			const back = document.createElement("div");
			back.className = "dialog-back";
			back.innerHTML = `<div class="dialog"><h3>${esc(title)}</h3>
				<input class="field-lg" placeholder="${esc(placeholder)}">
				<div class="dialog-actions"><button class="btn text" data-r="0">Cancel</button><button class="btn text" data-r="1">OK</button></div></div>`;
			const input = back.querySelector("input");
			const close = (ok) => {
				back.remove();
				const text = input.value.trim();
				resolve(ok && (text || optional) ? text : null);
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
		document.querySelectorAll("#m-pending [data-g]").forEach((b) => {
			const g = groups[b.dataset.g];
			const on = g && (g.kind === "unit"
				? g.serial_no === M.unit?.name
				: B.group && g.item_code === B.group.item_code && g.warehouse === B.group.warehouse);
			b.classList.toggle("selected", !!on);
		});
	}

	function renderUnit() {
		const u = M.unit;
		$("m-empty").hidden = !!u || !!B.group;
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
		closeBulk();
		M.unit = await call("mapping.unit_detail", { serial_no: code }, false);
		pickNext();
		renderUnit();
		// On phones the work panel sits below the list.
		if (window.matchMedia("(max-width: 860px)").matches) $("m-tagging").scrollIntoView({ behavior: "smooth" });
	}

	// ---- bulk: identical pieces of one item, all tags on the reader at once
	const B = { group: null, codes: [], verdicts: {} };
	let groups = [];

	function closeBulk() {
		B.group = null;
		B.codes = [];
		B.verdicts = {};
		$("m-bulk").hidden = true;
	}

	function openGroup(g) {
		M.unit = null;
		$("m-tagging").hidden = true;
		B.group = g;
		B.codes = [];
		B.verdicts = {};
		renderBulk();
		highlightPending();
		setTimeout(() => $("mb-read").focus(), 0);
		if (window.matchMedia("(max-width: 860px)").matches) $("m-bulk").scrollIntoView({ behavior: "smooth" });
	}

	function renderBulk() {
		const g = B.group;
		$("m-empty").hidden = !!g || !!M.unit;
		$("m-bulk").hidden = !g;
		if (!g) return;
		const qty = g.to_tag;
		const n = B.codes.length;
		const bad = B.codes.filter((c) => B.verdicts[c] && !B.verdicts[c].ok);
		const checking = B.codes.some((c) => !B.verdicts[c]);
		$("mb-name").textContent = g.item_name;
		$("mb-meta").textContent = `${g.item_code} · ${g.warehouse}`;
		$("mb-badge").textContent = g.kind === "serials" ? "With serial numbers" : "No serial numbers";
		$("mb-n").textContent = n;
		$("mb-qty").textContent = qty;
		document.querySelector(".bulk-count").className = `bulk-count${n > qty ? " over" : n === qty ? " full" : ""}`;
		$("mb-bar").style.width = `${Math.min(n / qty, 1) * 100}%`;
		const warn = bad.length
			? `${bad.length} tag(s) cannot be used (red below). Take them off the reader. Nothing is tagged yet.`
			: n > qty
				? `${n - qty} tag(s) too many: ${qty} piece(s) of this item wait for a tag. Take the extra tags off. Nothing is tagged yet.`
				: n && n < qty
					? `${n} of ${qty} pieces will be tagged; ${qty - n} stay untagged for later. Put more tags on the reader, or press Save.`
					: "";
		const ready = n > 0 && n <= qty && !bad.length && !checking;
		$("mb-warn").hidden = !warn;
		$("mb-warn").textContent = warn;
		$("mb-warn").className = n && n < qty && !bad.length ? "warnline soft" : "warnline";
		$("mb-ok").textContent = n ? `${n - bad.length} usable` : "";
		$("mb-save").disabled = !ready || B.saving;
		clearTimeout(B.timer);
		// All tags right: save by itself once the reader is quiet for a moment. Fewer tags wait for Save.
		if (ready && n === qty && !B.saving) B.timer = setTimeout(() => !$("mb-save").disabled && $("mb-save").click(), 1200);
		$("mb-save").innerHTML = `${icon("save")}Save ${n ? n : ""} tag${n === 1 ? "" : "s"}`;
		$("mb-list").innerHTML =
			B.codes
				.slice()
				.reverse()
				.map((c) => {
					const v = B.verdicts[c];
					const ok = !v || v.ok;
					return `<div class="row"><span class="lead ${v ? (ok ? "ok" : "bad") : ""}">${icon(v ? (ok ? "check" : "block") : "hourglass_empty", "small")}</span>
						<div class="grow"><b>${esc(c)}</b><small>${esc(v ? v.message : "Checking...")}</small></div>
						<button class="x" data-drop="${esc(c)}" title="Remove">${icon("close", "small")}</button></div>`;
				})
				.join("") || '<div class="empty-row">No tags read yet.</div>';
	}

	async function addReads(codes) {
		const fresh = [...new Set(codes.map((c) => c.toUpperCase()))].filter((c) => !B.codes.includes(c));
		if (!fresh.length) return;
		B.codes.push(...fresh);
		renderBulk();
		const out = await call("mapping.check_tags", { codes: fresh, item_code: B.group.item_code }, false);
		out.forEach((v) => (B.verdicts[v.code] = v));
		renderBulk();
	}

	onScan($("mb-read"), (codes) => addReads(codes));
	$("mb-list").addEventListener("click", (e) => {
		const b = e.target.closest("[data-drop]");
		if (!b) return;
		B.codes = B.codes.filter((c) => c !== b.dataset.drop);
		renderBulk();
	});
	$("mb-clear").addEventListener("click", () => {
		B.codes = [];
		B.verdicts = {};
		renderBulk();
		$("mb-read").focus();
	});
	$("mb-close").addEventListener("click", () => {
		closeBulk();
		renderUnit();
		$("m-code").focus();
	});
	$("mb-save").addEventListener("click", async () => {
		const g = B.group;
		if (B.saving) return;
		B.saving = true;
		renderBulk();
		let r;
		try {
			r = await call("mapping.bulk_assign", { item_code: g.item_code, warehouse: g.warehouse, codes: B.codes });
		} finally {
			B.saving = false;
			renderBulk();
		}
		snack(`${r.assigned} tag${r.assigned === 1 ? "" : "s"} saved on ${g.item_name}`);
		await loadPending();
		const next = groups.find((x) => x.kind !== "unit" && x.item_code === g.item_code && x.warehouse === g.warehouse);
		if (next) openGroup(next);
		else {
			closeBulk();
			renderUnit();
			$("m-code").focus();
		}
	});

	async function loadPending() {
		groups = await call("mapping.pending_groups", {}, false).catch(() => []);
		$("m-count").textContent = groups.reduce((n, g) => n + g.to_tag, 0);
		$("m-pending").innerHTML =
			groups
				.map((g, i) => {
					const chip = g.kind === "unit"
						? g.returned
							? '<span class="chip-s ret">Returned</span>'
							: g.retag
								? '<span class="chip-s ret">Re-tag</span>'
								: '<span class="chip-s set">Set</span>'
						: `<span class="chip-s">${g.to_tag} to tag</span>`;
					const sub = g.kind === "unit" ? g.serial_no : `${g.item_code} · ${g.warehouse}`;
					return `<button class="item" data-g="${i}">
						<span class="thumb">${icon(g.is_set ? "checkroom" : g.kind === "unit" ? "apparel" : "stacks")}</span>
						<span class="grow"><b>${esc(g.item_name)}</b><small>${esc(sub)}</small></span>
						${chip}${icon("chevron_right", "muted")}</button>`;
				})
				.join("") || `<div class="empty-row">Nothing waiting. New stock appears here after a purchase.</div>`;
		highlightPending();
	}
	onOpen.mapping = () => {
		loadPending();
		if (!B.group) renderUnit();
	};

	onScan($("m-code"), async (codes) => {
		const r = await call("mapping.open_code", { code: codes[0] }, false);
		if (r.kind === "unit") {
			closeBulk();
			M.unit = r.unit;
			pickNext();
			renderUnit();
		} else if (r.kind === "group") {
			if (r.groups.length > 1) snack(`${r.item_code || r.groups[0].item_name} waits in ${r.groups.length} warehouses - showing ${r.groups[0].warehouse}`);
			openGroup(r.groups[0]);
		} else snack(`Every piece of ${r.item_name} already has a tag`);
	});
	onScan($("m-tag"), async (codes) => {
		if (!M.unit || !M.current) return;
		if (codes.length > 1) {
			snack(`${codes.length} tags read at once. Hold only this piece's tag near the reader.`, true);
			return;
		}
		const piece = M.current === "__single__" ? null : M.current;
		M.unit = await call("mapping.map_tag", { epc: codes[0], serial_no: M.unit.name, component: piece, device: null });
		snack("Tag saved");
		pickNext();
		renderUnit();
		if (!M.current) loadPending();
	});

	$("m-pending").addEventListener("click", (e) => {
		const b = e.target.closest("[data-g]");
		if (!b) return;
		const g = groups[b.dataset.g];
		if (g.kind === "unit") openUnit(g.serial_no);
		else openGroup(g);
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
		closeBulk();
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

	// ------------------------------------------------------------ tag history
	const EV = {
		Assigned: ["good", "sell", "Tagged"],
		Restored: ["good", "undo", "Put back"],
		Found: ["good", "search", "Found"],
		Returned: ["good", "assignment_return", "Came back (return)"],
		Sold: ["sale", "payments", "Sold"],
		Freed: ["", "lock_open", "Freed"],
		Detached: ["", "content_cut", "Removed after sale"],
		Removed: ["warn", "label_off", "Removed (not sold)"],
		Transferred: ["", "local_shipping", "Moved"],
		Reserved: ["warn", "pending", "Read on draft"],
		"Reservation Released": ["", "remove_done", "Taken off draft"],
		Replaced: ["warn", "sync", "Replaced"],
		Damaged: ["bad", "broken_image", "Damaged"],
		Lost: ["bad", "help", "Lost"],
		Retired: ["bad", "block", "Retired"],
		"Issued Out": ["", "logout", "Left stock"],
		Rejected: ["bad", "error", "Refused"],
		"Exit Alert": ["bad", "warning", "Exit alarm"],
		Registered: ["", "add", "First seen"],
		Correction: ["warn", "edit", "Correction"],
	};
	const money = (v) => (v ? `₹${Number(v).toLocaleString("en-IN", { maximumFractionDigits: 2 })}` : "");
	const when = (v) => (v ? String(v).slice(0, 16).replace("T", " ") : "");
	const docLink = (dt, dn) =>
		dt && dn ? `<a href="/app/${dt.toLowerCase().replace(/ /g, "-")}/${encodeURIComponent(dn)}" target="_blank">${esc(dn)}</a>` : "";
	const H = { story: null };
	const canRemoveUnsold = !!RFID.canRemoveUnsold;

	function renderStory(st) {
		H.story = st;
		const t = st.tag;
		const actions = [];
		if (t && t.status === "Assigned" && canRemoveUnsold) actions.push(["Removed", "label_off", "Remove - use on another item"]);
		if (t && t.current_assignment) actions.push(["Damaged", "broken_image", "Damaged - put a new tag"], ["Lost", "help", "Mark lost"]);
		if (t && t.status === "Lost") actions.push(["Found", "search", "Found again"]);
		if (t && ["Available"].includes(t.status)) actions.push(["Damaged", "broken_image", "Damaged"], ["Retired", "block", "Retire"]);
		$("h-card").innerHTML = t
			? `<div class="tagcard"><h3>${esc(t.name)}</h3><span class="badge${t.current_assignment ? " ok" : ""}">${esc(t.status)}</span>
				<div class="kv">
					<span>On</span><span>${t.item_code ? `${esc(t.item_name || t.item_code)}${t.serial_no ? ` · ${esc(t.serial_no)}` : ""}${t.component ? ` · ${esc(t.component)}` : ""}` : "nothing"}</span>
					<span>Where</span><span>${esc(t.warehouse || "-")}</span>
					<span>Times used</span><span>${t.cycle || 0}</span>
					${t.reserved_for ? `<span>On draft</span><span>${docLink(t.reserved_doctype, t.reserved_for)}</span>` : ""}
					${t.last_read_on ? `<span>Last read</span><span>${esc(when(t.last_read_on))}</span>` : ""}
				</div>
				<div class="buttons">${actions
					.map(([st, ic, label]) => `<button class="btn ghost" data-cond="${st}">${icon(ic, "small")}${label}</button>`)
					.join("")}</div></div>`
			: `<div class="tagcard"><h3>${esc(st.code)}</h3><p class="muted small">Garment label · history of its tags</p></div>`;

		const uses = st.uses
			.map(
				(u) => `<tr><td>${u.cycle || ""}</td><td>${esc(u.item_name || u.item_code || "")}${u.serial_no ? `<br><small class="muted">${esc(u.serial_no)}</small>` : ""}</td>
				<td>${esc(when(u.assigned_on))}<br><small class="muted">${docLink(u.source_doctype, u.source_name)}</small></td>
				<td>${u.sales_invoice ? `${docLink("Sales Invoice", u.sales_invoice)}<br><small class="muted">${money(u.sold_rate)} · ${esc(u.sold_by_name || "")}</small>` : "-"}</td>
				<td>${esc(u.close_reason || (u.status === "Active" ? "On garment" : u.status))}<br><small class="muted">${esc(when(u.closed_on))}</small></td></tr>`
			)
			.join("");
		const events = st.events
			.map((e) => {
				const [cls, ic, label] = EV[e.action] || ["", "circle", e.action];
				const garment = [e.item_name || e.item_code, e.serial_no, e.component].filter(Boolean).map(esc).join(" · ");
				const place = e.to_warehouse ? `${esc(e.warehouse)} → ${esc(e.to_warehouse)}` : esc(e.warehouse || "");
				return `<div class="ev ${cls}"><b>${icon(ic, "small")} ${esc(label)}</b>${t ? "" : ` <span class="muted">${esc(e.tag)}</span>`}
					${garment ? ` · ${garment}` : ""}${place ? ` <span class="muted">· ${place}</span>` : ""}
					${e.amount ? ` · <b>${money(e.amount)}</b>` : ""}
					<div class="when">${esc(when(e.event_time))} · ${esc(e.by || "")}${e.reference_name ? ` · ${docLink(e.reference_doctype, e.reference_name)}` : ""}</div>
					${e.reason ? `<div class="note">${esc(e.reason)}</div>` : ""}</div>`;
			})
			.join("");
		$("h-out").innerHTML = `
			<div class="work-head"><div><h2>${esc(st.code)}</h2><p class="muted">${st.events.length} events · ${st.uses.length} garment${st.uses.length === 1 ? "" : "s"}</p></div></div>
			${uses ? `<h3 class="label">Garments this tag was on</h3><table class="uses"><thead><tr><th>#</th><th>Garment</th><th>Tagged</th><th>Sold</th><th>Ended</th></tr></thead><tbody>${uses}</tbody></table>` : ""}
			<h3 class="label">Everything that happened</h3>
			<div class="timeline">${events || '<div class="empty-row">No history yet.</div>'}</div>`;
	}

	async function openStory(code) {
		renderStory(await call("mapping.tag_story", { code }, false));
	}
	onScan($("h-code"), (codes) => openStory(codes[0]));
	$("h-card").addEventListener("click", async (e) => {
		const b = e.target.closest("[data-cond]");
		if (!b || !H.story?.tag) return;
		const tag = H.story.tag;
		const status = b.dataset.cond;
		if (status === "Removed") {
			const reason = await ask(`Remove tag from ${tag.item_name || tag.item_code}?`, "Reason (optional)", true);
			if (reason === null) return;
			const [r] = await call("mapping.remove_tags", { codes: [tag.name], action: "Remove", reason });
			snack(r.message, !r.ok);
			return openStory(tag.name);
		}
		if (status === "Damaged" && tag.current_assignment) {
			const fresh = await ask(`New tag for ${tag.item_name || tag.item_code}`, "Read the new tag (leave empty to just retire)");
			if (fresh) {
				await call("mapping.replace_tag", { old_epc: tag.name, new_epc: fresh, reason: "Tag damaged" });
				snack("Old tag marked damaged, new tag saved");
				return openStory(tag.name);
			}
		}
		const reason = await ask(`${status}: why?`, "e.g. torn, fell off, not found in count");
		if (!reason) return;
		renderStory(await call("mapping.set_condition", { epc: tag.name, status, reason }));
		snack(`Tag marked ${status.toLowerCase()}`);
	});

	// ------------------------------------------------------------ remove tags
	// Sold tags are freed at once. Tags on unsold stock (only when RFID Settings allows it) wait in the list,
	// ticked, so many can be removed in one go with one optional reason.
	let released = 0;
	const doneRow = (r) => `<div class="row"><span class="lead ${r.ok ? "ok" : "bad"}">${icon(r.ok ? "check" : "block", "small")}</span>
		<div class="grow"><b>${esc(r.message)}</b><small>${esc(r.epc)}</small></div></div>`;
	const unsoldRow = (r) => {
		const on = [r.item_name || r.item_code, r.serial_no, r.component].filter(Boolean).map(esc).join(" · ");
		return `<label class="row unsold" data-epc="${esc(r.epc)}"><input type="checkbox" class="pick" checked>
		<div class="grow"><b>Not sold: ${on || "unknown item"}</b><small>${esc(r.epc)}</small></div></label>`;
	};
	const picks = () => [...document.querySelectorAll("#d-out .unsold .pick")];
	function syncBulk() {
		const all = picks();
		const on = all.filter((c) => c.checked).length;
		$("d-bulk").hidden = !all.length;
		$("d-sel").textContent = `${on} of ${all.length} selected`;
		$("d-all").checked = all.length > 0 && on === all.length;
		$("d-all").indeterminate = on > 0 && on < all.length;
		document.querySelectorAll("[data-bulk]").forEach((b) => (b.disabled = !on));
	}
	const countReleased = (n) => {
		released += n;
		$("d-count").textContent = released;
	};
	onScan($("d-scan"), async (codes) => {
		const fresh = codes.filter((c) => !document.querySelector(`#d-out .unsold[data-epc="${CSS.escape(c.toUpperCase())}"]`));
		if (!fresh.length) return snack("Already in the list");
		const out = await call("mapping.detach", { codes: fresh });
		const ok = out.filter((r) => r.ok).length;
		const unsold = out.filter((r) => r.unsold).length;
		countReleased(ok);
		$("d-empty").hidden = true;
		snack(
			ok ? `${ok} tag${ok > 1 ? "s" : ""} released` : unsold ? `${unsold} not sold - tick and choose below` : "Nothing released",
			!ok && !unsold
		);
		$("d-out").insertAdjacentHTML("afterbegin", out.map((r) => (r.unsold ? unsoldRow(r) : doneRow(r))).join(""));
		syncBulk();
	});
	$("d-out").addEventListener("change", (e) => e.target.classList.contains("pick") && syncBulk());
	$("d-all").addEventListener("change", (e) => {
		picks().forEach((c) => (c.checked = e.target.checked));
		syncBulk();
	});
	$("d-bulk").addEventListener("click", async (e) => {
		const b = e.target.closest("[data-bulk]");
		if (!b) return;
		const rows = picks().filter((c) => c.checked).map((c) => c.closest("[data-epc]"));
		if (!rows.length) return;
		const action = b.dataset.bulk;
		const buttons = document.querySelectorAll("[data-bulk]");
		buttons.forEach((x) => (x.disabled = true));
		try {
			const out = await call("mapping.remove_tags", {
				codes: rows.map((r) => r.dataset.epc),
				action,
				reason: $("d-reason").value.trim(),
			});
			const byEpc = Object.fromEntries(out.map((r) => [r.epc, r]));
			let done = 0;
			rows.forEach((row) => {
				const r = byEpc[row.dataset.epc];
				if (!r) return;
				if (r.ok) done++;
				row.outerHTML = doneRow(r);
			});
			if (action === "Remove") countReleased(done);
			$("d-reason").value = "";
			const failed = out.length - done;
			snack(
				`${done} tag${done === 1 ? "" : "s"} ${action === "Remove" ? "removed" : `marked ${action.toLowerCase()}`}${failed ? ` · ${failed} failed` : ""}`,
				!done
			);
		} finally {
			syncBulk();
			$("d-scan").focus();
		}
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
	// Every scan is uploaded at once; the right panel is a live checklist of what should be in the store.
	const A = { session: null, area: "Sales Floor" };
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
		$("a-submit").disabled = !["Comparison Ready", "Investigation Pending"].includes(s.status);
		$("a-discard").hidden = ["Submitted for Review", "Reviewed"].includes(s.status);

		const seen = new Set(s.observed || []);
		const expectedTags = new Set();
		let complete = 0;
		const rows = (s.checklist || [])
			.map((g) => {
				g.pieces.forEach((p) => p.tag && expectedTags.add(p.tag));
				const found = g.pieces.filter((p) => seen.has(p.tag)).length;
				if (found === g.pieces.length) complete++;
				const state =
					found === g.pieces.length
						? '<span class="gstate ok">All found</span>'
						: found
							? `<span class="gstate part">${found} of ${g.pieces.length} found</span>`
							: '<span class="gstate no">Not scanned yet</span>';
				const chips = g.pieces
					.map((p) => `<span class="pc ${seen.has(p.tag) ? "found" : ""}">${seen.has(p.tag) ? "✓ " : ""}${esc(p.role)}</span>`)
					.join("");
				return `<div class="row"><span class="thumb">${icon(g.pieces.length > 1 ? "checkroom" : "apparel")}</span>
					<div class="grow"><b>${esc(g.item_name)}</b><small>${esc(g.serial_no)}</small>
					${g.pieces.length > 1 ? `<div class="pieces">${chips}</div>` : ""}</div>${state}</div>`;
			})
			.join("");
		const unknown = s.unknown || [];
		const r = s.results || {};
		const stat = (label, n, cls) => `<div class="stat ${n ? cls : ""}"><b>${n || 0}</b><small>${label}</small></div>`;
		const compared = Object.keys(r).length > 0;

		$("a-summary").innerHTML = `
			<div class="work-head"><div><h2>Stock check · ${esc(s.warehouse)}</h2>
				<p class="muted">${esc(s.name)} · started ${esc(String(s.cutoff || "").slice(11, 16))}</p></div>
				<span class="badge">${esc(s.status)}</span></div>
			${
				compared
					? `<h3 class="label">Result</h3><div class="stats">${stat("Garments found complete", r.Matched, "ok")}${stat(
							"Not found",
							r.Unverified,
							"bad"
						)}${stat("Sets missing a piece", r["Incomplete Set"], "warn")}${stat(
							"Unknown or extra tags",
							(r.Unexpected || 0) + (r.Unmapped || 0),
							"warn"
						)}</div>
						<a class="btn ghost" href="/app/rfid-audit-session/${encodeURIComponent(s.name)}">${icon("open_in_new", "small")}Open full result to note what happened</a>`
					: `<div class="explain">${icon("info")}<span>Scan every tag in the store. Each garment below turns green when all its pieces are scanned. When you are done, press <b>Finish count</b>.</span></div>`
			}
			${
				compared
					? ""
					: `<div class="tally">
				<div><b>${(s.checklist || []).length}</b><small>Garments expected here</small></div>
				<div><b>${complete}</b><small>Found complete</small></div>
				<div><b>${seen.size}</b><small>Codes scanned</small></div>
			</div>`
			}
			<h3 class="label">Garments that should be here</h3>
			<div class="list">${rows || '<div class="empty-row">No RFID garments in this warehouse.</div>'}</div>
			${
				unknown.length
					? `<h3 class="label">Tags that do not belong to any garment here</h3>
						<div class="list">${unknown.map((u) => `<div class="row"><span class="lead bad">${icon("help", "small")}</span><div class="grow"><b>${esc(u.code)}</b><small>${esc(u.reason)}</small></div></div>`).join("")}</div>`
					: ""
			}`;
	}

	function resetAudit() {
		A.session = null;
		store.set(null);
		$("a-run").hidden = true;
		$("a-start").hidden = false;
		show();
	}

	onOpen.audit = async () => {
		const saved = store.get();
		if (!A.session && saved && saved.day === new Date().toDateString()) {
			const s = await call("audit.summary", { session: saved.session }, false).catch(() => null);
			if (s && s.status !== "Reviewed") renderAudit(s);
		}
	};

	$("a-begin").addEventListener("click", async () => {
		renderAudit(await call("audit.start", { warehouse: $("a-wh").value, device: $("a-device").value || null }));
		$("a-scan").focus();
	});
	$("a-areas").addEventListener("click", (e) => {
		const chip = e.target.closest("[data-area]");
		if (!chip) return;
		document.querySelectorAll("#a-areas .chip").forEach((c) => c.classList.toggle("on", c === chip));
		A.area = chip.dataset.area;
		$("a-scan").focus();
	});
	onScan($("a-scan"), async (codes) => {
		renderAudit(
			await call("audit.upload", {
				session: A.session,
				batch_id: `${$("a-device").value || "HH"}-${Date.now()}`,
				codes,
				area: A.area,
				device: $("a-device").value || null,
			})
		);
	});
	$("a-compare").addEventListener("click", async () => {
		renderAudit(await call("audit.compare", { session: A.session }));
		snack("Count finished. Check the result on the right.");
	});
	$("a-discard").addEventListener("click", async () => {
		if (!confirm("Discard this count? Its scans are thrown away.")) return;
		await call("audit.discard", { session: A.session });
		snack("Count discarded");
		resetAudit();
		$("a-summary").innerHTML = '<div class="placeholder"><span class="ms">inventory</span><h3>Daily stock check</h3><p>Pick the warehouse on the left and press Start.</p></div>';
	});
	$("a-submit").addEventListener("click", async () => {
		renderAudit(await call("audit.submit_for_review", { session: A.session }));
		snack("Sent to the manager for review");
	});

	show();
})();
