"""Smarkets public exchange (UK). Winner markets only."""

from __future__ import annotations

import asyncio

import httpx

from polyhedge.polymarket import parse_vs

SM = "https://api.smarkets.com/v3"
TAG_TYPES = {
    "soccer": ["football_match"],
    "football": ["football_match"],
    "nba": ["basketball_match"],
    "nfl": ["american_football_match"],
    "mlb": ["baseball_match"],
    "nhl": ["ice_hockey_match"],
    "tennis": ["tennis_match"],
    "ufc": ["mma_match", "boxing_match"],
    "mma": ["mma_match"],
    "lol": ["league_of_legends_match"],
    "cs2": ["csgo_match"],
    "dota": ["dota_2_match"],
    "esports": [
        "league_of_legends_match",
        "csgo_match",
        "dota_2_match",
        "call_of_duty_match",
    ],
    "sports": [
        "football_match",
        "basketball_match",
        "american_football_match",
        "tennis_match",
        "baseball_match",
        "ice_hockey_match",
        "mma_match",
    ],
}
_WINNER_NAMES = {
    "full-time result",
    "winner",
    "match winner",
    "moneyline",
    "to win",
    "fight winner",
}


def ticks_to_decimal(price: int | float | None) -> float | None:
    try:
        p = float(price)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if p <= 0 or p >= 10000:
        return None
    return round(10000.0 / p, 6)


class SmarketsClient:
    def __init__(self, client: httpx.AsyncClient):
        self.http = client

    async def fetch_event_names(self, *, tag: str = "sports", per_type: int = 30) -> list[dict]:
        types = TAG_TYPES.get(tag.lower(), TAG_TYPES["sports"])
        games: list[dict] = []
        seen: set[str] = set()
        for event_type in types:
            for event in await self._events(event_type, limit=per_type):
                eid = str(event.get("id") or "")
                name = event.get("name") or ""
                home, away = parse_vs(name)
                if not eid or eid in seen or not home or not away:
                    continue
                seen.add(eid)
                url = "https://smarkets.com" + (event.get("full_slug") or f"/event/{eid}")
                games.append(
                    {
                        "venue": "smarkets",
                        "book": "Smarkets",
                        "id": eid,
                        "home": home,
                        "away": away,
                        "title": name,
                        "start": event.get("start_datetime"),
                        "url": url,
                        "outcomes": {},
                        "full_slug": event.get("full_slug"),
                        "books": [
                            {"book": "Smarkets", "key": "smarkets", "outcomes": {}, "url": url}
                        ],
                    }
                )
        return games

    async def fill_winners(self, games: list[dict]) -> None:
        sem = asyncio.Semaphore(6)

        async def one(game: dict) -> None:
            async with sem:
                filled = await self._winner_game(game)
            if not filled:
                return
            game["outcomes"] = filled["outcomes"]
            game["books"] = filled["books"]
            game["url"] = filled.get("url") or game.get("url")

        if games:
            await asyncio.gather(*(one(g) for g in games))

    async def fetch_winners(self, *, tag: str = "sports", per_type: int = 24) -> list[dict]:
        games = await self.fetch_event_names(tag=tag, per_type=per_type)
        await self.fill_winners(games)
        return games

    async def _events(self, event_type: str, *, limit: int) -> list[dict]:
        try:
            r = await self.http.get(
                f"{SM}/events/",
                params={"state": "upcoming", "type": event_type, "limit": str(limit)},
                timeout=20.0,
            )
            r.raise_for_status()
            return r.json().get("events") or []
        except httpx.HTTPError:
            return []

    async def _winner_game(self, event: dict) -> dict | None:
        eid = event.get("id")
        name = event.get("name") or event.get("title") or ""
        home, away = event.get("home"), event.get("away")
        if not home or not away:
            home, away = parse_vs(name)
        if not eid or not home or not away:
            return None
        try:
            r = await self.http.get(f"{SM}/events/{eid}/markets/", timeout=15.0)
            r.raise_for_status()
            markets = r.json().get("markets") or []
        except httpx.HTTPError:
            return None
        winner = next(
            (m for m in markets if (m.get("name") or "").strip().lower() in _WINNER_NAMES),
            None,
        )
        if not winner:
            winner = next((m for m in markets if m.get("category") == "winner"), None)
        if not winner:
            return None
        mid = winner.get("id")
        try:
            cr = await self.http.get(f"{SM}/markets/{mid}/contracts/", timeout=15.0)
            cr.raise_for_status()
            contracts = cr.json().get("contracts") or []
            qr = await self.http.get(f"{SM}/markets/{mid}/quotes/", timeout=15.0)
            qr.raise_for_status()
            quotes = qr.json() or {}
        except httpx.HTTPError:
            return None
        outcomes: dict[str, float] = {}
        for c in contracts:
            cname = c.get("name") or ""
            if not cname or cname.lower() in {"draw", "tie"}:
                continue
            q = quotes.get(str(c.get("id"))) or {}
            offers = q.get("offers") or []
            if not offers:
                continue
            dec = ticks_to_decimal(offers[0].get("price"))
            if dec:
                outcomes[cname] = dec
        url = "https://smarkets.com" + (event.get("full_slug") or f"/event/{eid}")
        return {
            "venue": "smarkets",
            "book": "Smarkets",
            "id": eid,
            "home": home,
            "away": away,
            "title": name,
            "start": event.get("start_datetime"),
            "url": url,
            "outcomes": outcomes,
            "books": [
                {
                    "book": "Smarkets",
                    "key": "smarkets",
                    "outcomes": outcomes,
                    "url": url,
                }
            ],
        }
