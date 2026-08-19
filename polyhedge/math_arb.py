"""Arbitrage and Polymarket fee math.

Taker fee (protocol): fee = shares * fee_rate * p * (1 - p)
Makers are not charged. Maker rebate is a pool redistribution, not a guaranteed
per-order credit — we expose it only as an estimate.
"""

from __future__ import annotations

from dataclasses import dataclass


def taker_fee_usdc(shares: float, price: float, fee_rate: float) -> float:
    if shares <= 0 or price <= 0 or price >= 1 or fee_rate <= 0:
        return 0.0
    fee = shares * fee_rate * price * (1.0 - price)
    return round(fee, 5)


def effective_poly_cost(
    shares: float,
    price: float,
    fee_rate: float,
    *,
    taker: bool,
) -> float:
    notional = shares * price
    if taker:
        return notional + taker_fee_usdc(shares, price, fee_rate)
    return notional


def maker_rebate_estimate(
    shares: float,
    price: float,
    fee_rate: float,
    rebate_rate: float,
) -> float:
    """Upper-bound estimate: rebate_rate * taker fee at this price."""
    if rebate_rate <= 0:
        return 0.0
    return round(taker_fee_usdc(shares, price, fee_rate) * rebate_rate, 5)


@dataclass(frozen=True)
class ForkQuote:
    shares: float
    poly_price: float
    book_odds: float
    poly_cost: float
    hedge_stake: float
    taker_fee: float
    rebate_est: float
    total_outlay: float
    locked_payout: float
    profit: float
    roi_pct: float
    break_even_odds: float

    def as_dict(self) -> dict:
        return {
            "shares": round(self.shares, 4),
            "poly_price": round(self.poly_price, 6),
            "book_odds": round(self.book_odds, 6),
            "poly_cost": round(self.poly_cost, 4),
            "hedge_stake": round(self.hedge_stake, 4),
            "taker_fee": round(self.taker_fee, 5),
            "rebate_est": round(self.rebate_est, 5),
            "total_outlay": round(self.total_outlay, 4),
            "locked_payout": round(self.locked_payout, 4),
            "profit": round(self.profit, 4),
            "roi_pct": round(self.roi_pct, 4),
            "break_even_odds": round(self.break_even_odds, 6),
        }


def break_even_decimal_odds(
    poly_price: float,
    fee_rate: float,
    *,
    taker: bool,
) -> float:
    """Minimum book decimal odds on the opposite side to lock 0% after fees.

    Buy C shares on Polymarket at price p. Hedge stake S = C / odds.
    Profit = C - (C*p + fee) - S. Set profit = 0 → odds = C / (C - poly_cost).
    """
    if poly_price <= 0 or poly_price >= 1:
        return float("inf")
    shares = 100.0
    poly_cost = effective_poly_cost(shares, poly_price, fee_rate, taker=taker)
    remainder = shares - poly_cost
    if remainder <= 0:
        return float("inf")
    return shares / remainder


def quote_fork(
    *,
    shares: float | None = None,
    hedge_stake: float | None = None,
    poly_price: float,
    book_odds: float,
    fee_rate: float = 0.05,
    taker: bool = False,
    rebate_rate: float = 0.0,
    include_rebate: bool = False,
) -> ForkQuote:
    if poly_price <= 0 or poly_price >= 1:
        raise ValueError("poly_price must be in (0, 1)")
    if book_odds <= 1:
        raise ValueError("book_odds must be decimal odds > 1")

    if shares is None and hedge_stake is None:
        shares = 100.0
    if shares is None:
        shares = hedge_stake * book_odds
    if shares <= 0:
        raise ValueError("shares must be positive")

    hedge = shares / book_odds
    fee = taker_fee_usdc(shares, poly_price, fee_rate) if taker else 0.0
    poly_cost = shares * poly_price + fee
    rebate = maker_rebate_estimate(shares, poly_price, fee_rate, rebate_rate)
    credit = rebate if (include_rebate and not taker) else 0.0
    outlay = poly_cost + hedge - credit
    payout = shares
    profit = payout - outlay
    roi = (profit / outlay * 100.0) if outlay > 0 else 0.0
    be = break_even_decimal_odds(poly_price, fee_rate, taker=taker)
    return ForkQuote(
        shares=shares,
        poly_price=poly_price,
        book_odds=book_odds,
        poly_cost=poly_cost,
        hedge_stake=hedge,
        taker_fee=fee,
        rebate_est=rebate,
        total_outlay=outlay,
        locked_payout=payout,
        profit=profit,
        roi_pct=roi,
        break_even_odds=be,
    )


def needed_shares_for_hedge(hedge_stake: float, book_odds: float) -> float:
    return hedge_stake * book_odds


def hedge_stake_for_shares(shares: float, book_odds: float) -> float:
    if book_odds <= 0:
        raise ValueError("book_odds must be positive")
    return shares / book_odds
