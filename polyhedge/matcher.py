"""Fuzzy match Polymarket events to book games."""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

_STRIP = re.compile(r"[^a-z0-9]+")
_GENERIC_LAST = {
    "united",
    "city",
    "town",
    "athletic",
    "club",
    "sport",
    "county",
    "state",
    "university",
    "de",
}
# Poly often uses nicknames; books use city + nickname.
_ALIASES = {
    "jets": ("new york jets", "new york"),
    "giants": ("new york giants", "new york"),
    "yankees": ("new york yankees", "new york"),
    "mets": ("new york mets", "new york m", "new york"),
    "knicks": ("new york knicks", "new york"),
    "rangers": ("new york rangers", "new york"),
    "islanders": ("new york islanders", "new york"),
    "49ers": ("san francisco 49ers", "san francisco"),
    "niners": ("san francisco 49ers", "san francisco"),
    "steelers": ("pittsburgh steelers", "pittsburgh"),
    "cowboys": ("dallas cowboys", "dallas"),
    "eagles": ("philadelphia eagles", "philadelphia"),
    "commanders": ("washington commanders", "washington"),
    "ravens": ("baltimore ravens", "baltimore"),
    "bengals": ("cincinnati bengals", "cincinnati"),
    "browns": ("cleveland browns", "cleveland"),
    "bills": ("buffalo bills", "buffalo"),
    "dolphins": ("miami dolphins", "miami"),
    "patriots": ("new england patriots", "new england"),
    "chargers": ("los angeles chargers", "los angeles", "la chargers"),
    "rams": ("los angeles rams", "los angeles", "la rams"),
    "raiders": ("las vegas raiders", "las vegas"),
    "chiefs": ("kansas city chiefs", "kansas city"),
    "broncos": ("denver broncos", "denver"),
    "saints": ("new orleans saints", "new orleans"),
    "falcons": ("atlanta falcons", "atlanta"),
    "panthers": ("carolina panthers", "carolina"),
    "buccaneers": ("tampa bay buccaneers", "tampa bay"),
    "packers": ("green bay packers", "green bay"),
    "bears": ("chicago bears", "chicago"),
    "lions": ("detroit lions", "detroit"),
    "vikings": ("minnesota vikings", "minnesota"),
    "seahawks": ("seattle seahawks", "seattle"),
    "cardinals": ("arizona cardinals", "arizona"),
    "titans": ("tennessee titans", "tennessee"),
    "colts": ("indianapolis colts", "indianapolis"),
    "jaguars": ("jacksonville jaguars", "jacksonville"),
    "texans": ("houston texans", "houston"),
    "celtics": ("boston celtics", "boston"),
    "lakers": ("los angeles lakers", "los angeles"),
    "warriors": ("golden state warriors", "golden state"),
}


def _fold(name: str) -> str:
    nk = unicodedata.normalize("NFKD", name or "")
    return "".join(ch for ch in nk if not unicodedata.combining(ch))


def _norm(name: str) -> str:
    s = _fold(name).lower()
    s = s.replace("&", " and ")
    s = _STRIP.sub(" ", s)
    drop = {"fc", "sk", "cf", "the", "esports", "esport", "team", "gaming"}
    parts = [p for p in s.split() if p not in drop]
    return " ".join(parts)


def _ratio(a: str, b: str) -> float:
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    if na in nb or nb in na:
        return 0.92
    variants_a = {na, *(_ALIASES.get(na, ()))}
    variants_b = {nb, *(_ALIASES.get(nb, ()))}
    for va in variants_a:
        for vb in variants_b:
            if va == vb or va in vb or vb in va:
                return 0.9
    la, lb = na.split(), nb.split()
    if len(la) == 1 and len(lb) >= 2 and la[0] not in _GENERIC_LAST and la[0] == lb[-1]:
        return 0.9
    if len(lb) == 1 and len(la) >= 2 and lb[0] not in _GENERIC_LAST and lb[0] == la[-1]:
        return 0.9
    ta, tb = set(la), set(lb)
    if ta and tb:
        inter = len(ta & tb)
        if inter:
            jacc = inter / len(ta | tb)
            if jacc >= 0.5:
                return round(0.72 + 0.28 * jacc, 3)
    return SequenceMatcher(None, na, nb).ratio()


