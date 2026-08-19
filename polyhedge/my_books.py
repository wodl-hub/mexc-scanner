"""User sportsbooks. Paste-in odds; Pinnacle can come from The Odds API."""

from __future__ import annotations

# Personal open-links (as provided). No HTML scrape — these sites have no public odds API
# except Pinnacle via The Odds API.
CATALOG = [
    {
        "key": "stake",
        "name": "Stake",
        "url": "https://stake.com/ru?c=BXlgbMYN&regionKey=GB&country=GB&region=ENG&dir=redirect&modal=restrictedRegion",
        "mode": "paste",
        "hint": "нет публичной линии — вставь кэф",
    },
    {
        "key": "pinnacle",
        "name": "Pinnacle",
        "url": "https://www.pinnacle.com/ru/",
        "mode": "odds_api",
        "odds_key": "pinnacle",
        "hint": "сам, если есть ключ The Odds API",
    },
    {
        "key": "empire",
        "name": "CSGOEmpire",
        "url": "https://csgoempire.com/roulette?utm_source=csgoempire&utm_medium=affiliate&utm_campaign=klon&utm_content=klon",
        "mode": "paste",
        "hint": "нет публичной линии — вставь кэф",
    },
    {
        "key": "mellstroy",
        "name": "Mellstroy",
        "url": "https://mell5126.live/",
        "mode": "paste",
        "hint": "нет публичной линии — вставь кэф",
    },
    {
        "key": "roobet",
        "name": "Roobet",
        "url": "https://roobet.com/?ref=klonka",
        "mode": "paste",
        "hint": "нет публичной линии — вставь кэф",
    },
    {
        "key": "shuffle",
        "name": "Shuffle",
        "url": "https://shuffle.com/ru?langRedirected=true",
        "mode": "paste",
        "hint": "нет публичной линии — вставь кэф",
    },
]


def default_urls() -> dict[str, str]:
    return {b["key"]: b["url"] for b in CATALOG}


def resolve_catalog(settings: dict | None = None) -> list[dict]:
    stored = (settings or {}).get("book_urls") or {}
    mirror = ((settings or {}).get("stake_mirror") or "").strip()
    out = []
    for book in CATALOG:
        item = dict(book)
        custom = (stored.get(book["key"]) or "").strip()
        if book["key"] == "stake" and mirror:
            item["url"] = mirror
        if custom:
            item["url"] = custom
        out.append(item)
    return out


def catalog_names() -> set[str]:
    return {b["name"] for b in CATALOG}
