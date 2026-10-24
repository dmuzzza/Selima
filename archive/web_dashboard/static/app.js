/* ═══════════════════════════════════════════════════════════════
   Backtest Analytics — логика фронтенда (Chart.js + fetch API)
   ═══════════════════════════════════════════════════════════════ */

"use strict";

// ───────────────────────── Состояние ─────────────────────────

const state = {
    page: 1,
    totalPages: 1,
    sortBy: "signal_date",
    sortDir: "desc",
    charts: {},          // Chart.js инстансы
    filterOptions: null,
};

const $ = (id) => document.getElementById(id);

// ───────────────────────── Утилиты ─────────────────────────

const fmtNum = (v, digits = 2) =>
    v === null || v === undefined || Number.isNaN(+v) ? "—"
        : (+v).toLocaleString("ru-RU", { minimumFractionDigits: digits, maximumFractionDigits: digits });

const fmtMoney = (v) => `${fmtNum(v)} ₽`;
const fmtPct = (v) => `${fmtNum(v)}%`;
const signed = (v) => (v > 0 ? "+" : "") + fmtNum(v);

const pnlClass = (v) => (v > 0 ? "pos" : v < 0 ? "neg" : "neu");

function monthLabel(m) {
    if (!m) return "";
    const [y, mo] = m.split("-");
    const names = ["янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"];
    return `${names[+mo - 1]} ${y.slice(2)}`;
}

