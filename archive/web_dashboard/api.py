"""
FastAPI-приложение web_dashboard.

Запуск: python -m web_dashboard.api  →  http://localhost:8050

Endpoints:
    GET /api/filters    — опции фильтров (тикеры, ТФ, направления, диапазон дат)
    GET /api/dashboard  — все данные по фильтрам (KPI, summary, monthly,
                          concurrent, гистограмма, страница сделок)
"""

import logging
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from web_dashboard import queries
from web_dashboard.queries import DEFAULT_INITIAL_BALANCE, DEFAULT_PORT

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("web_dashboard")

app = FastAPI(title="Backtest Analytics Dashboard", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_DIR = Path(__file__).parent / "static"


def _parse_list(value: Optional[str]) -> Optional[List[str]]:
    """'SBER,GAZP' → ['SBER', 'GAZP']; None/пусто → None (без фильтра)."""
    if not value:
        return None
    items = [v.strip() for v in value.split(",") if v.strip()]
    return items or None


@app.exception_handler(Exception)
async def unhandled_exception_handler(request, exc: Exception):
    logger.error(f"Unhandled error on {request.url}: {exc}", exc_info=True)
    return JSONResponse(status_code=500, content={"detail": str(exc)})


@app.get("/api/filters")
def get_filters():
    """Опции для фильтров."""
    try:
        return queries.fetch_filter_options()
    except Exception as e:
        logger.error(f"Failed to load filter options: {e}", exc_info=True)
        return JSONResponse(status_code=503, content={"detail": f"Database error: {e}"})


@app.get("/api/dashboard")
def get_dashboard(
    tickers: Optional[str] = Query(None, description="CSV список тикеров"),
    timeframes: Optional[str] = Query(None, description="CSV список таймфреймов"),
    directions: Optional[str] = Query(None, description="CSV: Long,Short"),
    date_from: Optional[str] = Query(None, description="YYYY-MM-DD"),
    date_to: Optional[str] = Query(None, description="YYYY-MM-DD"),
    only_profitable: bool = Query(False, description="Скрыть убыточные тикеры (по summary)"),
    initial_balance: float = Query(DEFAULT_INITIAL_BALANCE, description="Стартовый баланс"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=10, le=500),
    sort_by: str = Query("signal_date"),
    sort_dir: str = Query("desc"),
):
    """Все данные дашборда одним ответом (графики обновляются вместе)."""
    ticker_list = _parse_list(tickers)
    timeframe_list = _parse_list(timeframes)
    direction_list = _parse_list(directions)

    try:
        trades = queries.fetch_trades(
            tickers=ticker_list,
            timeframes=timeframe_list,
            directions=direction_list,
            date_from=date_from,
            date_to=date_to,
        )
        summary_rows = queries.fetch_summary(
            tickers=ticker_list,
            timeframes=timeframe_list,
            directions=direction_list,
            date_from=date_from,
            date_to=date_to,
        )
    except Exception as e:
        logger.error(f"Database query failed: {e}", exc_info=True)
        return JSONResponse(status_code=503, content={"detail": f"Database error: {e}"})

    if only_profitable:
        profitable = {r["ticker"] for r in summary_rows if r["total_pnl"] >= 0}
        trades = [t for t in trades if t["ticker"] in profitable]
        summary_rows = [r for r in summary_rows if r["ticker"] in profitable]

    return {
        "metrics": queries.compute_metrics(trades, summary_rows, initial_balance),
        "summary": summary_rows,
        "summary_total": queries.compute_summary_total(summary_rows, initial_balance),
        "monthly": queries.compute_monthly(trades, initial_balance),
        "concurrent": queries.compute_concurrent(trades),
        "pnl_histogram": queries.compute_pnl_histogram(trades),
        "trades": queries.paginate_trades(trades, page, page_size, sort_by, sort_dir),
        "generated_at": None,
    }


@app.get("/api/health")
def health():
    """Проверка соединения с БД."""
    try:
        options = queries.fetch_filter_options()
        return {"status": "ok", "tickers": len(options["tickers"])}
    except Exception as e:
        return JSONResponse(status_code=503, content={"status": "error", "detail": str(e)})


# Статика (SPA) — монтируем в конце, чтобы не перехватить /api
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")


def main():
    import uvicorn

    logger.info("=" * 70)
    logger.info("🌐 Backtest Analytics Dashboard")
    logger.info(f"   URL: http://localhost:{DEFAULT_PORT}")
    logger.info("=" * 70)
    uvicorn.run(app, host="0.0.0.0", port=DEFAULT_PORT, log_level="warning")


if __name__ == "__main__":
    main()
