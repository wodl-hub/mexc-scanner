"""Local PolyHedge HTTP API. Bind to localhost; keys never leave this process."""

from __future__ import annotations

import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from polyhedge.math_arb import break_even_decimal_odds, quote_fork
from polyhedge.matcher import best_book_leg, best_game_match
from polyhedge.odds_api import TAG_TO_SPORTS, OddsApiClient
from polyhedge.polymarket import PolymarketClient

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
load_dotenv(ROOT / ".env")

_http: httpx.AsyncClient | None = None
_cache: dict[str, tuple[float, object]] = {}


def _env(name: str) -> str:
    return (os.getenv(name) or "").strip()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global _http
    _http = httpx.AsyncClient(
        headers={"User-Agent": "PolyHedge/0.1 (local)"},
        timeout=30.0,
        follow_redirects=True,
    )
    yield
    await _http.aclose()
    _http = None


app = FastAPI(title="PolyHedge", lifespan=lifespan)
if STATIC.exists():
    app.mount("/static", StaticFiles(directory=STATIC), name="static")


def cache_get(key: str, ttl: float):
    hit = _cache.get(key)
    if not hit:
        return None
    ts, value = hit
    if time.time() - ts > ttl:
        return None
    return value


def cache_set(key: str, value: object) -> None:
    _cache[key] = (time.time(), value)


def poly() -> PolymarketClient:
    assert _http is not None
    return PolymarketClient(_http)


def odds_client() -> OddsApiClient | None:
    key = _env("ODDS_API_KEY")
    if not key:
        return None
    assert _http is not None
    return OddsApiClient(_http, key)


class CalcIn(BaseModel):
    poly_price: float = Field(gt=0, lt=1)
    book_odds: float = Field(gt=1)
    shares: float | None = Field(default=None, gt=0)
    hedge_stake: float | None = Field(default=None, gt=0)
    fee_rate: float = Field(default=0.05, ge=0, le=1)
    taker: bool = False
    rebate_rate: float = Field(default=0.15, ge=0, le=1)
    include_rebate: bool = False


@app.get("/")
async def index():
    path = STATIC / "index.html"
    if not path.exists():
        raise HTTPException(500, "UI not found")
    return FileResponse(path)


@app.get("/api/health")
async def health():
    oc = odds_client()
    return {
        "ok": True,
        "odds_api": bool(oc),
        "telegram": bool(_env("TELEGRAM_BOT_TOKEN") and _env("TELEGRAM_CHAT_ID")),
        "trading": bool(_env("POLYMARKET_PRIVATE_KEY")),
        "disclaimer": "Profit is locked only after both legs fill. This is a local tool, not financial advice.",
    }


