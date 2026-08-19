"""The Odds API (optional, official). Not a bookmaker scrape."""

from __future__ import annotations

from datetime import datetime, timezone

import httpx

ODDS_HOST = "https://api.the-odds-api.com/v4"

TAG_TO_SPORTS = {
    "soccer": [
        "soccer_uefa_champs_league",
        "soccer_epl",
        "soccer_spain_la_liga",
        "soccer_usa_mls",
        "soccer_uefa_europa_league",
        "soccer_germany_bundesliga",
        "soccer_italy_serie_a",
        "soccer_france_ligue_one",
        "soccer_uefa_champs_league_qualification",
    ],
    "football": [
        "soccer_uefa_champs_league",
        "soccer_epl",
        "soccer_spain_la_liga",
        "soccer_usa_mls",
    ],
    "nba": ["basketball_nba"],
    "nfl": ["americanfootball_nfl"],
    "mlb": ["baseball_mlb"],
    "nhl": ["icehockey_nhl"],
    "tennis": ["tennis_atp_us_open", "tennis_wta_us_open"],
    "ufc": ["mma_mixed_martial_arts"],
    "mma": ["mma_mixed_martial_arts"],
    "esports": [],
    "lol": [],
    "cs2": [],
    "dota": [],
    "sports": [
        "soccer_uefa_champs_league",
        "soccer_epl",
        "soccer_spain_la_liga",
        "soccer_usa_mls",
        "soccer_uefa_europa_league",
        "soccer_germany_bundesliga",
        "soccer_italy_serie_a",
        "soccer_france_ligue_one",
        "basketball_nba",
        "americanfootball_nfl",
        "icehockey_nhl",
        "baseball_mlb",
        "mma_mixed_martial_arts",
        "tennis_atp_us_open",
    ],
}


class OddsApiClient:
    def __init__(self, client: httpx.AsyncClient, api_key: str):
        self.http = client
        self.api_key = api_key
        self.remaining: str | None = None
        self.used: str | None = None

    def _headers_meta(self, response: httpx.Response) -> None:
        self.remaining = response.headers.get("x-requests-remaining")
        self.used = response.headers.get("x-requests-used")

    async def fetch_h2h(self, sport_keys: list[str]) -> list[dict]:
        games: list[dict] = []
        seen: set[str] = set()
        for key in sport_keys:
            if key in seen:
                continue
            seen.add(key)
            try:
                r = await self.http.get(
                    f"{ODDS_HOST}/sports/{key}/odds",
                    params={
                        "apiKey": self.api_key,
                        "regions": "eu,uk,us",
                        "markets": "h2h",
                        "oddsFormat": "decimal",
                        "dateFormat": "iso",
                    },
                    timeout=25.0,
                )
                self._headers_meta(r)
                if r.status_code == 404:
                    continue
                r.raise_for_status()
                payload = r.json()
            except httpx.HTTPError:
                continue
            now = datetime.now(timezone.utc)
            for game in payload:
                commence = game.get("commence_time")
                try:
                    ts = datetime.fromisoformat(str(commence).replace("Z", "+00:00"))
                    if ts < now and (now - ts).total_seconds() > 6 * 3600:
                        continue
                except (TypeError, ValueError):
                    pass
                books = []
                for bm in game.get("bookmakers") or []:
                    for market in bm.get("markets") or []:
                        if market.get("key") != "h2h":
                            continue
                        outcomes = {
                            o["name"]: float(o["price"])
                            for o in (market.get("outcomes") or [])
                            if o.get("name") and o.get("price")
                        }
                        if outcomes:
                            books.append(
                                {
                                    "book": bm.get("title") or bm.get("key"),
                                    "key": bm.get("key"),
                                    "updated": bm.get("last_update"),
                                    "outcomes": outcomes,
                                    "url": "https://www.pinnacle.com/ru/"
                                    if (bm.get("key") or "").lower() == "pinnacle"
                                    else "",
                                }
                            )
                books.sort(
                    key=lambda b: 0 if (b.get("key") or "").lower() == "pinnacle" else 1
                )
                games.append(
                    {
                        "id": game.get("id"),
                        "sport": game.get("sport_key"),
                        "home": game.get("home_team"),
                        "away": game.get("away_team"),
                        "start": commence,
                        "books": books,
                    }
                )
        return games
