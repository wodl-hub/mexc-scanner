"""Local PolyHedge HTTP API. Bind to localhost; keys never leave this process."""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from polyhedge.alerts import send_telegram
from polyhedge.kalshi import KalshiClient
from polyhedge.math_arb import break_even_decimal_odds, hedge_cover, quote_fork
from polyhedge.matcher import all_book_legs, all_game_matches
from polyhedge.odds_api import TAG_TO_SPORTS, OddsApiClient
from polyhedge.polymarket import PolymarketClient
from polyhedge.smarkets import SmarketsClient
from polyhedge.store import (
    HIDDEN_PATH,
    MANUAL_PATH,
    ORDERS_PATH,
    PNL_PATH,
    load_list,
    load_settings,
    public_settings,
    save_list,
    save_settings,
)
from polyhedge.sxbet import SxClient

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
load_dotenv(ROOT / ".env")

_http: httpx.AsyncClient | None = None
_cache: dict[str, tuple[float, object]] = {}


def _env(name: str) -> str:
    stored = load_settings()
    mapping = {
        "ODDS_API_KEY": stored.get("odds_api_key") or os.getenv("ODDS_API_KEY") or "",
        "TELEGRAM_BOT_TOKEN": stored.get("telegram_bot_token") or os.getenv("TELEGRAM_BOT_TOKEN") or "",
        "TELEGRAM_CHAT_ID": stored.get("telegram_chat_id") or os.getenv("TELEGRAM_CHAT_ID") or "",
        "POLYMARKET_PRIVATE_KEY": stored.get("polymarket_private_key")
        or os.getenv("POLYMARKET_PRIVATE_KEY")
        or "",
        "SX_API_KEY": stored.get("sx_api_key") or os.getenv("SX_API_KEY") or "",
    }
    return str(mapping.get(name) or os.getenv(name) or "").strip()


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


async def _cached_fetch(key: str, ttl: float, factory):
    cached = cache_get(key, ttl)
    if cached is not None:
        return cached, None
    try:
        data = await factory()
        cache_set(key, data)
        return data, None
    except Exception as exc:
        return [], str(exc)[:180]


async def collect_venue_games(tag: str, venues: set[str]) -> tuple[list[dict], dict]:
    assert _http is not None
    status = {
        "odds": False,
        "sx": False,
        "kalshi": False,
        "smarkets": False,
        "odds_remaining": None,
        "sx_games": 0,
        "kalshi_games": 0,
        "smarkets_games": 0,
        "odds_games": 0,
        "errors": {},
    }
    jobs = []

    async def pull_odds():
        oc = odds_client()
        if not oc:
            return "odds", [], None, oc
        keys = TAG_TO_SPORTS.get(tag.lower(), TAG_TO_SPORTS["sports"])
        if not keys:
            return "odds", [], None, oc
        data, err = await _cached_fetch(f"odds:{tag}", 45, lambda: oc.fetch_h2h(keys))
        for g in data:
            g["venue"] = "odds"
        return "odds", data, err, oc

    async def pull_sx():
        data, err = await _cached_fetch(
            f"sx:ml:{tag}",
            40,
            lambda: SxClient(_http, _env("SX_API_KEY")).fetch_moneylines(tag=tag, fill_odds=True),
        )
        return "sx", data, err, None

    async def pull_kalshi():
        data, err = await _cached_fetch(
            f"kalshi:{tag}",
            60,
            lambda: KalshiClient(_http).fetch_sports(tag=tag),
        )
        return "kalshi", data, err, None

    async def pull_smarkets():
        data, err = await _cached_fetch(
            f"smarkets:{tag}",
            50,
            lambda: SmarketsClient(_http).fetch_event_names(tag=tag, per_type=28),
        )
        return "smarkets", data, err, None

    if "odds" in venues:
        jobs.append(pull_odds())
    if "sx" in venues:
        jobs.append(pull_sx())
    if "kalshi" in venues:
        jobs.append(pull_kalshi())
    if "smarkets" in venues:
        jobs.append(pull_smarkets())

    games: list[dict] = []
    if jobs:
        for item in await asyncio.gather(*jobs):
            name, data, err, extra = item
            if err:
                status["errors"][name] = err
            if name == "odds":
                oc = extra
                status["odds"] = bool(odds_client())
                status["odds_games"] = len(data)
                if oc:
                    status["odds_remaining"] = oc.remaining
            else:
                status[name] = not err
                status[f"{name}_games"] = len(data)
            games.extend(data)
    return games, status


