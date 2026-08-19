const $ = (id) => document.getElementById(id);

const state = {
  rows: [],
  selected: null,
  pxMode: "cent",
};

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

async function getJson(url, opts) {
  const r = await fetch(url, opts);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || r.statusText);
  return data;
}

function renderRows() {
  const box = $("rows");
  if (!state.rows.length) {
    box.innerHTML = `<div class="muted">Нет рынков под фильтр. Если The Odds API не задан — ROI будет пустым, но break-even кэф всё равно считается.</div>`;
    return;
  }
  box.innerHTML = state.rows
    .map((row, i) => {
      const q0 = row.quotes[0];
      const roi = row.best_roi;
      const roiTxt = roi == null ? "нет БК" : `${roi.toFixed(2)}%`;
      const be = q0 ? q0.break_even_maker.toFixed(3) : "—";
      const book = q0 && q0.book ? `${q0.book.book} ${q0.book.odds}` : `BE ${be}`;
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
    })
    .join("");
  box.querySelectorAll(".row").forEach((el) => {
    el.addEventListener("click", () => selectRow(Number(el.dataset.i)));
  });
}

async function selectRow(i) {
  state.selected = i;
  renderRows();
  const row = state.rows[i];
  $("selTitle").textContent = row.group || row.sports_type;
  $("selMeta").textContent = `${row.question} · старт ${row.start || "?"} · fee ${row.fee_rate}`;
  $("polyLink").href = row.url;
  const form = $("calcForm");
  const q0 = row.quotes[0];
  if (q0) {
    form.poly_price.value = q0.poly_price;
    form.fee_rate.value = row.fee_rate;
    if (q0.book) form.book_odds.value = q0.book.odds;
  }
  const books = $("books");
  books.innerHTML = `<div class="muted">грузим стакан…</div>`;
  const panes = await Promise.all(
    row.quotes.map(async (q) => {
      if (!q.token_id) return "";
      try {
        const book = await getJson(`/api/book?token_id=${encodeURIComponent(q.token_id)}`);
        const asks = (book.asks || []).slice(0, 10);
        const bids = (book.bids || []).slice(0, 10);
        const askHtml = asks
          .map((l) => `<div class="lvl ask" data-px="${l.price}">${fmtPx(l.price)} <span>${fmtUsd(l.size)}</span></div>`)
          .join("");
        const bidHtml = bids
          .map((l) => `<div class="lvl bid" data-px="${l.price}">${fmtPx(l.price)} <span>${fmtUsd(l.size)}</span></div>`)
          .join("");
        return `<div class="book">
          <h3>${q.poly_team} · ask ${fmtPx(book.best_ask)} · bid ${fmtPx(book.best_bid)}</h3>
          ${askHtml}
          <div class="lvl muted">—— спред ——</div>
          ${bidHtml}
        </div>`;
      } catch (err) {
        return `<div class="book"><h3>${q.poly_team}</h3><p class="muted">${err.message}</p></div>`;
      }
    })
  );
  books.innerHTML = panes.join("") || `<p class="muted">Нет token_id</p>`;
  books.querySelectorAll(".lvl[data-px]").forEach((el) => {
    el.addEventListener("click", () => {
      form.poly_price.value = el.dataset.px;
      form.requestSubmit();
    });
  });
  form.requestSubmit();
}

async function scan() {
  $("status").textContent = "сканирую…";
  const params = new URLSearchParams({
    tag: $("tag").value,
    types: $("types").value,
    hours: $("hours").value || "72",
    min_volume: $("minVolume").value || "0",
  });
  try {
    const data = await getJson(`/api/scan?${params}`);
    state.rows = data.rows || [];
    const odds = data.odds_api ? `Odds API · осталось ${data.odds_remaining ?? "?"}` : "Odds API выкл (ручной кэф)";
    $("status").textContent = `${data.count} рынков · ${odds}`;
    renderRows();
    if (state.rows.length) selectRow(0);
  } catch (err) {
    $("status").textContent = `ошибка: ${err.message}`;
  }
}

$("calcForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  const stake = f.hedge_stake.value ? Number(f.hedge_stake.value) : null;
  const body = {
    poly_price: Number(f.poly_price.value),
    book_odds: Number(f.book_odds.value),
    shares: stake ? null : Number(f.shares.value || 100),
    hedge_stake: stake,
    fee_rate: Number(f.fee_rate.value || 0.05),
    taker: f.taker.checked,
    include_rebate: f.include_rebate.checked,
    rebate_rate: 0.15,
  };
  try {
    const q = await getJson("/api/calc", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    $("calcOut").innerHTML = `<dl>
      <dt>шейры</dt><dd>${fmtUsd(q.shares)}</dd>
      <dt>стоимость Poly</dt><dd>$${fmtUsd(q.poly_cost)}</dd>
      <dt>ставка БК</dt><dd>$${fmtUsd(q.hedge_stake)}</dd>
      <dt>taker fee</dt><dd>$${fmtUsd(q.taker_fee)}</dd>
      <dt>rebate (оценка)</dt><dd>$${fmtUsd(q.rebate_est)}</dd>
      <dt>всего вложено</dt><dd>$${fmtUsd(q.total_outlay)}</dd>
      <dt>выплата при любом исходе</dt><dd>$${fmtUsd(q.locked_payout)}</dd>
      <dt>профит</dt><dd class="${roiClass(q.profit)}">$${fmtUsd(q.profit)}</dd>
      <dt>ROI</dt><dd class="${roiClass(q.roi_pct)}">${q.roi_pct.toFixed(3)}%</dd>
      <dt>break-even кэф</dt><dd>${q.break_even_odds.toFixed(4)}</dd>
    </dl>`;
  } catch (err) {
    $("calcOut").textContent = err.message;
  }
});

$("refresh").addEventListener("click", scan);
["tag", "types", "hours", "minVolume"].forEach((id) => $(id).addEventListener("change", scan));
$("pxMode").addEventListener("change", (e) => {
  state.pxMode = e.target.value;
  renderRows();
  if (state.selected != null) selectRow(state.selected);
});

getJson("/api/health")
  .then((h) => {
    $("status").textContent = h.odds_api ? "Odds API подключен" : "ручной режим (без Odds API)";
  })
  .finally(scan);
