"""SX.bet public markets + resting orders. No scrape of casino books."""

from __future__ import annotations

from datetime import datetime, timezone

import httpx

SX = "https://api.sx.bet"
# 226 = sports moneyline, 52 = 2-way winner (esports, soccer, UFC, …)
MONEYLINE_TYPES = (226, 52)
TAG_SPORT_IDS = {
    "nba": "1",
    "nhl": "2",
    "mlb": "3",
    "soccer": "5",
    "football": "5",
    "tennis": "6",
    "ufc": "7",
    "mma": "7",
    "nfl": "8",
    "esports": "9",
    "cs2": "9",
    "lol": "9",
    "dota": "9",
}
_SKIP_LEAGUE = ("top 5", "top 10", "top 20", "including ties")


def _pct_to_decimal(percentage_odds: str | int | float | None) -> float | None:
    if percentage_odds in (None, ""):
        return None
    try:
        p = float(percentage_odds) / 1e20
    except (TypeError, ValueError):
        return None
    if p <= 0 or p >= 1:
        return None
    return round(1.0 / p, 6)


def taker_decimal_from_maker(percentage_odds: str | int | float | None) -> float | None:
    """Taker is the opposite side of a resting maker order."""
    if percentage_odds in (None, ""):
        return None
    try:
        p = float(percentage_odds) / 1e20
    except (TypeError, ValueError):
        return None
    if p <= 0 or p >= 1:
        return None
    return round(1.0 / (1.0 - p), 6)


def _skip_league(label: str | None) -> bool:
    s = (label or "").lower()
    return any(bit in s for bit in _SKIP_LEAGUE)


def _game_from_market(m: dict) -> dict | None:
    if int(m.get("type") or 0) not in MONEYLINE_TYPES:
        return None
    if _skip_league(m.get("leagueLabel")):
        return None
    home = m.get("teamOneName") or m.get("outcomeOneName")
    away = m.get("teamTwoName") or m.get("outcomeTwoName")
    if not home or not away or home == away:
        return None
    start = None
    gt = m.get("gameTime")
    if gt:
        try:
            start = datetime.fromtimestamp(int(gt), tz=timezone.utc).isoformat()
        except (TypeError, ValueError, OSError):
            start = str(gt)
    url = f"https://sx.bet/markets/{m.get('marketHash')}"
    return {
        "venue": "sx",
        "book": "SX.bet",
        "id": m.get("marketHash"),
        "home": home,
        "away": away,
        "start": start,
        "sport": m.get("sportLabel"),
        "league": m.get("leagueLabel"),
        "url": url,
        "outcomes": {},
        "market_hash": m.get("marketHash"),
        "books": [{"book": "SX.bet", "key": "sx", "outcomes": {}, "url": url}],
    }


class SxClient:
    def __init__(self, client: httpx.AsyncClient, api_key: str = ""):
        self.http = client
        self.api_key = api_key

    def _headers(self) -> dict:
        h = {"Accept": "application/json", "User-Agent": "PolyHedge/0.1"}
        if self.api_key:
            h["x-sx-api-key"] = self.api_key
        return h

    async def fetch_moneylines(
        self, *, tag: str = "sports", max_pages: int = 8, fill_odds: bool = False
    ) -> list[dict]:
        games: list[dict] = []
        seen: set[str] = set()
        sport_ids = TAG_SPORT_IDS.get(tag.lower())
        for market_type in MONEYLINE_TYPES:
            page_key = None
            for _ in range(max_pages):
                params: dict[str, str] = {
                    "onlyMainLine": "true",
                    "type": str(market_type),
                    "pageSize": "100",
                }
                if sport_ids:
                    params["sportIds"] = sport_ids
                if page_key:
                    params["paginationKey"] = page_key
                r = await self.http.get(
                    f"{SX}/markets/active", params=params, headers=self._headers(), timeout=25.0
                )
                r.raise_for_status()
                payload = r.json().get("data") or {}
                markets = payload.get("markets") or []
                for m in markets:
                    hid = m.get("marketHash")
                    if not hid or hid in seen:
                        continue
                    game = _game_from_market(m)
                    if not game:
                        continue
                    seen.add(hid)
                    games.append(game)
                page_key = payload.get("nextKey") or None
                if not page_key or not markets:
                    break
        if fill_odds:
            await self.fill_odds(games)
        return games

    async def fill_odds(self, games: list[dict], *, batch: int = 20) -> None:
        hashes = [g["market_hash"] for g in games if g.get("market_hash")]
        by_hash = {g["market_hash"]: g for g in games}
        for i in range(0, len(hashes), batch):
            chunk = hashes[i : i + batch]
            odds_map = await self._orders_for(chunk)
            for hid, sides in odds_map.items():
                game = by_hash.get(hid)
                if game:
                    self._apply(game, sides)

    @staticmethod
    def _apply(game: dict, raw: dict) -> None:
        out: dict[str, float] = {}
        if raw.get("one"):
            out[game["home"]] = raw["one"]
        if raw.get("two"):
            out[game["away"]] = raw["two"]
        game["outcomes"] = out
        if game.get("books"):
            game["books"][0]["outcomes"] = out

    async def _orders_for(self, market_hashes: list[str]) -> dict[str, dict]:
        """Best taker decimal per side from public resting orders."""
        if not market_hashes:
            return {}
        try:
            r = await self.http.get(
                f"{SX}/orders",
                params={"marketHashes": ",".join(market_hashes)},
                headers=self._headers(),
                timeout=20.0,
            )
            if r.status_code in (401, 403):
                return await self._orderbook_fallback(market_hashes[0])
            r.raise_for_status()
            orders = r.json().get("data") or []
        except httpx.HTTPError:
            return {}
        best: dict[str, dict[str, float]] = {}
        for order in orders:
            hid = order.get("marketHash")
            dec = taker_decimal_from_maker(order.get("percentageOdds"))
            if not hid or not dec:
                continue
            # Maker on outcome one → taker is outcome two, and vice versa.
            side = "two" if order.get("isMakerBettingOutcomeOne") else "one"
            slot = best.setdefault(hid, {})
            if dec > slot.get(side, 0):
                slot[side] = dec
        return best

    async def _orderbook_fallback(self, market_hash: str) -> dict[str, dict]:
        try:
            r = await self.http.get(
                f"{SX}/orderbook-v3/snapshot",
                params={"marketHash": market_hash, "showTakerPerspective": "true"},
                headers=self._headers(),
                timeout=12.0,
            )
            if r.status_code in (401, 403):
                return {}
            r.raise_for_status()
            data = (r.json() or {}).get("data") or r.json()
        except httpx.HTTPError:
            return {}
        out: dict[str, float] = {}
        for side, key in (("outcomeOne", "one"), ("outcomeTwo", "two")):
            levels = data.get(side) or []
            if not levels:
                continue
            dec = _pct_to_decimal(levels[0].get("percentageOdds"))
            if dec:
                out[key] = dec
        return {market_hash: out} if out else {}
