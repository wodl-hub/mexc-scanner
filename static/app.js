const $ = (id) => document.getElementById(id);
const seen = new Set();
const state = { rows: [], selected: null, pxMode: "cent", timer: null };

function fmtPx(p) {
  const n = Number(p);
  if (!Number.isFinite(n) || n <= 0) return "—";
  if (state.pxMode === "dec") return (1 / n).toFixed(3);
  return `${(n * 100).toFixed(1)}¢`;
}
function fmtUsd(n) {
  const x = Number(n);
  if (!Number.isFinite(x)) return "—";
  return x.toLocaleString("en-US", { maximumFractionDigits: 2 });
}
function roiClass(v) {
  if (v == null) return "roi";
  return v > 0 ? "roi pos" : v < 0 ? "roi neg" : "roi";
}
function beep() {
  if (!$("sound").checked) return;
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const o = ctx.createOscillator();
    const g = ctx.createGain();
    o.connect(g); g.connect(ctx.destination);
    o.frequency.value = 880; g.gain.value = 0.04;
    o.start(); o.stop(ctx.currentTime + 0.12);
  } catch (_) { /* ignore */ }
}
async function getJson(url, opts) {
  const r = await fetch(url, opts);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) {
    const d = data.detail;
    throw new Error(typeof d === "string" ? d : r.statusText);
  }
  return data;
}

function currentRow() {
  return state.selected == null ? null : state.rows[state.selected];
}

function sortedRows(rows) {
  const copy = [...rows];
  const mode = $("sortBy").value;
  copy.sort((a, b) => {
    if (mode === "volume") return (b.volume24hr || 0) - (a.volume24hr || 0);
    if (mode === "time") return String(a.start || "").localeCompare(String(b.start || ""));
    const ar = a.best_roi, br = b.best_roi;
    if (ar == null && br == null) return (b.volume24hr || 0) - (a.volume24hr || 0);
    if (ar == null) return 1;
    if (br == null) return -1;
    return br - ar;
  });
  return copy;
}

function renderRows() {
  const box = $("rows");
  const rows = sortedRows(state.rows);
  $("rowCount").textContent = `${rows.length}`;
  if (!rows.length) {
    box.innerHTML = `<div class="muted">Нет рынков. Включите SX / Smarkets / Kalshi / Odds API или впишите кэф вручную.</div>`;
    return;
  }
  box.innerHTML = rows.map((row) => {
    const i = state.rows.indexOf(row);
    const q0 = row.quotes[0];
    const roi = row.best_roi;
    const roiTxt = roi == null ? `BE ${q0 ? q0.break_even_maker.toFixed(2) : "—"}` : `${roi.toFixed(2)}%`;
      const book = (q0 && q0.book && q0.book.odds)
        ? `${q0.book.book} ${q0.book.odds}`
        : ((q0 && q0.books && q0.books.length) ? q0.books.map((b) => b.book).filter(Boolean).slice(0, 3).join(", ") : "");
    return `<article class="row ${state.selected === i ? "on" : ""}" data-i="${i}">
      <div class="t">${row.title}</div>
      <div class="s">
        <span>${row.sports_type} · ${row.phase}</span>
        <span>vol ${fmtUsd(row.volume24hr)}</span>
        <span class="${roiClass(roi)}">${roiTxt}</span>
        <span>${q0 ? q0.poly_team : ""} ${q0 ? fmtPx(q0.poly_price) : ""}</span>
        <span>${book}</span>
      </div>
    </article>`;
  }).join("");
  box.querySelectorAll(".row").forEach((el) => el.addEventListener("click", () => selectRow(Number(el.dataset.i))));
}

function renderVenueBooks(row) {
  const box = $("venueBooks");
  const q0 = row.quotes[0];
  const books = (q0 && q0.books) || [];
  if (!books.length) {
    box.textContent = "нет авто-матча — впишите кэф БК ниже";
    return;
  }
  box.innerHTML = books.map((b) => {
    const roi = b.fork ? `${b.fork.roi_pct.toFixed(2)}%` : "нет цены";
    const odds = b.odds ? b.odds : "—";
    const href = b.url ? `<a href="${b.url}" target="_blank">${b.book}</a>` : b.book;
    return `<div class="mini-item"><span>${href} · ${b.team || ""} @ ${odds}</span><span class="${roiClass(b.fork ? b.fork.roi_pct : null)}">${roi}</span></div>`;
  }).join("");
}

