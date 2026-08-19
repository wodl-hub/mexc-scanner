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


def test_team_pair_score_swapped():
    assert pair_score("Brazil", "Haiti", "Haiti", "Brazil") > 0.9