async def hydrate_smarkets(games: list[dict], events: list[dict]) -> None:
    need: set[str] = set()
    for event in events:
        for g in all_game_matches(event.get("home"), event.get("away"), games):
            if g.get("venue") == "smarkets" and g.get("id"):
                need.add(str(g["id"]))
    originals = [
        g
        for g in games
        if g.get("venue") == "smarkets" and str(g.get("id")) in need and not g.get("outcomes")
    ]
    if originals:
        assert _http is not None
        await SmarketsClient(_http).fill_winners(originals)


class CalcIn(BaseModel):
    poly_price: float = Field(gt=0, lt=1)
    book_odds: float = Field(gt=1)
    shares: float | None = Field(default=None, gt=0)
    hedge_stake: float | None = Field(default=None, gt=0)
    fee_rate: float = Field(default=0.05, ge=0, le=1)
    taker: bool = False
    rebate_rate: float = Field(default=0.15, ge=0, le=1)
    include_rebate: bool = False


class HedgeIn(BaseModel):
    payouts: list[float] = Field(min_length=1)
    current_shares: float = 0


class SettingsIn(BaseModel):
    odds_api_key: str | None = None
    sx_api_key: str | None = None
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    polymarket_private_key: str | None = None
    polymarket_funder: str | None = None
    signature_type: int | None = None
    stake_mirror: str | None = None
    sound: bool | None = None
    auto_refresh_sec: int | None = None
    min_roi: float | None = None


class PnlIn(BaseModel):
    title: str
    poly_price: float
    book_odds: float
    shares: float
    profit: float
    roi_pct: float
    book: str = ""
    note: str = ""


class ManualBookIn(BaseModel):
    event_id: str
    book: str
    team: str
    odds: float = Field(gt=1)
    url: str = ""


class OrderIn(BaseModel):
    title: str
    token_id: str = ""
    price: float
    shares: float
    book_odds: float
    auto_cancel: bool = True
    kill_off_min: int = 0
    url: str = ""


@app.get("/")
async def index():
    path = STATIC / "index.html"
    if not path.exists():
        raise HTTPException(500, "UI not found")
    return FileResponse(path)


@app.get("/api/health")
async def health():
    oc = odds_client()
    s = load_settings()
    return {
        "ok": True,
        "odds_api": bool(oc),
        "sx": True,
        "kalshi": True,
        "smarkets": True,
        "telegram": bool(_env("TELEGRAM_BOT_TOKEN") and _env("TELEGRAM_CHAT_ID")),
        "trading": bool(_env("POLYMARKET_PRIVATE_KEY")),
        "sound": bool(s.get("sound")),
        "auto_refresh_sec": s.get("auto_refresh_sec") or 20,
        "disclaimer": "Profit is locked only after both legs fill. Local tool, not financial advice.",
    }


@app.get("/api/settings")
async def get_settings():
    return public_settings()


@app.post("/api/settings")
async def post_settings(body: SettingsIn):
    saved = save_settings(body.model_dump(exclude_none=True))
    _cache.clear()
    return public_settings() if saved else public_settings()


@app.post("/api/telegram/detect")
async def telegram_detect():
    token = _env("TELEGRAM_BOT_TOKEN")
    if not token:
        raise HTTPException(400, "Сначала сохраните bot token")
    assert _http is not None
    r = await _http.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=20.0)
    data = r.json()
    if not data.get("ok"):
        raise HTTPException(400, data.get("description") or "Telegram error")
    chat_id = None
    for upd in reversed(data.get("result") or []):
        msg = upd.get("message") or upd.get("my_chat_member") or {}
        chat = msg.get("chat") or {}
        if chat.get("id") is not None:
            chat_id = str(chat["id"])
            break
    if not chat_id:
        raise HTTPException(404, "Напишите /start своему боту и нажмите Detect ещё раз")
    save_settings({"telegram_chat_id": chat_id})
    return {"chat_id": chat_id}


