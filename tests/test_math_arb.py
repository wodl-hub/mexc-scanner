from polyhedge.math_arb import break_even_decimal_odds, quote_fork, taker_fee_usdc
from polyhedge.matcher import pair_score
from polyhedge.polymarket import parse_vs as parse_title


def test_sports_taker_fee_peaks_at_fifty():
    assert taker_fee_usdc(100, 0.50, 0.05) == 1.25
    assert taker_fee_usdc(100, 0.30, 0.05) == 1.05
    assert taker_fee_usdc(100, 0.01, 0.05) == 0.0495


def test_maker_has_no_taker_fee():
    q = quote_fork(shares=6000, poly_price=0.11, book_odds=1.13, taker=False)
    assert q.taker_fee == 0
    assert round(q.poly_cost, 2) == 660.0
    assert round(q.hedge_stake, 2) == 5309.73
    assert q.profit > 0


def test_taker_fee_reduces_roi():
    maker = quote_fork(shares=100, poly_price=0.40, book_odds=1.80, taker=False)
    taker = quote_fork(shares=100, poly_price=0.40, book_odds=1.80, taker=True, fee_rate=0.05)
    assert taker.profit < maker.profit
    assert taker.taker_fee > 0


def test_break_even_maker_is_one_over_one_minus_p():
    # p=0.40 → remainder 0.60 → odds 1/0.60 = 1.666...
    be = break_even_decimal_odds(0.40, 0.05, taker=False)
    assert abs(be - (1 / 0.60)) < 1e-9


def test_negative_fork_when_odds_too_low():
    q = quote_fork(shares=100, poly_price=0.55, book_odds=1.10, taker=False)
    assert q.profit < 0
    assert q.roi_pct < 0


def test_parse_esports_title():
    home, away = parse_title("LoL: Team WE vs EDward Gaming (BO3) - LPL Group Ascend")
    assert home == "Team WE"
    assert away == "EDward Gaming"


def test_hedge_cover_from_video_example():
    from polyhedge.math_arb import hedge_cover

    h = hedge_cover([1500, 750], current_shares=0)
    assert h["needed_shares"] == 2250
    assert h["delta_shares"] == 2250
    assert h["matched"] is False
    done = hedge_cover([1500, 750], current_shares=2250)
    assert done["matched"] is True


def test_sx_percentage_to_decimal():
    from polyhedge.sxbet import _pct_to_decimal, taker_decimal_from_maker

    # 50% implied → 2.0 decimal
    assert abs(_pct_to_decimal("50000000000000000000") - 2.0) < 1e-6
    # Maker on 58.75% → taker opposite ≈ 2.424
    assert abs(taker_decimal_from_maker("58750000000000000000") - (1 / 0.4125)) < 1e-5


def test_sx_skips_golf_outright_leagues():
    from polyhedge.sxbet import _game_from_market

    assert (
        _game_from_market(
            {
                "type": 52,
                "teamOneName": "A",
                "teamTwoName": "B",
                "leagueLabel": "Top 5 (Including Ties)",
                "marketHash": "0x1",
            }
        )
        is None
    )
    game = _game_from_market(
        {
            "type": 226,
            "teamOneName": "Los Angeles Rams",
            "teamTwoName": "San Francisco 49ers",
            "leagueLabel": "NFL",
            "marketHash": "0xabc",
            "gameTime": 1789086900,
        }
    )
    assert game and game["home"] == "Los Angeles Rams"


def test_team_pair_score_swapped():
    assert pair_score("Brazil", "Haiti", "Haiti", "Brazil") > 0.9


def test_city_matches_full_nfl_name():
    assert pair_score("Las Vegas", "Houston", "Las Vegas Raiders", "Houston Texans") >= 0.9


def test_esports_org_names():
    assert pair_score("Team WE", "EDward Gaming", "Team We", "Edward Gaming") >= 0.9


def test_parse_home_at_away():
    home, away = parse_title("Atlanta Dream at Los Angeles Sparks")
    assert home == "Atlanta Dream"
    assert away == "Los Angeles Sparks"


def test_smarkets_ticks_to_decimal():
    from polyhedge.smarkets import ticks_to_decimal

    assert abs(ticks_to_decimal(5000) - 2.0) < 1e-6
    assert ticks_to_decimal(0) is None
    assert ticks_to_decimal(10000) is None


def test_all_book_legs_keep_unpriced_listing():
    from polyhedge.matcher import all_book_legs

    games = [
        {
            "venue": "sx",
            "books": [{"book": "SX.bet", "key": "sx", "outcomes": {}, "url": "https://sx.bet/x"}],
        }
    ]
    legs = all_book_legs(games, "Rams")
    assert legs and legs[0]["listed"] is True
    assert legs[0]["odds"] is None

