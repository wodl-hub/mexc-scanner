"""Polymarket Gamma + CLOB public clients."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

import httpx

from polyhedge.matcher import _ratio

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"

SPORT_TAGS = {
    "sports": "sports",
    "soccer": "soccer",
    "football": "soccer",
    "nba": "nba",
    "nfl": "nfl",
    "mlb": "mlb",
    "nhl": "nhl",
    "tennis": "tennis",
    "ufc": "ufc",
    "mma": "mma",
    "esports": "esports",
    "cs2": "counter-strike-2",
    "lol": "league-of-legends",
    "dota": "dota-2",
    "valorant": "valorant",
    "hockey": "nhl",
}


def _loads(value: Any, default: Any):
    if value is None:
        return default
    if isinstance(value, (list, dict)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return default
    return default


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_vs(title: str) -> tuple[str | None, str | None]:
    raw = (title or "").strip()
    if ":" in raw and " vs" in raw.lower():
        raw = raw.split(":", 1)[1].strip()
    for sep in (" vs. ", " vs ", " VS ", " v ", " at ", " @ "):
        if sep in raw:
            left, right = raw.split(sep, 1)
            right = right.split(" - ")[0].split("(")[0].strip(" .")
            return left.strip(), right.strip()
    return None, None


_WILL_WIN = re.compile(r"will\s+(.+?)\s+win\b", re.I)


def _expand_yes_no_legs(market: dict, home: str | None, away: str | None) -> None:
    """Map Yes/No moneylines onto actual team names so books can hedge."""
    legs = market.get("legs") or []
    if len(legs) != 2:
        return
    names = [(leg.get("name") or "").strip().lower() for leg in legs]
    if set(names) != {"yes", "no"}:
        return
    question = market.get("question") or ""
    match = _WILL_WIN.search(question)
    mentioned = (match.group(1) if match else "") or (market.get("group") or "")
    yes_team, no_team = home, away
    if mentioned and home and away:
        if _ratio(mentioned, away) > _ratio(mentioned, home):
            yes_team, no_team = away, home
        else:
            yes_team, no_team = home, away
    elif mentioned:
        yes_team, no_team = mentioned, None
    yes_i = 0 if names[0] == "yes" else 1
    no_i = 1 - yes_i
    if yes_team:
        legs[yes_i]["name"] = yes_team
        legs[yes_i]["binary"] = "Yes"
    if no_team:
        legs[no_i]["name"] = no_team
        legs[no_i]["binary"] = "No"


def classify_phase(start_iso: str | None) -> str:
    if not start_iso:
        return "unknown"
    try:
        ts = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
    except ValueError:
        return "unknown"
    now = datetime.now(timezone.utc)
    if ts <= now:
        return "live"
    return "prematch"


class PolymarketClient:
    def __init__(self, client: httpx.AsyncClient):
        self.http = client

    async def list_events(
        self,
        *,
        tag: str = "sports",
        limit: int = 80,
        hours_ahead: float | None = None,
        min_volume: float = 0.0,
    ) -> list[dict]:
        slug = SPORT_TAGS.get(tag.lower(), tag.lower())
        params = {
            "active": "true",
            "closed": "false",
            "limit": str(limit),
            "offset": "0",
            "order": "volume24hr",
            "ascending": "false",
            "tag_slug": slug,
        }
        r = await self.http.get(f"{GAMMA}/events", params=params, timeout=30.0)
        r.raise_for_status()
        events = r.json()
        now = datetime.now(timezone.utc)
        out = []
        for event in events:
            start = event.get("startTime") or event.get("startDate")
            if hours_ahead is not None and start:
                try:
                    ts = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
                    if ts > now and (ts - now).total_seconds() > hours_ahead * 3600:
                        continue
                except ValueError:
                    pass
            vol = _f(event.get("volume24hr") or event.get("volume"))
            if vol < min_volume:
                continue
            out.append(self._normalize_event(event))
        return out

    def _normalize_event(self, event: dict) -> dict:
        title = event.get("title") or ""
        home, away = parse_vs(title)
        start = event.get("startTime") or event.get("startDate")
        tags = [t.get("slug") for t in (event.get("tags") or []) if isinstance(t, dict)]
        markets = [self._normalize_market(m, event) for m in (event.get("markets") or [])]
        markets = [m for m in markets if m]
        for market in markets:
            _expand_yes_no_legs(market, home, away)
        return {
            "id": str(event.get("id")),
            "slug": event.get("slug"),
            "title": title,
            "home": home,
            "away": away,
            "start": start,
            "phase": classify_phase(start),
            "volume24hr": _f(event.get("volume24hr")),
            "volume": _f(event.get("volume")),
            "liquidity": _f(event.get("liquidity") or event.get("liquidityClob")),
            "tags": tags,
            "url": f"https://polymarket.com/event/{event.get('slug')}",
            "markets": markets,
        }

    def _normalize_market(self, market: dict, event: dict) -> dict | None:
        if market.get("closed") or not market.get("active", True):
            return None
        outcomes = _loads(market.get("outcomes"), [])
        prices = [_f(x) for x in _loads(market.get("outcomePrices"), [])]
        tokens = _loads(market.get("clobTokenIds"), [])
        if not outcomes or not tokens:
            return None
        fee = market.get("feeSchedule") or {}
        fee_rate = _f(fee.get("rate"), 0.05)
        rebate_rate = _f(fee.get("rebateRate"), 0.15)
        legs = []
        for i, name in enumerate(outcomes):
            token = tokens[i] if i < len(tokens) else None
            price = prices[i] if i < len(prices) else 0.0
            legs.append(
                {
                    "name": name,
                    "token_id": str(token) if token else None,
                    "last": price,
                    "best_bid": _f(market.get("bestBid")) if i == 0 else None,
                    "best_ask": _f(market.get("bestAsk")) if i == 0 else None,
                }
            )
        return {
            "id": str(market.get("id")),
            "question": market.get("question") or event.get("title"),
            "slug": market.get("slug"),
            "group": market.get("groupItemTitle"),
            "sports_type": market.get("sportsMarketType") or "other",
            "condition_id": market.get("conditionId"),
            "volume": _f(market.get("volumeClob") or market.get("volume")),
            "volume24hr": _f(market.get("volume24hrClob") or market.get("volume24hr")),
            "liquidity": _f(market.get("liquidityClob") or market.get("liquidityNum")),
            "fee_rate": fee_rate,
            "rebate_rate": rebate_rate,
            "tick": _f(market.get("orderPriceMinTickSize"), 0.01),
            "min_size": _f(market.get("orderMinSize"), 5),
            "url": f"https://polymarket.com/event/{event.get('slug')}",
            "legs": legs,
        }

    async def get_book(self, token_id: str) -> dict:
        r = await self.http.get(f"{CLOB}/book", params={"token_id": token_id}, timeout=20.0)
        r.raise_for_status()
        raw = r.json()
        bids = [{"price": _f(x["price"]), "size": _f(x["size"])} for x in (raw.get("bids") or [])]
        asks = [{"price": _f(x["price"]), "size": _f(x["size"])} for x in (raw.get("asks") or [])]
        best_bid = bids[-1]["price"] if bids else None
        best_ask = asks[-1]["price"] if asks else None
        return {
            "market": raw.get("market"),
            "token_id": raw.get("asset_id") or token_id,
            "timestamp": raw.get("timestamp"),
            "tick_size": _f(raw.get("tick_size"), 0.01),
            "min_order_size": _f(raw.get("min_order_size"), 5),
            "last_trade_price": _f(raw.get("last_trade_price")),
            "bids": list(reversed(bids)),
            "asks": list(reversed(asks)),
            "best_bid": best_bid,
            "best_ask": best_ask,
        }
