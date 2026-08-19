"""Fuzzy match Polymarket events to Odds API games."""

from __future__ import annotations

import re
from difflib import SequenceMatcher

_STRIP = re.compile(r"[^a-z0-9]+")


def _norm(name: str) -> str:
    s = (name or "").lower()
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
    ta, tb = set(na.split()), set(nb.split())
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
    ranked.sort(key=lambda x: -x[0])
    return [g for _, g in ranked[:8]]


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
            for name, odds in outcomes.items():
                if _ratio(name, team_name) < 0.78:
                    continue
                legs.append(
                    {
                        "book": book.get("book"),
                        "key": book.get("key"),
                        "venue": game.get("venue"),
                        "team": name,
                        "odds": float(odds),
                        "url": book.get("url") or game.get("url"),
                        "updated": book.get("updated"),
                    }
                )
    # keep best odds per book name
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
