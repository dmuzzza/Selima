"""
Generate a static HTML backtest report (no server needed).

Data sources (first available wins):
    1. PostgreSQL (SQL): table strategy_backtest_trades
    2. Local CSV fallback: dual_ma_trades.csv

Usage:
    python scripts/generate_report.py
    python scripts/generate_report.py --no-pg --balance 100000

Output:
    report.html  ->  open in browser (double-click), charts via Chart.js CDN.
"""

import argparse
import csv
import json
import os
import sys
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CSV = os.path.join(ROOT, "dual_ma_trades.csv")
DEFAULT_OUTPUT = os.path.join(ROOT, "report.html")


# ═══════════════════════════════════════════════════════════════
# DATA LOADING
# ═══════════════════════════════════════════════════════════════

def load_from_postgres() -> Optional[List[Dict[str, Any]]]:
    """Try to load trades from PostgreSQL via SQL. Returns None if unavailable."""
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(ROOT, ".env"))
        user, pwd = os.getenv("PG_USER"), os.getenv("PG_PASSWORD")
        host, dbname = os.getenv("PG_HOST"), os.getenv("PG_DBNAME")
        if not all([user, pwd, host, dbname]):
            print("[..] PG_* variables missing in .env, falling back to CSV")
            return None
        from sqlalchemy import create_engine, text
        url = f"postgresql://{user}:{pwd}@{host}:5432/{dbname}?sslmode=require&connect_timeout=5"
        engine = create_engine(url, pool_pre_ping=True)
        sql = text("""
            SELECT ticker, timeframe, signal_date, direction,
                   entry_price, exit_price, pnl_net, pnl_percent
            FROM strategy_backtest_trades
            ORDER BY signal_date ASC
        """)
        with engine.connect() as conn:
            rows = conn.execute(sql).mappings().all()
        trades = [{
            "ticker": r["ticker"],
            "timeframe": r["timeframe"],
            "date": r["signal_date"],
            "direction": r["direction"],
            "exit_type": "TP" if (r["pnl_net"] or 0) > 0 else "SL",
            "entry_price": float(r["entry_price"] or 0),
            "exit_price": float(r["exit_price"]) if r["exit_price"] is not None else 0.0,
            "pnl": float(r["pnl_net"] or 0),
            "pnl_pct": float(r["pnl_percent"] or 0),
        } for r in rows]
        print(f"[OK] Loaded {len(trades)} trades from PostgreSQL ({host})")
        return trades
    except Exception as e:
        print(f"[..] PostgreSQL unavailable ({type(e).__name__}), falling back to CSV")
        return None


def load_from_csv(path: str) -> List[Dict[str, Any]]:
    """Load trades from the local dual_ma_trades.csv export (by column index)."""
    trades = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        # Column order: 0 Тикер, 1 Дата, 2 ТФ, 3 Направление, 4 SMA50, 5 AdaptiveMA,
        #               6 EMA200, 7 Цена входа, 8 Цена выхода, 9 Тип выхода,
        #               10 Объём, 11 Комиссия, 12 PnL(₽), 13 PnL(%)
        if len(header) < 14:
            raise ValueError(f"Unexpected CSV header: {header}")
        for row in reader:
            if len(row) < 14 or not row[0].strip():
                continue
            trades.append({
                "ticker": row[0].strip(),
                "timeframe": row[2].strip(),
                "date": datetime.strptime(row[1].strip(), "%Y-%m-%d %H:%M"),
                "direction": row[3].strip(),
                "exit_type": row[9].strip(),
                "entry_price": float(row[7].replace(",", ".")),
                "exit_price": float(row[8].replace(",", ".")),
                "pnl": float(row[12].replace(",", ".")),
                "pnl_pct": float(row[13].replace(",", ".")),
            })
    print(f"[OK] Loaded {len(trades)} trades from CSV: {path}")
    return trades


# ═══════════════════════════════════════════════════════════════
# METRICS
# ═══════════════════════════════════════════════════════════════