async function selectRow(i) {
  state.selected = i;
  renderRows();
  const row = state.rows[i];
  if (!row) return;
  $("selTitle").textContent = row.group || row.sports_type;
  $("selMeta").textContent = `${row.question} · ${row.start || "?"} · fee ${row.fee_rate} · rebate ${row.rebate_rate}`;
  $("polyLink").href = row.url;
  const form = $("calcForm");
  const q0 = row.quotes[0];
  if (q0) {
    form.poly_price.value = q0.poly_price;
    form.fee_rate.value = row.fee_rate;
    $("limitPx").value = q0.poly_price;
    if (q0.book && q0.book.odds) {
      form.book_odds.value = q0.book.odds;
      $("limitOdds").value = q0.book.odds;
    }
  }
  renderVenueBooks(row);
  $("manualForm").team.value = q0 ? q0.hedge_team : "";
  const books = $("books");
  books.innerHTML = `<div class="muted">стакан…</div>`;
  const panes = await Promise.all(row.quotes.map(async (q) => {
    if (!q.token_id) return "";
    try {
      const book = await getJson(`/api/book?token_id=${encodeURIComponent(q.token_id)}`);
      const asks = (book.asks || []).slice(0, 10);
      const bids = (book.bids || []).slice(0, 10);
      const askHtml = asks.map((l) => `<div class="lvl ask" data-px="${l.price}">${fmtPx(l.price)} <span>${fmtUsd(l.size)}</span></div>`).join("");
      const bidHtml = bids.map((l) => `<div class="lvl bid" data-px="${l.price}">${fmtPx(l.price)} <span>${fmtUsd(l.size)}</span></div>`).join("");
      return `<div class="book" data-token="${q.token_id}">
        <h3>${q.poly_team} · ask ${fmtPx(book.best_ask)} · bid ${fmtPx(book.best_bid)} · BE ${q.break_even_maker}</h3>
        ${askHtml}<div class="lvl muted">——</div>${bidHtml}
      </div>`;
    } catch (err) {
      return `<div class="book"><h3>${q.poly_team}</h3><p class="muted">${err.message}</p></div>`;
    }
  }));
  books.innerHTML = panes.join("") || `<p class="muted">нет стакана</p>`;
  books.querySelectorAll(".lvl[data-px]").forEach((el) => {
    el.addEventListener("click", () => {
      form.poly_price.value = el.dataset.px;
      $("limitPx").value = el.dataset.px;
      form.requestSubmit();
    });
  });
  form.requestSubmit();
}

async function scan(opts = {}) {
  $("status").textContent = "сканирую…";
  const params = new URLSearchParams({
    tag: $("tag").value,
    types: $("types").value,
    phase: $("phase").value,
    hours: $("hours").value || "72",
    min_volume: $("minVolume").value || "0",
    min_roi: $("minRoi").value || "0",
    venues: [
      $("vSx").checked ? "sx" : "",
      $("vSmarkets").checked ? "smarkets" : "",
      $("vKalshi").checked ? "kalshi" : "",
      $("vOdds").checked ? "odds" : "",
      $("vManual").checked ? "manual" : "",
    ].filter(Boolean).join(",") || "manual",
  });
  try {
    const data = await getJson(`/api/scan?${params}`);
    const prev = new Set(state.rows.map((r) => r.market_id));
    state.rows = data.rows || [];
    let fresh = 0;
    for (const row of state.rows) {
      if (row.best_roi > 0 && !seen.has(row.market_id)) {
        seen.add(row.market_id);
        if (prev.size) fresh += 1;
      }
    }
    if (fresh) beep();
    const v = data.venues || {};
    const bits = [
      `SX ${v.sx_games ?? 0}`,
      `Smarkets ${v.smarkets_games ?? 0}`,
      `Kalshi ${v.kalshi_games ?? 0}`,
    ];
    if (data.odds_api) bits.push(`Odds API ${data.odds_remaining ?? (v.odds_games ?? "")}`.trim());
    $("status").textContent = `${data.count} рынков · ${bits.join(" · ")}`;
    renderRows();
    if (!opts.keep && state.rows.length) selectRow(0);
    else if (state.selected != null) selectRow(state.selected);
  } catch (err) {
    $("status").textContent = `ошибка: ${err.message}`;
  }
}

