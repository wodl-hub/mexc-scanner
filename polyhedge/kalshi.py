"""Kalshi public sports winner markets (GAME / MATCH series)."""

from __future__ import annotations

import httpx

from polyhedge.polymarket import parse_vs

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"

CORE_SERIES = {
    "nfl": ["KXNFLGAME"],
    "nba": ["KXNBAGAME"],
    "mlb": ["KXMLBGAME"],
    "nhl": ["KXNHLGAME"],
    "soccer": [
        "KXEPLGAME",
        "KXMLSGAME",
        "KXUCLGAME",
        "KXUELEAGUEGAME",
        "KXBUNDESLIGAGAME",
        "KXSERIEAGAME",
        "KXLALIGAGAME",
        "KXLIGUE1GAME",
    ],
    "football": [
        "KXEPLGAME",
        "KXMLSGAME",
        "KXUCLGAME",
        "KXUELEAGUEGAME",
    ],
    "tennis": ["KXITFMATCH", "KXATPMATCH", "KXWTAMATCH"],
    "ufc": ["KXUFCFIGHT", "KXMMAMATCH"],
    "mma": ["KXUFCFIGHT", "KXMMAMATCH"],
    "esports": ["KXLOLMATCH", "KXLCKGAME", "KXLPLGAME", "KXCS2MATCH"],
    "lol": ["KXLOLMATCH", "KXLCKGAME", "KXLPLGAME"],
    "cs2": ["KXCS2MATCH"],
    "sports": [
        "KXNFLGAME",
        "KXNBAGAME",
        "KXMLBGAME",
        "KXNHLGAME",
        "KXEPLGAME",
        "KXMLSGAME",
        "KXUCLGAME",
        "KXUELEAGUEGAME",
        "KXATPMATCH",
        "KXUFCFIGHT",
    ],
}


def _usd_to_decimal(price: str | float | None) -> float | None:
    try:
        p = float(price)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if p <= 0 or p >= 1:
        return None
    return round(1.0 / p, 6)


def _tag_ok(tags: list, wanted: str) -> bool:
    if wanted in {"sports", ""}:
        return True
    blob = " ".join(str(t).lower() for t in tags)
    mapping = {
        "soccer": "soccer football",
        "football": "soccer football",
        "nfl": "football nfl",
        "nba": "basketball nba",
        "mlb": "baseball mlb",
        "nhl": "hockey nhl",
        "tennis": "tennis",
        "ufc": "mma ufc boxing",
        "mma": "mma ufc",
        "esports": "esports lol dota cs",
        "lol": "lol league",
        "cs2": "cs counter",
    }
    keys = mapping.get(wanted, wanted)
    return any(k in blob for k in keys.split())


class KalshiClient:
    def __init__(self, client: httpx.AsyncClient):
        self.http = client

    async def fetch_sports(self, *, tag: str = "sports", max_series: int = 18) -> list[dict]:
        tickers = list(CORE_SERIES.get(tag.lower(), CORE_SERIES["sports"]))
        extra = await self._extra_series(tag.lower(), already=set(tickers), limit=max_series - len(tickers))
        games: list[dict] = []
        seen: set[str] = set()
        for ticker in tickers + extra:
            if len(games) > 220:
                break
            events = await self._events_for(ticker)
            for event in events:
                game = self._normalize(event)
                if not game or game["id"] in seen:
                    continue
                seen.add(str(game["id"]))
                games.append(game)
        return games

    async def _extra_series(self, tag: str, *, already: set[str], limit: int) -> list[str]:
        if limit <= 0:
            return []
        try:
            r = await self.http.get(f"{KALSHI}/series", params={"category": "Sports"}, timeout=30.0)
            r.raise_for_status()
            series = r.json().get("series") or []
        except httpx.HTTPError:
            return []
        out: list[str] = []
        for s in series:
            ticker = s.get("ticker") or ""
            title = (s.get("title") or "").lower()
            if ticker in already or not ticker:
                continue
            if not (ticker.endswith("GAME") or ticker.endswith("MATCH") or ticker.endswith("FIGHT")):
                continue
            if "total" in title or "spread" in title or "first" in title:
                continue
            if not _tag_ok(s.get("tags") or [], tag):
                continue
            out.append(ticker)
            if len(out) >= limit:
                break
        return out

    async def _events_for(self, series_ticker: str) -> list[dict]:
        try:
            r = await self.http.get(
                f"{KALSHI}/events",
                params={
                    "status": "open",
                    "limit": "50",
                    "series_ticker": series_ticker,
                    "with_nested_markets": "true",
                },
                timeout=20.0,
            )
            r.raise_for_status()
            return r.json().get("events") or []
        except httpx.HTTPError:
            return []

    @staticmethod
    def _normalize(event: dict) -> dict | None:
        title = event.get("title") or ""
        home, away = parse_vs(title)
        outcomes: dict[str, float] = {}
        url = f"https://kalshi.com/markets/{event.get('event_ticker')}"
        for m in event.get("markets") or []:
            name = m.get("yes_sub_title") or m.get("subtitle")
            ask = _usd_to_decimal(m.get("yes_ask_dollars"))
            if name and ask:
                outcomes[name] = ask
        if len(outcomes) < 2:
            return None
        names = list(outcomes)
        return {
            "venue": "kalshi",
            "book": "Kalshi",
            "id": event.get("event_ticker"),
            "home": home or names[0],
            "away": away or names[1],
            "title": title,
            "start": event.get("target_datetime") or event.get("strike_date"),
            "url": url,
            "outcomes": outcomes,
            "books": [{"book": "Kalshi", "key": "kalshi", "outcomes": outcomes, "url": url}],
        }