def compute_metrics(trades: List[Dict], initial_balance: float) -> Dict[str, Any]:
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    total_pnl = sum(t["pnl"] for t in trades)
    gross_profit = sum(t["pnl"] for t in wins)
    gross_loss = abs(sum(t["pnl"] for t in losses))
    avg_win = gross_profit / len(wins) if wins else 0.0
    avg_loss = -gross_loss / len(losses) if losses else 0.0

    # Equity curve + max drawdown
    equity, peak, max_dd, max_dd_pct = initial_balance, initial_balance, 0.0, 0.0
    for t in sorted(trades, key=lambda x: x["date"]):
        equity += t["pnl"]
        peak = max(peak, equity)
        dd = peak - equity
        if dd > max_dd:
            max_dd = dd
            max_dd_pct = dd / peak * 100 if peak > 0 else 0.0

    pf = gross_profit / gross_loss if gross_loss > 0 else float("inf")
    return {
        "total_trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / len(trades) * 100 if trades else 0.0,
        "total_pnl": total_pnl,
        "total_pnl_pct": total_pnl / initial_balance * 100,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "expectancy": total_pnl / len(trades) if trades else 0.0,
        "profit_factor": pf,
        "max_dd": max_dd,
        "max_dd_pct": max_dd_pct,
        "long_count": sum(1 for t in trades if t["direction"] == "Long"),
        "short_count": sum(1 for t in trades if t["direction"] == "Short"),
        "tp_count": sum(1 for t in trades if t["exit_type"] == "TP"),
        "sl_count": sum(1 for t in trades if t["exit_type"] == "SL"),
    }


def compute_equity(trades: List[Dict], initial_balance: float) -> Dict[str, list]:
    """Cumulative balance curve ordered by trade date."""
    labels, values = [], []
    balance = initial_balance
    for t in sorted(trades, key=lambda x: x["date"]):
        balance += t["pnl"]
        labels.append(t["date"].strftime("%Y-%m-%d %H:%M"))
        values.append(round(balance, 2))
    return {"labels": labels, "values": values}


def compute_monthly(trades: List[Dict]) -> Dict[str, list]:
    monthly: Dict[str, Dict] = defaultdict(lambda: {"pnl": 0.0, "win": 0, "loss": 0})
    for t in trades:
        m = monthly[t["date"].strftime("%Y-%m")]
        m["pnl"] += t["pnl"]
        m["win" if t["pnl"] > 0 else "loss"] += 1
    months = sorted(monthly)
    return {
        "months": months,
        "pnl": [round(monthly[m]["pnl"], 2) for m in months],
        "wins": [monthly[m]["win"] for m in months],
        "losses": [monthly[m]["loss"] for m in months],
    }


def compute_by_ticker(trades: List[Dict]) -> List[Dict]:
    agg: Dict[str, Dict] = defaultdict(lambda: {"pnl": 0.0, "n": 0, "wins": 0})
    for t in trades:
        a = agg[t["ticker"]]
        a["pnl"] += t["pnl"]
        a["n"] += 1
        a["wins"] += 1 if t["pnl"] > 0 else 0
    rows = [{
        "ticker": k,
        "trades": v["n"],
        "win_rate": round(v["wins"] / v["n"] * 100, 1) if v["n"] else 0.0,
        "pnl": round(v["pnl"], 2),
    } for k, v in agg.items()]
    return sorted(rows, key=lambda r: r["pnl"], reverse=True)


def compute_histogram(trades: List[Dict], bins: int = 25) -> Dict[str, list]:
    values = [t["pnl_pct"] for t in trades]
    if not values:
        return {"edges": [], "counts": []}
    lo, hi = min(values), max(values)
    if lo == hi:
        lo, hi = lo - 1, hi + 1
    width = (hi - lo) / bins
    counts = [0] * bins
    for v in values:
        counts[min(int((v - lo) / width), bins - 1)] += 1
    return {"edges": [round(lo + i * width, 2) for i in range(bins + 1)], "counts": counts}