async function loadOrders() {
  const data = await getJson("/api/orders");
  const box = $("orders");
  const rows = data.rows || [];
  if (!rows.length) { box.textContent = "пусто"; return; }
  box.innerHTML = rows.map((o) => `<div class="mini-item">
    <span>${o.status} · ${o.title}<br>${o.shares} @ ${o.price} · БК ${o.book_odds}</span>
    ${o.status === "open" ? `<button type="button" class="ghost" data-cancel="${o.id}">снять</button>` : ""}
  </div>`).join("");
  box.querySelectorAll("[data-cancel]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      await getJson(`/api/orders/${btn.dataset.cancel}/cancel`, { method: "POST" });
      loadOrders();
    });
  });
}

async function loadPnl() {
  const data = await getJson("/api/pnl");
  $("pnlTotal").textContent = `$${fmtUsd(data.total_profit)}`;
  $("pnlRows").innerHTML = (data.rows || []).map((r) => `<div class="mini-item">
    <span>${r.title} · ${r.book || ""}<br>${r.ts || ""}</span>
    <span class="${roiClass(r.profit)}">$${fmtUsd(r.profit)} (${Number(r.roi_pct || 0).toFixed(2)}%)</span>
  </div>`).join("") || "пусто";
}

function setTab(name) {
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("on", t.dataset.tab === name));
  ["desk", "pnl", "settings"].forEach((id) => $(`view-${id}`).classList.toggle("hidden", id !== name));
  if (name === "pnl") loadPnl();
  if (name === "settings") loadSettings();
}

async function loadSettings() {
  const s = await getJson("/api/settings");
  const f = $("setForm");
  f.telegram_chat_id.value = s.telegram_chat_id || "";
  f.polymarket_funder.value = s.polymarket_funder || "";
  f.signature_type.value = String(s.signature_type || 2);
  f.stake_mirror.value = s.stake_mirror || "";
  f.auto_refresh_sec.value = s.auto_refresh_sec || 20;
  f.sound.checked = !!s.sound;
  $("sound").checked = !!s.sound;
}

$("calcForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  const stake = f.hedge_stake.value ? Number(f.hedge_stake.value) : null;
  try {
    const q = await getJson("/api/calc", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        poly_price: Number(f.poly_price.value),
        book_odds: Number(f.book_odds.value),
        shares: stake ? null : Number(f.shares.value || 100),
        hedge_stake: stake,
        fee_rate: Number(f.fee_rate.value || 0.05),
        taker: f.taker.checked,
        include_rebate: f.include_rebate.checked,
        rebate_rate: 0.15,
      }),
    });
    $("calcOut").innerHTML = `<dl>
      <dt>шейры</dt><dd>${fmtUsd(q.shares)}</dd>
      <dt>Poly $</dt><dd>$${fmtUsd(q.poly_cost)}</dd>
      <dt>ставка БК</dt><dd>$${fmtUsd(q.hedge_stake)}</dd>
      <dt>fee / rebate</dt><dd>$${fmtUsd(q.taker_fee)} / $${fmtUsd(q.rebate_est)}</dd>
      <dt>вложено</dt><dd>$${fmtUsd(q.total_outlay)}</dd>
      <dt>выплата</dt><dd>$${fmtUsd(q.locked_payout)}</dd>
      <dt>профит</dt><dd class="${roiClass(q.profit)}">$${fmtUsd(q.profit)}</dd>
      <dt>ROI</dt><dd class="${roiClass(q.roi_pct)}">${q.roi_pct.toFixed(3)}%</dd>
      <dt>break-even</dt><dd>${q.break_even_odds.toFixed(4)}</dd>
    </dl>`;
  } catch (err) {
    $("calcOut").textContent = err.message;
  }
});

$("hedgeForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const raw = e.target.payouts.value.split(/[,\s]+/).map(Number).filter((n) => n > 0);
  try {
    const h = await getJson("/api/hedge", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ payouts: raw, current_shares: Number(e.target.current.value || 0) }),
    });
    $("hedgeOut").innerHTML = `нужно <b>${fmtUsd(h.needed_shares)}</b> шейров · есть ${fmtUsd(h.current_shares)} · дельта <b>${fmtUsd(h.delta_shares)}</b> · ${h.matched ? "хедж закрыт" : "не закрыт"}`;
  } catch (err) {
    $("hedgeOut").textContent = err.message;
  }
});