@app.get("/api/scan")
async def scan(
    tag: str = Query("sports"),
    hours: float | None = Query(default=72, ge=0, le=720),
    min_volume: float = Query(default=0, ge=0),
    types: str = Query("moneyline,child_moneyline"),
    limit: int = Query(60, ge=1, le=120),
):
    cached = cache_get(f"scan:{tag}:{hours}:{min_volume}:{types}:{limit}", 15)
    if cached is not None:
        return cached

    events = await poly().list_events(
        tag=tag, limit=limit, hours_ahead=hours, min_volume=min_volume
    )
    wanted = {t.strip() for t in types.split(",") if t.strip()}
    games: list[dict] = []
    oc = odds_client()
    if oc:
        sport_keys = TAG_TO_SPORTS.get(tag.lower(), TAG_TO_SPORTS["sports"])
        g_cached = cache_get(f"odds:{tag}", 45)
        if g_cached is None:
            games = await oc.fetch_h2h(sport_keys)
            cache_set(f"odds:{tag}", games)
        else:
            games = g_cached  # type: ignore[assignment]

    rows = []
    for event in events:
        matched = best_game_match(event.get("home"), event.get("away"), games) if games else None
        for market in event["markets"]:
            if wanted and market["sports_type"] not in wanted:
                continue
            legs = market["legs"]
            if len(legs) < 2:
                continue
            # Gamma bestBid/bestAsk refer to outcome 0. Last prices are a fallback.
            p0 = legs[0].get("best_ask") or legs[0].get("last") or 0
            p1 = legs[1].get("last") or 0
            if p1 <= 0 and p0:
                p1 = max(0.001, 1 - float(p0))
            quotes = []
            for idx, leg in enumerate(legs):
                price = float(leg.get("best_ask") or 0) if idx == 0 else float(p1 if idx == 1 else 0)
                if idx == 1 and (not price or price <= 0):
                    price = float(leg.get("last") or 0)
                if price <= 0.01 or price >= 0.99:
                    continue
                opposite = legs[1 - idx]["name"]
                book_leg = None
                fork = None
                if matched:
                    book_leg = best_book_leg(matched, opposite)
                    if book_leg:
                        try:
                            fork = quote_fork(
                                shares=100,
                                poly_price=price,
                                book_odds=book_leg["odds"],
                                fee_rate=market["fee_rate"],
                                taker=False,
                                rebate_rate=market["rebate_rate"],
                            ).as_dict()
                        except ValueError:
                            fork = None
                quotes.append(
                    {
                        "poly_team": leg["name"],
                        "poly_price": price,
                        "token_id": leg["token_id"],
                        "hedge_team": opposite,
                        "break_even_maker": round(
                            break_even_decimal_odds(price, market["fee_rate"], taker=False), 4
                        ),
                        "break_even_taker": round(
                            break_even_decimal_odds(price, market["fee_rate"], taker=True), 4
                        ),
                        "book": book_leg,
                        "fork": fork,
                    }
                )
            if not quotes:
                continue
            best_roi = max((q["fork"]["roi_pct"] for q in quotes if q.get("fork")), default=None)
            rows.append(
                {
                    "event_id": event["id"],
                    "event_slug": event["slug"],
                    "title": event["title"],
                    "market_id": market["id"],
                    "question": market["question"],
                    "group": market["group"],
                    "sports_type": market["sports_type"],
                    "phase": event["phase"],
                    "start": event["start"],
                    "volume24hr": market["volume24hr"] or event["volume24hr"],
                    "liquidity": market["liquidity"] or event["liquidity"],
                    "fee_rate": market["fee_rate"],
                    "rebate_rate": market["rebate_rate"],
                    "url": market["url"],
                    "home": event["home"],
                    "away": event["away"],
                    "odds_match": {
                        "home": matched.get("home"),
                        "away": matched.get("away"),
                        "score": matched.get("match_score"),
                        "sport": matched.get("sport"),
                    }
                    if matched
                    else None,
                    "quotes": quotes,
                    "best_roi": best_roi,
                }
            )

    rows.sort(key=lambda r: (r["best_roi"] is None, -(r["best_roi"] or 0), -r["volume24hr"]))
    payload = {
        "count": len(rows),
        "odds_api": bool(oc),
        "odds_remaining": oc.remaining if oc else None,
        "rows": rows,
    }
    cache_set(f"scan:{tag}:{hours}:{min_volume}:{types}:{limit}", payload)
    return payload


@app.get("/api/book")
async def book(token_id: str):
    token_id = token_id.strip()
    if not token_id:
        raise HTTPException(400, "token_id required")
    hit = cache_get(f"book:{token_id}", 2.0)
    if hit is not None:
        return hit
    try:
        data = await poly().get_book(token_id)
    except httpx.HTTPStatusError as exc:
        raise HTTPException(exc.response.status_code, exc.response.text[:300]) from exc
    cache_set(f"book:{token_id}", data)
    return data


@app.post("/api/calc")
async def calc(body: CalcIn):
    try:
        q = quote_fork(
            shares=body.shares,
            hedge_stake=body.hedge_stake,
            poly_price=body.poly_price,
            book_odds=body.book_odds,
            fee_rate=body.fee_rate,
            taker=body.taker,
            rebate_rate=body.rebate_rate,
            include_rebate=body.include_rebate,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return q.as_dict()


@app.get("/api/event/{event_id}")
async def event_detail(event_id: str, tag: str = "sports"):
    events = await poly().list_events(tag=tag, limit=120, hours_ahead=None)
    for event in events:
        if event["id"] == str(event_id):
            return event
    raise HTTPException(404, "Event not in current sports window; widen filters.")