# ═══════════════════════════════════════════════════════════════
# HTML RENDERING
# ═══════════════════════════════════════════════════════════════

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Backtest Report — Dual MA Crossover</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>📊</text></svg>">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.3/dist/chart.umd.min.js"></script>
<style>
  :root {
    --bg: #0d1117; --card: #161b22; --border: #21262d;
    --text: #e6edf3; --muted: #8b949e;
    --green: #3fb950; --red: #f85149; --blue: #58a6ff; --accent: #d2a8ff;
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { background: var(--bg); color: var(--text);
         font-family: 'Segoe UI', system-ui, sans-serif; padding: 24px; }
  .wrap { max-width: 1400px; margin: 0 auto; }
  header { display: flex; justify-content: space-between; align-items: center;
           margin-bottom: 20px; flex-wrap: wrap; gap: 12px; }
  h1 { font-size: 22px; font-weight: 600; }
  .badge { background: var(--card); border: 1px solid var(--border);
           border-radius: 20px; padding: 4px 14px; font-size: 13px; color: var(--muted); }
  .kpi-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
              gap: 12px; margin-bottom: 20px; }
  .kpi { background: var(--card); border: 1px solid var(--border);
         border-radius: 10px; padding: 14px 16px; }
  .kpi .label { font-size: 12px; color: var(--muted); margin-bottom: 6px; }
  .kpi .value { font-size: 22px; font-weight: 700; }
  .kpi .sub { font-size: 11px; color: var(--muted); margin-top: 4px; }
  .pos { color: var(--green); } .neg { color: var(--red); } .neu { color: var(--blue); }
  .charts { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 20px; }
  .chart-card.wide { grid-column: 1 / -1; }
  .card { background: var(--card); border: 1px solid var(--border);
          border-radius: 10px; padding: 18px; }
  .card h2 { font-size: 15px; font-weight: 600; margin-bottom: 12px; color: var(--text); }
  .chart-wrap { position: relative; height: 300px; }
  .chart-card.wide .chart-wrap { height: 340px; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th, td { padding: 8px 12px; text-align: left; border-bottom: 1px solid var(--border); }
  th { color: var(--muted); font-weight: 600; font-size: 12px; text-transform: uppercase; }
  tr:hover td { background: #1c2128; }
  td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
  .table-scroll { max-height: 420px; overflow-y: auto; }
  footer { text-align: center; color: var(--muted); font-size: 12px; padding: 20px 0 8px; }
  @media (max-width: 900px) { .charts { grid-template-columns: 1fr; } }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>📊 Backtest Report — Dual MA Crossover (4h)</h1>
    <span class="badge">Источник: __SOURCE__ · Сгенерирован: __GENERATED__</span>
  </header>

  <section class="kpi-grid">__KPI__</section>

  <section class="charts">
    <div class="card chart-card wide"><h2>Кривая баланса</h2>
      <div class="chart-wrap"><canvas id="c-equity"></canvas></div></div>
    <div class="card chart-card"><h2>PnL по месяцам, ₽</h2>
      <div class="chart-wrap"><canvas id="c-monthly"></canvas></div></div>
    <div class="card chart-card"><h2>Win / Loss по месяцам</h2>
      <div class="chart-wrap"><canvas id="c-winloss"></canvas></div></div>
    <div class="card chart-card"><h2>Распределение PnL по сделкам, %</h2>
      <div class="chart-wrap"><canvas id="c-hist"></canvas></div></div>
    <div class="card chart-card"><h2>PnL по тикерам, ₽</h2>
      <div class="chart-wrap"><canvas id="c-tickers"></canvas></div></div>
  </section>

  <section class="card" style="margin-bottom:20px">
    <h2>Сводка по тикерам</h2>
    <div class="table-scroll"><table id="t-summary">
      <thead><tr><th>Тикер</th><th class="num">Сделок</th>
        <th class="num">Win rate</th><th class="num">PnL, ₽</th></tr></thead>
      <tbody>__SUMMARY_ROWS__</tbody>
    </table></div>
  </section>

  <section class="card">
    <h2>Все сделки (__N_TRADES__)</h2>
    <div class="table-scroll"><table id="t-trades">
      <thead><tr><th>Дата</th><th>Тикер</th><th>Напр.</th><th>Выход</th>
        <th class="num">Вход</th><th class="num">Выход</th>
        <th class="num">PnL, ₽</th><th class="num">PnL, %</th></tr></thead>
      <tbody>__TRADE_ROWS__</tbody>
    </table></div>
  </section>

  <footer>Dual MA Crossover · SMA50 × Adaptive MA14 × EMA200 · 4h · статический отчёт</footer>
</div>

<script>
const DATA = __DATA_JSON__;

Chart.defaults.color = '#8b949e';
Chart.defaults.borderColor = '#21262d';
Chart.defaults.font.family = "'Segoe UI', system-ui, sans-serif";

new Chart(cEquity, {
  type: 'line',
  data: { labels: DATA.equity.labels, datasets: [{
      data: DATA.equity.values, borderColor: '#58a6ff', borderWidth: 2,
      pointRadius: 0, tension: 0.1, fill: true,
      backgroundColor: 'rgba(88,166,255,0.08)' }]},
  options: { plugins: { legend: { display: false } },
    scales: { x: { ticks: { maxTicksLimit: 12 } } },
    interaction: { intersect: false, mode: 'index' } }
});

new Chart(cMonthly, {
  type: 'bar',
  data: { labels: DATA.monthly.months, datasets: [{
      data: DATA.monthly.pnl,
      backgroundColor: DATA.monthly.pnl.map(v => v >= 0 ? '#3fb950cc' : '#f85149cc') }]},
  options: { plugins: { legend: { display: false } } }
});

new Chart(cWinloss, {
  type: 'bar',
  data: { labels: DATA.monthly.months, datasets: [
    { label: 'Win',  data: DATA.monthly.wins,   backgroundColor: '#3fb950cc' },
    { label: 'Loss', data: DATA.monthly.losses, backgroundColor: '#f85149cc' }]},
  options: { scales: { x: { stacked: true }, y: { stacked: true } } }
});

new Chart(cHist, {
  type: 'bar',
  data: { labels: DATA.hist.edges.slice(0, -1).map((e, i) => `${e} … ${DATA.hist.edges[i+1]}`),
    datasets: [{ data: DATA.hist.counts, backgroundColor: '#d2a8ffaa' }]},
  options: { plugins: { legend: { display: false } } }
});

new Chart(cTickers, {
  type: 'bar',
  data: { labels: DATA.by_ticker.map(r => r.ticker),
    datasets: [{ data: DATA.by_ticker.map(r => r.pnl),
      backgroundColor: DATA.by_ticker.map(r => r.pnl >= 0 ? '#3fb950cc' : '#f85149cc') }]},
  options: { plugins: { legend: { display: false } } }
});
</script>
</body>
</html>
"""


def _money(v: float) -> str:
    return f"{v:+,.0f}".replace(",", " ")


def _fmt(v: float, nd: int = 0) -> str:
    return f"{v:,.{nd}f}".replace(",", " ")


def _kpi(label: str, value: str, cls: str = "", sub: str = "") -> str:
    sub_html = f'<div class="sub">{sub}</div>' if sub else ""
    return (f'<div class="kpi"><div class="label">{label}</div>'
            f'<div class="value {cls}">{value}</div>{sub_html}</div>')


def render_html(trades, metrics, equity, monthly, by_ticker, hist, source) -> str:
    gen_time = datetime.now().strftime("%d.%m.%Y %H:%M")
    pf = metrics["profit_factor"]
    pf_str = "∞" if pf == float("inf") else f"{pf:.2f}"

    kpis = "".join([
        _kpi("Total PnL", f"{_money(metrics['total_pnl'])} ₽",
             "pos" if metrics["total_pnl"] >= 0 else "neg",
             f"{metrics['total_pnl_pct']:+.1f}% от старта"),
        _kpi("Сделок", str(metrics["total_trades"]),
             "neu", f"Long {metrics['long_count']} · Short {metrics['short_count']}"),
        _kpi("Win Rate", f"{metrics['win_rate']:.1f}%",
             "pos" if metrics["win_rate"] >= 50 else "neg",
             f"{metrics['wins']} W / {metrics['losses']} L"),
        _kpi("Profit Factor", pf_str,
             "pos" if pf >= 1 else "neg", "gross profit / gross loss"),
        _kpi("Expectancy", f"{_money(metrics['expectancy'])} ₽",
             "pos" if metrics["expectancy"] >= 0 else "neg", "на сделку"),
        _kpi("Avg Win", f"+{_fmt(metrics['avg_win'])} ₽", "pos"),
        _kpi("Avg Loss", f"{_fmt(metrics['avg_loss'])} ₽", "neg"),
        _kpi("Max Drawdown", f"-{_fmt(metrics['max_dd'])} ₽", "neg",
             f"{metrics['max_dd_pct']:.1f}%"),
        _kpi("TP / SL", f"{metrics['tp_count']} / {metrics['sl_count']}",
             "neu", "по типу выхода"),
    ])

    summary_rows = "".join(
        f"<tr><td><b>{r['ticker']}</b></td><td class='num'>{r['trades']}</td>"
        f"<td class='num'>{r['win_rate']}%</td>"
        f"<td class='num {'pos' if r['pnl'] >= 0 else 'neg'}'>{_money(r['pnl'])}</td></tr>"
        for r in by_ticker)

    trade_rows = "".join(
        f"<tr><td>{t['date'].strftime('%Y-%m-%d %H:%M')}</td>"
        f"<td><b>{t['ticker']}</b></td><td>{t['direction']}</td>"
        f"<td>{t['exit_type']}</td>"
        f"<td class='num'>{t['entry_price']:.2f}</td>"
        f"<td class='num'>{t['exit_price']:.2f}</td>"
        f"<td class='num {'pos' if t['pnl'] >= 0 else 'neg'}'>{t['pnl']:+,.0f}</td>"
        f"<td class='num {'pos' if t['pnl_pct'] >= 0 else 'neg'}'>{t['pnl_pct']:+.2f}</td></tr>"
        for t in sorted(trades, key=lambda x: x["date"], reverse=True))

    data_json = json.dumps({
        "equity": equity, "monthly": monthly,
        "by_ticker": by_ticker, "hist": hist,
    }, ensure_ascii=False, default=str).replace("</", "<\\/")

    html = (HTML_TEMPLATE
            .replace("__SOURCE__", source)
            .replace("__GENERATED__", gen_time)
            .replace("__KPI__", kpis)
            .replace("__SUMMARY_ROWS__", summary_rows)
            .replace("__TRADE_ROWS__", trade_rows)
            .replace("__N_TRADES__", str(len(trades)))
            .replace("__DATA_JSON__", data_json))
    return html


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Generate static backtest report.html")
    parser.add_argument("--csv", default=DEFAULT_CSV, help="CSV fallback path")
    parser.add_argument("--balance", type=float, default=100_000, help="Initial balance, RUB")
    parser.add_argument("--no-pg", action="store_true", help="Skip PostgreSQL, use CSV directly")
    parser.add_argument("--out", default=DEFAULT_OUTPUT, help="Output HTML path")
    args = parser.parse_args()

    source = "PostgreSQL"
    trades = None if args.no_pg else load_from_postgres()

    if trades is None:
        if not os.path.exists(args.csv):
            print(f"[ERROR] CSV not found: {args.csv}")
            sys.exit(1)
        trades = load_from_csv(args.csv)
        source = f"CSV ({os.path.basename(args.csv)})"

    if not trades:
        print("[ERROR] No trades loaded")
        sys.exit(1)

    metrics = compute_metrics(trades, args.balance)
    equity = compute_equity(trades, args.balance)
    monthly = compute_monthly(trades)
    by_ticker = compute_by_ticker(trades)
    hist = compute_histogram(trades)

    html = render_html(trades, metrics, equity, monthly, by_ticker, hist, source)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(html)

    pf_str = "inf" if metrics["profit_factor"] == float("inf") else f"{metrics['profit_factor']:.2f}"
    print()
    print("=" * 50)
    print(f"  Total PnL:   {metrics['total_pnl']:+,.0f} RUB  ({metrics['total_pnl_pct']:+.1f}%)")
    print(f"  Trades:      {metrics['total_trades']}  (W {metrics['wins']} / L {metrics['losses']})")
    print(f"  Win Rate:    {metrics['win_rate']:.1f}%   Profit Factor: {pf_str}")
    print(f"  Max DD:      -{metrics['max_dd']:,.0f} RUB ({metrics['max_dd_pct']:.1f}%)")
    print("=" * 50)
    print(f"  Report saved: {args.out}")
    print("  Open it in your browser (double-click).")


if __name__ == "__main__":
    main()