$("manualForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const row = currentRow();
  if (!row) return;
  const f = e.target;
  await getJson("/api/manual", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      event_id: row.event_id,
      book: f.book.value,
      team: f.team.value,
      odds: Number(f.odds.value),
    }),
  });
  scan({ keep: true });
});
  const row = currentRow();
  if (!row) return;
  const q0 = row.quotes[0] || {};
  await getJson("/api/orders", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      title: row.title,
      token_id: q0.token_id || "",
      price: Number($("limitPx").value || q0.poly_price),
      shares: Number($("limitSz").value || 100),
      book_odds: Number($("limitOdds").value || 2),
      auto_cancel: $("autoCancel").checked,
      kill_off_min: Number($("killOff").value || 0),
      url: row.url,
    }),
  });
  loadOrders();
});

$("copyLink").addEventListener("click", async () => {
  const row = currentRow();
  if (row?.url) await navigator.clipboard.writeText(row.url);
});
$("hideEvt").addEventListener("click", async () => {
  const row = currentRow();
  if (!row) return;
  await getJson(`/api/hidden/${row.event_id}`, { method: "POST" });
  scan();
});
$("overlay").addEventListener("click", () => {
  const row = currentRow();
  if (!row) return;
  const w = window.open("", "polyhedge-overlay", "width=420,height=520");
  const q0 = row.quotes[0] || {};
  w.document.write(`<body style="font-family:sans-serif;background:#12110f;color:#f3efe6;padding:12px">
    <h3>${row.title}</h3>
    <p>${q0.poly_team || ""} ${q0.poly_price || ""} · BE ${q0.break_even_maker || ""}</p>
    <p><a href="${row.url}" target="_blank" style="color:#e2b657">Polymarket</a></p>
  </body>`);
});

$("pnlForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  const row = currentRow() || {};
  await getJson("/api/pnl", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      title: f.title.value,
      profit: Number(f.profit.value),
      roi_pct: Number(f.roi_pct.value || 0),
      book: f.book.value,
      poly_price: Number(row.quotes?.[0]?.poly_price || 0.5),
      book_odds: Number($("limitOdds").value || 2),
      shares: Number($("limitSz").value || 0),
    }),
  });
  f.reset();
  loadPnl();
});

$("setForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  const body = {
    telegram_chat_id: f.telegram_chat_id.value,
    polymarket_funder: f.polymarket_funder.value,
    signature_type: Number(f.signature_type.value),
    stake_mirror: f.stake_mirror.value,
    auto_refresh_sec: Number(f.auto_refresh_sec.value || 20),
    sound: f.sound.checked,
  };
  if (f.odds_api_key.value && !f.odds_api_key.value.startsWith("*")) body.odds_api_key = f.odds_api_key.value;
  if (f.sx_api_key.value && !f.sx_api_key.value.startsWith("*")) body.sx_api_key = f.sx_api_key.value;
  if (f.telegram_bot_token.value && !f.telegram_bot_token.value.startsWith("*")) body.telegram_bot_token = f.telegram_bot_token.value;
  if (f.polymarket_private_key.value) body.polymarket_private_key = f.polymarket_private_key.value;
  await getJson("/api/settings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  $("setMsg").textContent = "сохранено локально";
  $("sound").checked = f.sound.checked;
  armTimer();
});
$("tgDetect").addEventListener("click", async () => {
  $("setMsg").textContent = "detect…";
  try {
    const d = await getJson("/api/telegram/detect", { method: "POST" });
    $("setForm").telegram_chat_id.value = d.chat_id;
    $("setMsg").textContent = `chat id ${d.chat_id}`;
  } catch (err) { $("setMsg").textContent = err.message; }
});
$("tgTest").addEventListener("click", async () => {
  try {
    await getJson("/api/telegram/test", { method: "POST" });
    $("setMsg").textContent = "отправлено";
  } catch (err) { $("setMsg").textContent = err.message; }
});

document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => setTab(t.dataset.tab)));
$("refresh").addEventListener("click", () => scan());
["tag", "types", "phase", "hours", "minVolume", "minRoi", "sortBy", "vSx", "vSmarkets", "vKalshi", "vOdds", "vManual"].forEach((id) => $(id).addEventListener("change", () => scan()));
$("pxMode").addEventListener("change", (e) => {
  state.pxMode = e.target.value;
  renderRows();
  if (state.selected != null) selectRow(state.selected);
});

function armTimer() {
  if (state.timer) clearInterval(state.timer);
  const sec = Math.max(8, Number($("setForm").auto_refresh_sec.value || 20));
  state.timer = setInterval(() => scan({ keep: true }), sec * 1000);
}

loadOrders();
getJson("/api/health").then((h) => {
  $("sound").checked = !!h.sound;
  $("status").textContent = h.odds_api ? "Odds API" : "ручной режим";
}).finally(() => { scan(); armTimer(); });