def pair_score(home_a: str, away_a: str, home_b: str, away_b: str) -> float:
    same = (_ratio(home_a, home_b) + _ratio(away_a, away_b)) / 2
    swapped = (_ratio(home_a, away_b) + _ratio(away_a, home_b)) / 2
    return max(same, swapped)


def _has_odds(game: dict) -> bool:
    if game.get("outcomes"):
        return True
    return any((b.get("outcomes") or {}) for b in game.get("books") or [])


def best_game_match(
    home: str | None,
    away: str | None,
    games: list[dict],
    *,
    min_score: float = 0.68,
) -> dict | None:
    matches = all_game_matches(home, away, games, min_score=min_score)
    return matches[0] if matches else None


def all_game_matches(
    home: str | None,
    away: str | None,
    games: list[dict],
    *,
    min_score: float = 0.68,
) -> list[dict]:
    if not home or not away:
        return []
    ranked: list[tuple[float, dict]] = []
    for game in games:
        score = pair_score(home, away, game.get("home") or "", game.get("away") or "")
        title = game.get("title") or ""
        if title:
            score = max(score, (_ratio(home, title) + _ratio(away, title)) / 2)
        if score >= min_score:
            ranked.append((score, {**game, "match_score": round(score, 3)}))
    ranked.sort(key=lambda x: (-x[0], not _has_odds(x[1])))
    return [g for _, g in ranked[:8]]


def _outcome_for(game: dict, outcomes: dict, team_name: str) -> tuple[str, float] | None:
    best: tuple[float, str, float] | None = None
    for name, odds in outcomes.items():
        score = _ratio(name, team_name)
        if best is None or score > best[0]:
            best = (score, name, float(odds))
    if best and best[0] >= 0.78:
        return best[1], best[2]
    home, away = game.get("home") or "", game.get("away") or ""
    side = None
    if _ratio(team_name, home) >= 0.68:
        side = home
    elif _ratio(team_name, away) >= 0.68:
        side = away
    if not side:
        return None
    for name, odds in outcomes.items():
        if _ratio(name, side) >= 0.68:
            return name, float(odds)
    return None


def all_book_legs(games: list[dict], team_name: str) -> list[dict]:
    legs = []
    for game in games:
        for book in game.get("books") or []:
            outcomes = book.get("outcomes") or {}
            if not outcomes:
                legs.append(
                    {
                        "book": book.get("book"),
                        "key": book.get("key"),
                        "venue": game.get("venue"),
                        "team": team_name,
                        "odds": None,
                        "url": book.get("url") or game.get("url"),
                        "listed": True,
                    }
                )
                continue
            picked = _outcome_for(game, outcomes, team_name)
            if not picked:
                continue
            name, odds = picked
            legs.append(
                {
                    "book": book.get("book"),
                    "key": book.get("key"),
                    "venue": game.get("venue"),
                    "team": name,
                    "odds": odds,
                    "url": book.get("url") or game.get("url"),
                    "updated": book.get("updated"),
                }
            )
    best: dict[str, dict] = {}
    for leg in legs:
        k = str(leg.get("book"))
        if k not in best or (leg.get("odds") or 0) > (best[k].get("odds") or 0):
            best[k] = leg
    return list(best.values())


def best_book_leg(game: dict, team_name: str) -> dict | None:
    legs = all_book_legs([game], team_name)
    priced = [x for x in legs if x.get("odds")]
    if not priced:
        return None
    return max(priced, key=lambda x: x["odds"])