function debounce(fn, ms = 300) {
    let t;
    return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

// ───────────────────────── Chart.js: общие настройки ─────────────────────────

Chart.defaults.color = "#8b949e";
Chart.defaults.borderColor = "#2d333b";
Chart.defaults.font.family = "-apple-system, 'Segoe UI', Roboto, sans-serif";
Chart.defaults.font.size = 11;
Chart.defaults.plugins.legend.labels.boxWidth = 12;
Chart.defaults.plugins.legend.labels.boxHeight = 12;

const GRID_COLOR = "#232a33";
const TOOLTIP_STYLE = {
    backgroundColor: "#1f2630",
    borderColor: "#2d333b",
    borderWidth: 1,
    titleColor: "#e6edf3",
    bodyColor: "#c9d1d9",
    padding: 10,
    displayColors: true,
};

function makeChart(id, config) {
    if (state.charts[id]) state.charts[id].destroy();
    state.charts[id] = new Chart($(id), config);
    return state.charts[id];
}

const axisX = (labels) => ({
    grid: { color: GRID_COLOR },
    ticks: { maxRotation: 45, autoSkip: true, maxTicksLimit: 18 },
});

const axisY = (title) => ({
    grid: { color: GRID_COLOR },
    ticks: { callback: (v) => v >= 1000 ? (v / 1000) + "k" : v },
    title: title ? { display: true, text: title } : undefined,
});

// ───────────────────────── Загрузка ─────────────────────────

function buildQuery(extra = {}) {
    const tickers = Array.from($("f-tickers").selectedOptions).map(o => o.value);
    const params = new URLSearchParams();
    if (tickers.length) params.set("tickers", tickers.join(","));
    if ($("f-timeframe").value) params.set("timeframes", $("f-timeframe").value);
    if ($("f-direction").value) params.set("directions", $("f-direction").value);
    if ($("f-date-from").value) params.set("date_from", $("f-date-from").value);
    if ($("f-date-to").value) params.set("date_to", $("f-date-to").value);
    if ($("f-profitable").checked) params.set("only_profitable", "true");
    params.set("page", state.page);
    params.set("sort_by", state.sortBy);
    params.set("sort_dir", state.sortDir);
    for (const [k, v] of Object.entries(extra)) params.set(k, v);
    return params.toString();
}

async function loadDashboard(extra = {}) {
    $("loading").classList.remove("hidden");
    try {
        const resp = await fetch(`/api/dashboard?${buildQuery(extra)}`);
        if (!resp.ok) {
            const err = await resp.json().catch(() => ({}));
            throw new Error(err.detail || `HTTP ${resp.status}`);
        }
        const data = await resp.json();
        renderAll(data);
        setStatus(true);
    } catch (e) {
        console.error(e);
        setStatus(false, e.message);
    } finally {
        $("loading").classList.add("hidden");
    }
}

function setStatus(ok, message = "") {
    const dot = $("status-dot"), text = $("status-text");
    dot.className = "status-dot " + (ok ? "ok" : "err");
    text.textContent = ok ? "данные актуальны" : `ошибка: ${message}`;
}

// ───────────────────────── Фильтры ─────────────────────────

async function loadFilters() {
    const resp = await fetch("/api/filters");
    const opts = await resp.json();
    state.filterOptions = opts;

    const tickersSel = $("f-tickers");
    tickersSel.innerHTML = "";
    for (const t of opts.tickers) {
        const o = document.createElement("option");
        o.value = t; o.textContent = t;
        tickersSel.appendChild(o);
    }
    tickersSel.size = Math.min(1, opts.tickers.length); // компактно; раскрывается кликом

    const tfSel = $("f-timeframe");
    tfSel.innerHTML = '<option value="">Все</option>';
    for (const tf of opts.timeframes) {
        const o = document.createElement("option");
        o.value = tf; o.textContent = tf;
        tfSel.appendChild(o);
    }

    if (opts.date_min) $("f-date-from").min = opts.date_min;
    if (opts.date_max) $("f-date-to").max = opts.date_max;
}

function resetFilters() {
    Array.from($("f-tickers").options).forEach(o => o.selected = false);
    $("f-timeframe").value = "";
    $("f-direction").value = "";
    $("f-date-from").value = "";
    $("f-date-to").value = "";
    $("f-profitable").checked = false;
    state.page = 1;
    loadDashboard();
}

// ───────────────────────── Рендер: KPI ─────────────────────────

function kpiCard(label, value, hint, cls = "neu") {
    return `
        <div class="kpi">
            <div class="kpi__label">${label}</div>
            <div class="kpi__value ${cls}">${value}</div>
            ${hint ? `<div class="kpi__hint">${hint}</div>` : ""}
        </div>`;
}

function renderKpi(m) {
    const pfCls = m.profit_factor >= 1 ? "pos" : "neg";
    $("kpi-grid").innerHTML = [
        kpiCard("Total PnL", fmtMoney(m.total_pnl), `${signed(m.total_pnl_percent)}% от баланса`, pnlClass(m.total_pnl)),
        kpiCard("Win Rate", fmtPct(m.win_rate), `${m.winning}W / ${m.losing}L из ${m.closed_trades}`),
        kpiCard("Profit Factor", fmtNum(m.profit_factor, 2), "прибыль / убыток", pfCls),
        kpiCard("Expectancy", fmtMoney(m.expectancy), "матожидание на сделку", pnlClass(m.expectancy)),
        kpiCard("Payoff Ratio", fmtNum(m.payoff_ratio, 2), "avg win / avg loss"),
        kpiCard("Avg Win", fmtMoney(m.avg_win), "", "pos"),
        kpiCard("Avg Loss", fmtMoney(m.avg_loss), "", "neg"),
        kpiCard("Max Drawdown", fmtMoney(m.max_drawdown), `${fmtNum(m.max_drawdown_pct)}% от пика`, "neg"),
        kpiCard("Sharpe Ratio", fmtNum(m.sharpe_ratio, 2), "аннуализированный (мес.)", m.sharpe_ratio >= 1 ? "pos" : "neu"),
        kpiCard("Z-Score", fmtNum(m.z_score, 2), "серии сделок"),
        kpiCard("Сделки", `${m.closed_trades}`, `${m.open_trades} открытых, всего ${m.total_trades}`),
        kpiCard("Баланс", fmtMoney(m.final_balance), `старт ${fmtMoney(m.initial_balance)}`, pnlClass(m.final_balance - m.initial_balance)),
        kpiCard("Avg MFE (TP)", fmtPct(m.avg_mfe), "средний ход в плюс"),
        kpiCard("Avg MAE (SL)", fmtPct(m.avg_mae), "средний ход в минус"),
        kpiCard("Median Long ↑", fmtPct(m.median_long_up_move), "после лонг-сигнала"),
        kpiCard("Median Short ↓", fmtPct(m.median_short_down_move), "после шорт-сигнала"),
    ].join("");
}

// ───────────────────────── Рендер: графики ─────────────────────────

function renderCharts(d) {
    const months = d.monthly.months.map(monthLabel);

    // 1. Кривая баланса
    makeChart("chart-balance", {
        type: "line",
        data: {
            labels: months,
            datasets: [{
                label: "Баланс",
                data: d.monthly.balance,
                borderColor: "#4f8ff7",
                backgroundColor: (ctx) => {
                    const g = ctx.chart.ctx.createLinearGradient(0, 0, 0, 320);
                    g.addColorStop(0, "rgba(79,143,247,0.35)");
                    g.addColorStop(1, "rgba(79,143,247,0)");
                    return g;
                },
                fill: true,
                tension: 0.25,
                pointRadius: 2,
                pointHoverRadius: 5,
            }],
        },
        options: {
            responsive: true, maintainAspectRatio: false,
            interaction: { mode: "index", intersect: false },
            plugins: {
                tooltip: { ...TOOLTIP_STYLE, callbacks: { label: (c) => ` Баланс: ${fmtMoney(c.parsed.y)}` } },
                legend: { display: false },
            },
            scales: { x: axisX(), y: { ...axisY("₽"), beginAtZero: false } },
        },
    });

    // 2. PnL по месяцам
    makeChart("chart-pnl", {
        type: "bar",
        data: {
            labels: months,
            datasets: [{
                label: "PnL",
                data: d.monthly.pnl,
                backgroundColor: d.monthly.pnl.map(v => v >= 0 ? "rgba(63,185,80,0.7)" : "rgba(248,81,73,0.7)"),
                borderColor: d.monthly.pnl.map(v => v >= 0 ? "#3fb950" : "#f85149"),
                borderWidth: 1,
                borderRadius: 4,
            }],
        },
        options: {
            responsive: true, maintainAspectRatio: false,
            interaction: { mode: "index", intersect: false },
            plugins: {
                tooltip: { ...TOOLTIP_STYLE, callbacks: { label: (c) => ` PnL: ${fmtMoney(c.parsed.y)}` } },
                legend: { display: false },
            },
            scales: { x: axisX(), y: axisY("₽") },
        },
    });

    // 3. Одновременные открытые позиции (среднее + пик, Long/Short)
    const cc = d.concurrent;
    makeChart("chart-concurrent", {
        data: {
            labels: cc.months.map(monthLabel),
            datasets: [
                {
                    type: "bar", label: "Long (среднее)",
                    data: cc.long_avg,
                    backgroundColor: "rgba(63,185,80,0.55)", borderRadius: 3,
                },
                {
                    type: "bar", label: "Short (среднее)",
                    data: cc.short_avg,
                    backgroundColor: "rgba(248,81,73,0.55)", borderRadius: 3,
                },
                {
                    type: "line", label: "Long (пик)",
                    data: cc.long_max,
                    borderColor: "#3fb950", borderDash: [5, 4],
                    pointRadius: 2, tension: 0.3, fill: false,
                },
                {
                    type: "line", label: "Short (пик)",
                    data: cc.short_max,
                    borderColor: "#f85149", borderDash: [5, 4],
                    pointRadius: 2, tension: 0.3, fill: false,
                },
            ],
        },
        options: {
            responsive: true, maintainAspectRatio: false,
            interaction: { mode: "index", intersect: false },
            plugins: {
                tooltip: {
                    ...TOOLTIP_STYLE,
                    callbacks: {
                        label: (c) => ` ${c.dataset.label}: ${fmtNum(c.parsed.y, 1)}`,
                    },
                },
            },
            scales: { x: axisX(), y: { ...axisY("сделок"), beginAtZero: true } },
        },
    });

    // 4. Win / Loss по месяцам (stacked)
    makeChart("chart-winloss", {
        type: "bar",
        data: {
            labels: months,
            datasets: [
                { label: "Win", data: d.monthly.win_count, backgroundColor: "rgba(63,185,80,0.7)", borderRadius: 3 },
                { label: "Loss", data: d.monthly.loss_count, backgroundColor: "rgba(248,81,73,0.7)", borderRadius: 3 },
            ],
        },
        options: {
            responsive: true, maintainAspectRatio: false,
            interaction: { mode: "index", intersect: false },
            plugins: { tooltip: TOOLTIP_STYLE },
            scales: {
                x: { ...axisX(), stacked: true },
                y: { ...axisY("сделок"), stacked: true, beginAtZero: true, ticks: { precision: 0 } },
            },
        },
    });

    // 5. Гистограмма PnL (%)
    const hist = d.pnl_histogram;
    const histLabels = hist.edges.slice(0, -1).map((e, i) => `${fmtNum((e + hist.edges[i + 1]) / 2, 1)}%`);
    makeChart("chart-hist", {
        data: {
            labels: histLabels,
            datasets: [
                { type: "bar", label: "Long", data: hist.long, backgroundColor: "rgba(63,185,80,0.55)", borderRadius: 2 },
                { type: "bar", label: "Short", data: hist.short, backgroundColor: "rgba(248,81,73,0.55)", borderRadius: 2 },
                {
                    type: "line", label: "Все сделки", data: hist.all,
                    borderColor: "#bc8cff", backgroundColor: "rgba(188,140,255,0.15)",
                    fill: true, tension: 0.35, pointRadius: 0, borderWidth: 2,
                },
            ],
        },
        options: {
            responsive: true, maintainAspectRatio: false,
            interaction: { mode: "index", intersect: false },
            plugins: { tooltip: TOOLTIP_STYLE },
            scales: { x: axisX(), y: { ...axisY("сделок"), beginAtZero: true } },
        },
    });

    // 6. Среднее время удержания
    makeChart("chart-hold", {
        type: "line",
        data: {
            labels: months,
            datasets: [{
                label: "Дней в сделке",
                data: d.monthly.avg_hold,
                borderColor: "#d29922",
                backgroundColor: "rgba(210,153,34,0.15)",
                fill: true, tension: 0.3, pointRadius: 2,
            }],
        },
        options: {
            responsive: true, maintainAspectRatio: false,
            interaction: { mode: "index", intersect: false },
            plugins: { tooltip: { ...TOOLTIP_STYLE, callbacks: { label: (c) => ` ${fmtNum(c.parsed.y, 1)} дн.` } }, legend: { display: false } },
            scales: { x: axisX(), y: { ...axisY("дней"), beginAtZero: true } },
        },
    });
}

// ───────────────────────── Рендер: сводная таблица ─────────────────────────

const SUMMARY_COLS = [
    ["ticker", "Тикер", true],
    ["timeframe", "ТФ", true],
    ["total_trades", "Trades"],
    ["closed_trades", "Closed"],
    ["winning", "Win"],
    ["losing", "Loss"],
    ["win_rate", "WR %"],
    ["avg_win", "Avg Win ₽"],
    ["avg_loss", "Avg Loss ₽"],
    ["avg_mfe", "Avg MFE %"],
    ["avg_mae", "Avg MAE %"],
    ["avg_hold_days", "Hold дн."],
    ["total_pnl", "Total PnL ₽"],
    ["total_pnl_percent", "PnL %"],
    ["final_balance", "Final ₽"],
];

function renderSummary(rows, total) {
    const thead = $("summary-table").querySelector("thead");
    const tbody = $("summary-table").querySelector("tbody");

    thead.innerHTML = "<tr>" + SUMMARY_COLS.map(([k, label, isText]) =>
        `<th class="${isText ? "text-col" : ""}">${label}</th>`).join("") + "</tr>";

    const rowHtml = (r, isTotal = false) => `
        <tr class="${isTotal ? "total-row" : ""}">
            <td class="text-col"><b>${r.ticker}</b></td>
            <td class="text-col">${r.timeframe}</td>
            <td>${r.total_trades}</td>
            <td>${r.closed_trades}</td>
            <td class="pos">${r.winning}</td>
            <td class="neg">${r.losing}</td>
            <td>${fmtNum(r.win_rate, 1)}</td>
            <td class="pos">${fmtNum(r.avg_win)}</td>
            <td class="neg">${fmtNum(r.avg_loss)}</td>
            <td>${fmtNum(r.avg_mfe, 1)}</td>
            <td>${fmtNum(r.avg_mae, 1)}</td>
            <td>${fmtNum(r.avg_hold_days, 1)}</td>
            <td class="${pnlClass(r.total_pnl)}"><b>${fmtNum(r.total_pnl)}</b></td>
            <td class="${pnlClass(r.total_pnl)}">${signed(r.total_pnl_percent)}%</td>
            <td>${fmtNum(r.final_balance, 0)}</td>
        </tr>`;

    tbody.innerHTML = rows.map(r => rowHtml(r)).join("")
        + (total ? rowHtml(total, true) : "");
}

// ───────────────────────── Рендер: таблица сделок ─────────────────────────

const TRADES_COLS = [
    ["signal_date", "Дата сигнала", true],
    ["ticker", "Тикер", true],
    ["timeframe", "ТФ", true],
    ["direction", "Напр.", true],
    ["pattern_name", "Паттерн", true],
    ["entry_price", "Entry"],
    ["exit_price", "Exit"],
    ["days_held", "Дн."],
    ["pnl_net", "PnL ₽"],
    ["pnl_percent", "PnL %"],
    [null, "Статус"],
];

function renderTrades(data) {
    const thead = $("trades-table").querySelector("thead");
    const tbody = $("trades-table").querySelector("tbody");

    thead.innerHTML = "<tr>" + TRADES_COLS.map(([key, label, isText]) => {
        if (!key) return `<th class="text-col">${label}</th>`;
        const active = state.sortBy === key;
        const arrow = active ? (state.sortDir === "asc" ? " ▲" : " ▼") : "";
        return `<th class="${isText ? "text-col" : ""} ${active ? "sorted" : ""}" data-sort="${key}">${label}${arrow}</th>`;
    }).join("") + "</tr>";

    tbody.innerHTML = data.items.map(t => `
        <tr>
            <td class="text-col">${t.signal_date}</td>
            <td class="text-col"><b>${t.ticker}</b></td>
            <td class="text-col">${t.timeframe}</td>
            <td class="text-col"><span class="${t.direction === "Long" ? "dir-long" : "dir-short"}">${t.direction}</span></td>
            <td class="text-col">${t.pattern_name}</td>
            <td>${fmtNum(t.entry_price)}</td>
            <td>${t.exit_price === null ? "—" : fmtNum(t.exit_price)}</td>
            <td>${t.days_held}</td>
            <td class="${pnlClass(t.pnl_net)}"><b>${signed(t.pnl_net)}</b></td>
            <td class="${pnlClass(t.pnl_percent)}">${signed(t.pnl_percent)}%</td>
            <td class="text-col"><span class="tag ${t.is_open ? "tag--open" : "tag--closed"}">${t.is_open ? "OPEN" : "CLOSED"}</span></td>
        </tr>`).join("") || '<tr><td colspan="11" class="text-col muted">Нет сделок по выбранным фильтрам</td></tr>';

    // Пагинация
    state.page = data.page;
    state.totalPages = data.total_pages;
    $("trades-count").textContent = `(${data.total})`;
    $("pg-info").textContent = `стр. ${data.page} из ${data.total_pages}`;
    $("pg-first").disabled = $("pg-prev").disabled = data.page <= 1;
    $("pg-next").disabled = $("pg-last").disabled = data.page >= data.total_pages;
}

function renderAll(d) {
    renderKpi(d.metrics);
    renderCharts(d);
    renderSummary(d.summary, d.summary_total);
    renderTrades(d.trades);
    $("generated-at").textContent =
        `Обновлено: ${new Date().toLocaleString("ru-RU")} · стратегия: Dual MA Crossover · данные: strategy_backtest_trades`;
}

// ───────────────────────── События ─────────────────────────

function bindEvents() {
    const reload = () => { state.page = 1; loadDashboard(); };

    $("btn-apply").addEventListener("click", reload);
    $("btn-reset").addEventListener("click", resetFilters);
    $("f-timeframe").addEventListener("change", reload);
    $("f-direction").addEventListener("change", reload);
    $("f-profitable").addEventListener("change", reload);
    $("f-date-from").addEventListener("change", reload);
    $("f-date-to").addEventListener("change", reload);

    // Тикеры: применяем с задержкой (мультивыбор)
    $("f-tickers").addEventListener("change", debounce(reload, 500));

    // Поиск в сводной таблице (клиентский)
    $("summary-search").addEventListener("input", debounce(() => {
        const q = $("summary-search").value.trim().toUpperCase();
        const tbody = $("summary-table").querySelector("tbody");
        const rows = Array.from(tbody.querySelectorAll("tr"));
        const totalRow = rows.pop();
        rows.forEach(r => {
            const ticker = r.querySelector("td")?.textContent.trim().toUpperCase() || "";
            r.style.display = !q || ticker.includes(q) ? "" : "none";
        });
        if (totalRow) totalRow.style.display = "";
    }, 200));

    // Сортировка таблицы сделок
    $("trades-table").querySelector("thead").addEventListener("click", (e) => {
        const th = e.target.closest("th[data-sort]");
        if (!th) return;
        const key = th.dataset.sort;
        if (state.sortBy === key) {
            state.sortDir = state.sortDir === "asc" ? "desc" : "asc";
        } else {
            state.sortBy = key;
            state.sortDir = "desc";
        }
        loadDashboard();
    });

    // Пагинация
    const goPage = (p) => loadDashboard({ page: Math.max(1, Math.min(p, state.totalPages)) });
    $("pg-first").addEventListener("click", () => goPage(1));
    $("pg-prev").addEventListener("click", () => goPage(state.page - 1));
    $("pg-next").addEventListener("click", () => goPage(state.page + 1));
    $("pg-last").addEventListener("click", () => goPage(state.totalPages));
}

// ───────────────────────── Инициализация ─────────────────────────

(async function init() {
    bindEvents();
    try {
        await loadFilters();
        setStatus(true);
    } catch (e) {
        setStatus(false, "не удалось загрузить фильтры — проверьте соединение с БД");
        console.error(e);
    }
    loadDashboard();
})();
