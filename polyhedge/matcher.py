"""Fuzzy match Polymarket events to Odds API games."""

from __future__ import annotations

import re
from difflib import SequenceMatcher

_STRIP = re.compile(r"[^a-z0-9]+")


def _norm(name: str) -> str:
    s = (name or "").lower()
    s = s.replace("&", " and ")
    s = _STRIP.sub(" ", s)
    parts = [p for p in s.split() if p not in {"fc", "sk", "cf", "the", "esports", "team"}]
    return " ".join(parts)


def _ratio(a: str, b: str) -> float:
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return 0.0
    if na in nb or nb in na:
        return 0.92
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
    min_score: float = 0.72,
) -> dict | None:
    if not home or not away:
        return None
    best = None
    best_score = 0.0
    for game in games:
        score = pair_score(home, away, game.get("home") or "", game.get("away") or "")
        if score > best_score:
            best_score = score
            best = game
    if best is None or best_score < min_score:
        return None
    return {**best, "match_score": round(best_score, 3)}


def best_book_leg(game: dict, team_name: str) -> dict | None:
    """Highest decimal odds on a team across all books in a matched game."""
    best = None
    for book in game.get("books") or []:
        for name, odds in (book.get("outcomes") or {}).items():
            if _ratio(name, team_name) < 0.78:
                continue
            if best is None or odds > best["odds"]:
                best = {
                    "book": book.get("book"),
                    "team": name,
                    "odds": odds,
                    "updated": book.get("updated"),
                }
    return best