@app.post("/api/telegram/test")
async def telegram_test():
    ok = await send_telegram(
        _env("TELEGRAM_BOT_TOKEN"),
        _env("TELEGRAM_CHAT_ID"),
        "PolyHedge: тест алерта. Стол живой.",
    )
    if not ok:
        raise HTTPException(400, "Не отправилось. Проверьте token и chat id.")
    return {"ok": True}


@app.get("/api/scan")
async def scan(
    tag: str = Query("sports"),
    hours: float | None = Query(default=72, ge=0, le=720),
    min_volume: float = Query(default=0, ge=0),
    min_liq: float = Query(default=0, ge=0),
    min_roi: float | None = Query(default=None),
    phase: str = Query("all"),
    types: str = Query("moneyline,child_moneyline"),
    venues: str = Query("sx,smarkets,kalshi,odds,manual"),
    limit: int = Query(80, ge=1, le=120),
):
    cached = cache_get(
        f"scan:{tag}:{hours}:{min_volume}:{min_liq}:{min_roi}:{phase}:{types}:{venues}:{limit}", 12
    )
    if cached is not None:
        return cached

    events = await poly().list_events(
        tag=tag, limit=limit, hours_ahead=hours, min_volume=min_volume
    )
    wanted = {t.strip() for t in types.split(",") if t.strip()}
    venue_set = {v.strip() for v in venues.split(",") if v.strip()}
    games, venue_status = await collect_venue_games(tag, venue_set)
    if "smarkets" in venue_set:
        await hydrate_smarkets(games, events)
    manual_rows = load_list(MANUAL_PATH) if "manual" in venue_set else []

    hidden = set(load_list(HIDDEN_PATH))
    roi_floor = load_settings().get("min_roi") if min_roi is None else min_roi
    try:
        roi_floor = float(roi_floor or 0)
    except (TypeError, ValueError):
        roi_floor = 0.0

    rows = []
    for event in events:
        if event["id"] in hidden or event.get("slug") in hidden:
            continue
        if phase not in ("all", "", None) and event.get("phase") != phase:
            continue
        matched_games = all_game_matches(event.get("home"), event.get("away"), games) if games else []
        manuals = [m for m in manual_rows if m.get("event_id") == event["id"]]
        if manuals:
            matched_games.append(
                {
                    "home": event.get("home"),
                    "away": event.get("away"),
                    "venue": "manual",
                    "books": [
                        {
                            "book": m.get("book"),
                            "key": "manual",
                            "outcomes": {m.get("team"): m.get("odds")},
                            "url": m.get("url") or "",
                        }
                        for m in manuals
                    ],
                }
            )
        for market in event["markets"]:
            if wanted and market["sports_type"] not in wanted:
                continue
            if min_liq and (market["liquidity"] or 0) < min_liq:
                continue
            legs = market["legs"]
            if len(legs) < 2:
                continue
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
                book_legs = all_book_legs(matched_games, opposite) if matched_games else []
                priced = []
                for bl in book_legs:
                    fork = None
                    if bl.get("odds"):
                        try:
                            fork = quote_fork(
                                shares=100,
                                poly_price=price,
                                book_odds=float(bl["odds"]),
                                fee_rate=market["fee_rate"],
                                taker=False,
                                rebate_rate=market["rebate_rate"],
                            ).as_dict()
                        except ValueError:
                            fork = None
                    priced.append({**bl, "fork": fork})
                best = None
                with_fork = [p for p in priced if p.get("fork")]
                if with_fork:
                    best = max(with_fork, key=lambda x: x["fork"]["roi_pct"])
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
                        "book": best,
                        "books": priced,
                        "fork": (best or {}).get("fork") if best else None,
                    }
                )
            if not quotes:
                continue
            best_roi = max((q["fork"]["roi_pct"] for q in quotes if q.get("fork")), default=None)
            if best_roi is not None and best_roi < roi_floor:
                continue
            if roi_floor > 0 and best_roi is None:
                continue
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
                    "venues": [
                        {
                            "book": g.get("book") or g.get("venue"),
                            "venue": g.get("venue"),
                            "home": g.get("home"),
                            "away": g.get("away"),
                            "score": g.get("match_score"),
                            "url": g.get("url"),
                        }
                        for g in matched_games
                    ],
                    "quotes": quotes,
                    "best_roi": best_roi,
                }
            )

    rows.sort(key=lambda r: (r["best_roi"] is None, -(r["best_roi"] or 0), -r["volume24hr"]))
    payload = {
        "count": len(rows),
        "venues": venue_status,
        "odds_api": bool(venue_status.get("odds")),
        "odds_remaining": venue_status.get("odds_remaining"),
        "rows": rows,
    }
    cache_set(
        f"scan:{tag}:{hours}:{min_volume}:{min_liq}:{min_roi}:{phase}:{types}:{venues}:{limit}",
        payload,
    )
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


@app.post("/api/hedge")
async def hedge(body: HedgeIn):
    try:
        return hedge_cover(body.payouts, body.current_shares)
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/pnl")
async def pnl_list():
    rows = load_list(PNL_PATH)
    total = sum(float(r.get("profit") or 0) for r in rows)
    return {"rows": rows, "total_profit": round(total, 4), "count": len(rows)}


@app.post("/api/pnl")
async def pnl_add(body: PnlIn):
    rows = load_list(PNL_PATH)
    item = body.model_dump()
    item["id"] = str(uuid.uuid4())[:8]
    item["ts"] = datetime.now(timezone.utc).isoformat()
    rows.insert(0, item)
    save_list(PNL_PATH, rows[:500])
    return item


@app.delete("/api/pnl/{item_id}")
async def pnl_del(item_id: str):
    rows = [r for r in load_list(PNL_PATH) if r.get("id") != item_id]
    save_list(PNL_PATH, rows)
    return {"ok": True}


@app.get("/api/orders")
async def orders_list():
    return {"rows": load_list(ORDERS_PATH)}


@app.post("/api/orders")
async def orders_add(body: OrderIn):
    rows = load_list(ORDERS_PATH)
    item = body.model_dump()
    item["id"] = str(uuid.uuid4())[:8]
    item["ts"] = datetime.now(timezone.utc).isoformat()
    item["status"] = "open"
    rows.insert(0, item)
    save_list(ORDERS_PATH, rows)
    token = _env("TELEGRAM_BOT_TOKEN")
    chat = _env("TELEGRAM_CHAT_ID")
    if token and chat:
        await send_telegram(
            token,
            chat,
            f"Лимитка: {item['title']}\n{item['shares']} @ {item['price']}\nкф БК {item['book_odds']}",
        )
    return item


@app.post("/api/orders/{item_id}/cancel")
async def orders_cancel(item_id: str):
    rows = load_list(ORDERS_PATH)
    found = False
    for row in rows:
        if row.get("id") == item_id:
            row["status"] = "cancelled"
            found = True
    if not found:
        raise HTTPException(404, "order not found")
    save_list(ORDERS_PATH, rows)
    return {"ok": True}


@app.get("/api/hidden")
async def hidden_list():
    return {"ids": load_list(HIDDEN_PATH)}


@app.post("/api/hidden/{event_id}")
async def hidden_add(event_id: str):
    ids = load_list(HIDDEN_PATH)
    if event_id not in ids:
        ids.append(event_id)
        save_list(HIDDEN_PATH, ids)
    _cache.clear()
    return {"ids": ids}


@app.get("/api/manual")
async def manual_list(event_id: str | None = None):
    rows = load_list(MANUAL_PATH)
    if event_id:
        rows = [r for r in rows if r.get("event_id") == event_id]
    return {"rows": rows}


@app.post("/api/manual")
async def manual_add(body: ManualBookIn):
    rows = load_list(MANUAL_PATH)
    item = body.model_dump()
    rows = [
        r
        for r in rows
        if not (r.get("event_id") == item["event_id"] and r.get("book") == item["book"] and r.get("team") == item["team"])
    ]
    rows.append(item)
    save_list(MANUAL_PATH, rows)
    _cache.clear()
    return item


@app.get("/api/event/{event_id}")
async def event_detail(event_id: str, tag: str = "sports"):
    events = await poly().list_events(tag=tag, limit=120, hours_ahead=None)
    for event in events:
        if event["id"] == str(event_id):
            return event
    raise HTTPException(404, "Event not in current sports window; widen filters.")
