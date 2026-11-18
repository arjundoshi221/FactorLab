"""Account performance: net profit, return on capital, TWR, XIRR, monthly returns and attribution.

Everything is in the account's base currency and reconciles to one NAV series: the broker's
account-wide net liquidation per New York trading date, preferring the Flex statement close
over Gateway snapshots. NAV already nets margin borrowing, so the loan is never subtracted
again. External flows are deposits and withdrawals from ``broker.cash_flows``.

Conventions (stated in the response ``basis``):

* A day's external flow is assumed to arrive at the start of that day, so the day's return is
  ``(NAV_t - NAV_{t-1} - CF_t) / (NAV_{t-1} + CF_t)``. Flows between two NAV dates count on the
  later date.
* XIRR discounts by ``days / 365`` (Excel's ``XIRR``) and is annualised.
* Non-base flows convert at the row's own broker rate when known, else the broker
  ``ExchangeRate`` on or before that date, else the nearest later one (counted as approximate).
* Attribution lists every cash line the broker books separately; the residual is
  "investment gains", which also holds FX translation and interest/dividend accrual changes.
"""

from __future__ import annotations

import math
from bisect import bisect_right
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from .indexes import IndexRepository
from .portfolio import ProfileFilter, Repo, validate_profile_filter
from .portfolio_analytics import (
    Benchmark,
    RiskStats,
    benchmark_values,
    compound_series,
    daily_changes,
    rebased,
    risk_stats,
)

router = APIRouter()

NEW_YORK = ZoneInfo("America/New_York")
EXTERNAL_TYPES = ("deposit", "withdrawal")


def ny_date(value: datetime) -> date:
    stamp = value if value.tzinfo else value.replace(tzinfo=UTC)
    return stamp.astimezone(NEW_YORK).date()


def text(value: Any) -> str:
    """A ClickHouse string as text: ``FixedString`` columns arrive as bytes."""
    if isinstance(value, bytes):
        value = value.rstrip(b"\x00").decode("utf-8", "replace")
    return "" if value is None else str(value).strip()


def to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        result = float(value if not isinstance(value, Decimal) else str(value))
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


# ---------------------------------------------------------------------------
# Pure calculations
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DayReturn:
    day: date
    nav: float
    flow: float
    profit: float
    ret: float | None  # None when the day's capital base is not positive


def daily_returns(
    opening: tuple[date, float] | None, navs: list[tuple[date, float]], flows: dict[date, float]
) -> list[DayReturn]:
    """Per NAV date: profit and start-of-day-flow return. ``navs`` are after ``opening``, ascending.

    Flows dated on or before the opening date are ignored; a flow between two NAV dates counts
    on the later one. With no opening NAV the account starts from zero.
    """
    flow_days = sorted(flows)
    prev_day, prev_nav = opening or (None, 0.0)
    result = []
    for day, nav in navs:
        flow = sum(flows[d] for d in flow_days if (prev_day is None or d > prev_day) and d <= day)
        profit = nav - prev_nav - flow
        base = prev_nav + flow
        result.append(DayReturn(day, nav, flow, profit, profit / base if base > 0 else None))
        prev_day, prev_nav = day, nav
    return result


def compound(returns: Iterable[float | None]) -> float:
    growth = 1.0
    for value in returns:
        if value is not None:
            growth *= 1 + value
    return growth - 1


def xnpv(rate: float, flows: list[tuple[date, float]]) -> float:
    start = flows[0][0]
    return sum(amount / (1 + rate) ** ((day - start).days / 365) for day, amount in flows)


def xirr(flows: list[tuple[date, float]]) -> float | None:
    """Annualised money-weighted return (Excel ``XIRR``), by bisection; None when undefined."""
    flows = sorted((day, amount) for day, amount in flows if amount)
    if len(flows) < 2 or flows[-1][0] == flows[0][0]:
        return None
    if not (any(amount < 0 for _, amount in flows) and any(amount > 0 for _, amount in flows)):
        return None
    low, high = -0.999999, 1.0
    f_low = xnpv(low, flows)
    while xnpv(high, flows) * f_low > 0:
        high *= 4
        if high > 1e9:
            return None
    for _ in range(300):
        mid = (low + high) / 2
        f_mid = xnpv(mid, flows)
        if f_mid == 0 or high - low < 1e-12:
            return mid
        if f_mid * f_low > 0:
            low, f_low = mid, f_mid
        else:
            high = mid
    return (low + high) / 2


@dataclass
class RateBook:
    """Broker ``ExchangeRate`` history: 1 unit of ``currency`` = ``rate`` base, per NY date."""

    base: str
    rates: dict[str, list[tuple[date, float]]] = field(default_factory=dict)

    def rate(self, currency: str, day: date) -> tuple[float | None, bool]:
        """(rate, exact). ``exact`` is False when only a later rate exists."""
        currency = currency.strip().upper()
        if not currency or currency == self.base:
            return 1.0, True
        history = self.rates.get(currency)
        if not history:
            return None, False
        index = bisect_right([d for d, _ in history], day)
        if index:
            return history[index - 1][1], True
        return history[0][1], False


@dataclass
class Converted:
    total: float = 0.0
    approximate: int = 0
    missing: dict[str, float] = field(default_factory=lambda: defaultdict(float))


def convert(book: RateBook, items: Iterable[tuple[date, str, float, float | None]]) -> Converted:
    """Sum (date, currency, amount, own rate) in base; the row's own rate wins when known."""
    result = Converted()
    for day, currency, amount, own in items:
        if own:
            result.total += amount * own
            continue
        rate, exact = book.rate(currency, day)
        if rate is None:
            result.missing[currency or "unknown"] += amount
            continue
        result.total += amount * rate
        result.approximate += 0 if exact else 1
    return result


# ---------------------------------------------------------------------------
# Response
# ---------------------------------------------------------------------------


class NavPoint(BaseModel):
    date: date
    nav: float
    contributions: float  # opening NAV + cumulative net external flows
    twr: float  # cumulative time-weighted return since the start
    ret: float | None = None  # that day's return; None when its capital base was not positive


class MonthRow(BaseModel):
    month: str  # YYYY-MM
    start_nav: float
    end_nav: float
    flows: float
    profit: float
    twr: float


class AttributionLine(BaseModel):
    key: str
    label: str
    amount: float
    note: str = ""


class Performance(BaseModel):
    currency: str | None
    since: date
    start: date | None  # opening NAV date (the close before ``since``), None if none
    end: date | None  # last NAV date
    opening_nav: float | None
    closing_nav: float | None
    deposits: float
    withdrawals: float
    net_contributions: float
    net_profit: float | None
    return_on_capital: float | None
    twr: float | None
    # IBKR's own daily TWR chained over the same days (from ChangeInNAV), for reconciliation.
    ibkr_twr: float | None = None
    xirr: float | None
    # Interest charged or credited plus the change in interest accruals (negative = cost).
    financing_cost: float | None = None
    attribution: list[AttributionLine]
    # "ibkr": IBKR's daily change-in-NAV lines; "cash": cash records with a residual.
    attribution_source: Literal["ibkr", "cash"] = "cash"
    nav: list[NavPoint]
    monthly: list[MonthRow]
    warnings: list[str]
    basis: list[str]
    # Statistical risk over the period's daily returns (see portfolio_analytics).
    risk: RiskStats | None = None
    # Benchmarks in the base currency on the same dates; the last is risk-matched when beta is known.
    benchmarks: list[Benchmark] = []


BASIS = [
    (
        "NAV is the broker's account-wide net liquidation in the base currency, one value per New York "
        "date, preferring the Flex statement close over Gateway snapshots."
    ),
    "Net profit = closing NAV - opening NAV - net deposits. Margin borrowing is already inside NAV.",
    "Return on capital = net profit / (opening NAV + net deposits): cumulative, not annualised.",
    "TWR chains daily returns with each day's deposits treated as arriving at the start of the day.",
    "XIRR is annualised, discounting by days / 365 like Excel's XIRR.",
    (
        "Attribution is IBKR's own daily change in NAV (mark-to-market, dividends, interest and "
        "accrual changes, commissions, taxes, FX translation) when the Flex statements cover "
        "every day; otherwise investment gains are the residual after the separately booked "
        "cash lines and include FX translation and accrual changes."
    ),
    "Financing cost = interest charged or credited plus the change in interest accruals.",
]


def empty(currency: str | None, since: date, warnings: list[str]) -> Performance:
    return Performance(
        currency=currency,
        since=since,
        start=None,
        end=None,
        opening_nav=None,
        closing_nav=None,
        deposits=0,
        withdrawals=0,
        net_contributions=0,
        net_profit=None,
        return_on_capital=None,
        twr=None,
        xirr=None,
        attribution=[],
        nav=[],
        monthly=[],
        warnings=warnings,
        basis=BASIS,
    )


def build(
    *,
    currency: str,
    since: date,
    nav_rows: list[tuple[date, float]],
    flow_rows: list[tuple[date, str, float, float | None]],
    cash_lines: dict[str, Converted],
    book: RateBook,
    ibkr_days: dict[date, dict[str, float]] | None = None,
) -> Performance:
    """Assemble the report from a NAV series, external flows (date, currency, signed amount,
    own rate) and the base-currency totals of each separately booked cash line.

    When IBKR's daily change in NAV covers every NAV date in the period, the attribution is
    IBKR's own lines; otherwise it is built from cash records with a residual."""
    warnings: list[str] = []
    nav_rows = sorted(nav_rows)
    before = [row for row in nav_rows if row[0] < since]
    opening = before[-1] if before else None
    navs = [row for row in nav_rows if row[0] >= since]
    if not navs:
        return empty(currency, since, ["No net liquidation is recorded for this period."])
    end = navs[-1][0]
    if opening is None:
        first_day = navs[0][0]
        funded = any(since <= day <= first_day for day, _, amount, _ in flow_rows if amount > 0)
        if funded:
            warnings.append(
                f"No net liquidation before {since.isoformat()}, so the account is treated as "
                "starting from zero."
            )
        else:
            # History starts with money already in the account: measure from that first close
            # rather than count the opening balance as profit.
            opening, navs = navs[0], navs[1:]
            warnings.append(
                f"Net liquidation history starts on {first_day.isoformat()}; the period starts there."
            )
            if not navs:
                return empty(currency, since, ["Only one net liquidation is recorded so far."])

    flows: dict[date, float] = defaultdict(float)
    deposits = withdrawals = 0.0
    approximate = 0
    missing: dict[str, float] = defaultdict(float)
    late = 0
    for day, ccy, amount, own in flow_rows:
        if (opening and day <= opening[0]) or (not opening and day < since):
            continue
        if day > end:
            late += 1
            continue
        converted = convert(book, [(day, ccy, amount, own)])
        if converted.missing:
            for key, value in converted.missing.items():
                missing[key] += value
            continue
        approximate += converted.approximate
        flows[day] += converted.total
        if converted.total >= 0:
            deposits += converted.total
        else:
            withdrawals -= converted.total
    if missing:
        warnings.append(
            "Deposits or withdrawals with no rate to "
            f"{currency} were left out: "
            + ", ".join(f"{amount:,.2f} {ccy}" for ccy, amount in sorted(missing.items()))
            + ". Net profit and returns are unreliable until they convert."
        )
    if approximate:
        warnings.append(
            f"{approximate} external flow(s) converted at a later broker rate (no earlier one)."
        )
    if late:
        warnings.append(
            f"{late} external flow(s) after the last NAV date ({end.isoformat()}) wait for the next NAV."
        )

    days = daily_returns(opening, navs, flows)
    skipped = [day for day in days if day.ret is None]
    if skipped:
        warnings.append(
            f"{len(skipped)} day(s) had no positive capital base and are left out of TWR."
        )
    opening_nav = opening[1] if opening else 0.0
    closing_nav = navs[-1][1]
    net_contributions = deposits - withdrawals
    net_profit = closing_nav - opening_nav - net_contributions
    capital = opening_nav + net_contributions
    start_day = opening[0] if opening else since

    nav_points, cumulative, growth = [], opening_nav, 1.0
    for day in days:
        cumulative += day.flow
        if day.ret is not None:
            growth *= 1 + day.ret
        nav_points.append(
            NavPoint(
                date=day.day, nav=day.nav, contributions=cumulative, twr=growth - 1, ret=day.ret
            )
        )

    monthly: list[MonthRow] = []
    previous = opening_nav
    for day in days:
        key = day.day.strftime("%Y-%m")
        if not monthly or monthly[-1].month != key:
            monthly.append(
                MonthRow(month=key, start_nav=previous, end_nav=previous, flows=0, profit=0, twr=0)
            )
        row = monthly[-1]
        row.end_nav, row.flows, row.profit = day.nav, row.flows + day.flow, row.profit + day.profit
        row.twr = (1 + row.twr) * (1 + (day.ret or 0)) - 1
        previous = day.nav

    cash_flows_for_irr = [(start_day, -opening_nav)] if opening_nav else []
    cash_flows_for_irr += [(day, -amount) for day, amount in flows.items()]
    cash_flows_for_irr.append((end, closing_nav))
    twr = compound(day.ret for day in days) if days else None

    def result(
        attribution: list[AttributionLine],
        source: Literal["ibkr", "cash"],
        ibkr_twr: float | None,
        financing: float,
    ) -> Performance:
        return Performance(
            currency=currency,
            since=since,
            start=opening[0] if opening else None,
            end=end,
            opening_nav=opening_nav,
            closing_nav=closing_nav,
            deposits=deposits,
            withdrawals=withdrawals,
            net_contributions=net_contributions,
            net_profit=net_profit,
            return_on_capital=net_profit / capital if capital > 0 else None,
            twr=twr,
            ibkr_twr=ibkr_twr,
            xirr=xirr(cash_flows_for_irr),
            financing_cost=financing,
            attribution=attribution,
            attribution_source=source,
            nav=nav_points,
            monthly=monthly,
            warnings=warnings,
            basis=BASIS,
        )

    in_period = {
        day: row
        for day, row in (ibkr_days or {}).items()
        if (day > opening[0] if opening else day >= since) and day <= end
    }
    last_statement = max(in_period, default=None)
    uncovered = [
        day.day
        for day in days
        if day.day not in in_period and last_statement is not None and day.day <= last_statement
    ]
    if in_period and not uncovered:
        pending = last_statement if last_statement != end else None
        attribution, ibkr_twr, financing = ibkr_attribution(
            [in_period[day] for day in sorted(in_period)], net_profit, pending_after=pending
        )
        ibkr_deposits = sum(row.get("depositsWithdrawals", 0.0) for row in in_period.values())
        if abs(ibkr_deposits - net_contributions) >= 1:
            warnings.append(
                f"IBKR statements show {ibkr_deposits:,.2f} {currency} of net deposits, but the "
                f"cash records give {net_contributions:,.2f}."
            )
        return result(attribution, "ibkr", ibkr_twr, financing)
    if in_period:
        warnings.append(
            f"IBKR change-in-NAV is missing for {len(uncovered)} NAV date(s) (first "
            f"{uncovered[0].isoformat()}); attribution is from cash records instead."
        )
    attribution: list[AttributionLine] = []
    booked = 0.0
    for key, label, note in CASH_LINES:
        line = cash_lines.get(key)
        if line is None:
            continue
        if line.missing:
            warnings.append(
                f"{label}: no rate to {currency} for "
                + ", ".join(f"{amount:,.2f} {ccy}" for ccy, amount in sorted(line.missing.items()))
                + "; left out of the attribution."
            )
        if line.total or key in ALWAYS_SHOWN:
            attribution.append(AttributionLine(key=key, label=label, amount=line.total, note=note))
            booked += line.total
    attribution.insert(
        0,
        AttributionLine(
            key="investment",
            label="Investment gains",
            amount=net_profit - booked,
            note="Residual: price moves, FX translation and accrual changes",
        ),
    )
    attribution.append(AttributionLine(key="net_profit", label="Net profit", amount=net_profit))
    paid, received = cash_lines.get("interest_paid"), cash_lines.get("interest_received")
    financing = (paid.total if paid else 0.0) + (received.total if received else 0.0)
    return result(attribution, "cash", None, financing)


# IBKR ChangeInNAV fields per attribution line (base currency), in waterfall order.
IBKR_LINES: list[tuple[str, str, tuple[str, ...], str]] = [
    ("investment", "Investment gains", ("mtm", "mtmAtPaxos"), "Mark-to-market on positions"),
    ("dividends", "Dividends", ("dividends",), ""),
    (
        "withholding",
        "Withholding tax",
        ("withholdingTax", "withholding871m", "withholdingTaxCollected"),
        "",
    ),
    ("dividend_accruals", "Dividend accrual change", ("changeInDividendAccruals",), ""),
    ("interest", "Interest", ("interest",), "Margin interest charged or credited"),
    (
        "interest_accruals",
        "Interest accrual change",
        ("changeInInterestAccruals",),
        "Interest accrued, not yet charged",
    ),
    ("commissions", "Commissions", ("commissions", "forexCommissions", "commissionsAtPaxos"), ""),
    (
        "fees",
        "Transaction fees",
        # IBKR's ``transactionTax`` is the statement's "Transaction Fees" (exchange and
        # regulatory fees on trades).
        (
            "transactionTax",
            "brokerFees",
            "changeInBrokerFeeAccruals",
            "advisorFees",
            "clientFees",
            "otherFees",
            "referralFee",
        ),
        "Exchange, regulatory and account fees",
    ),
    ("taxes", "Sales tax", ("salesTax", "billableSalesTax"), "GST on commissions and fees"),
    (
        "fx",
        "FX translation",
        ("fxTranslation", "netFxTrading"),
        "Revaluing non-base cash and positions",
    ),
]
# Realized + change in unrealized: IBKR's alternative to MTM; used only when MTM is absent.
IBKR_PNL_FIELDS = ("realized", "changeInUnrealized")
IBKR_FIELDS = sorted(
    {name for _, _, names, _ in IBKR_LINES for name in names}
    | set(IBKR_PNL_FIELDS)
    | {"startingValue", "endingValue", "depositsWithdrawals", "twr"}
)


def ibkr_attribution(
    days: list[dict[str, float]], net_profit: float, pending_after: date | None = None
) -> tuple[list[AttributionLine], float, float]:
    """IBKR's own change-in-NAV lines summed over ``days`` (one row per day).

    Returns the lines (ending with net profit), the chained IBKR TWR and the financing cost
    (interest plus the change in interest accruals). ``Other`` holds what IBKR books outside
    the listed lines (cost adjustments, corporate actions...); ``Unreconciled`` is any gap
    between IBKR's daily profit and the NAV-based net profit (flows outside the statements).
    With ``pending_after`` (NAV dates after the last statement) that gap is labelled as
    profit since the last statement instead.
    """
    totals: dict[str, float] = defaultdict(float)
    ibkr_profit = 0.0
    growth = 1.0
    for row in days:

        def get(name: str, row: dict[str, float] = row) -> float:
            return row.get(name, 0.0)

        ibkr_profit += get("endingValue") - get("startingValue") - get("depositsWithdrawals")
        growth *= 1 + get("twr") / 100
        mtm = get("mtm") + get("mtmAtPaxos")
        for key, _, names, _ in IBKR_LINES:
            if key == "investment" and not mtm:
                totals[key] += sum(get(name) for name in IBKR_PNL_FIELDS)
            else:
                totals[key] += sum(get(name) for name in names)
    lines = [
        AttributionLine(key=key, label=label, amount=totals[key], note=note)
        for key, label, _, note in IBKR_LINES
        if totals[key] or key in ("investment", "interest", "commissions")
    ]
    other = ibkr_profit - sum(totals.values())
    if abs(other) >= 0.005:
        lines.append(
            AttributionLine(
                key="other",
                label="Other",
                amount=other,
                note="Booked by IBKR outside the lines above",
            )
        )
    gap = net_profit - ibkr_profit
    if abs(gap) >= 0.005:
        lines.append(
            AttributionLine(
                key="pending",
                label="Since the last statement",
                amount=gap,
                note=f"NAV change after {pending_after.isoformat()}, not yet in a Flex statement",
            )
            if pending_after
            else AttributionLine(
                key="unreconciled",
                label="Unreconciled",
                amount=gap,
                note="NAV-based profit not explained by IBKR's daily statements",
            )
        )
    lines.append(AttributionLine(key="net_profit", label="Net profit", amount=net_profit))
    return lines, growth - 1, totals["interest"] + totals["interest_accruals"]


# key, label, note — in waterfall order after investment gains.
CASH_LINES: list[tuple[str, str, str]] = [
    ("dividends", "Dividends", "Including payments in lieu"),
    ("withholding", "Withholding tax", ""),
    ("interest_received", "Interest received", ""),
    ("interest_paid", "Interest paid", "Margin financing; accrual changes sit in investment gains"),
    ("commissions", "Commissions", "Trade and FX-conversion commissions"),
    ("fees", "Fees", "Account fees and commission adjustments"),
    ("taxes", "Transaction taxes", "GST, stamp duty and similar on trades"),
    ("other", "Other cash items", "Cash transactions the broker does not classify"),
]
ALWAYS_SHOWN = frozenset({"interest_paid", "commissions"})


# ---------------------------------------------------------------------------
# Data access
# ---------------------------------------------------------------------------


def _day(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _flex_time(value: str) -> date | None:
    digits = "".join(ch for ch in value or "" if ch.isdigit())
    if len(digits) < 8:
        return None
    try:
        return date(int(digits[:4]), int(digits[4:6]), int(digits[6:8]))
    except ValueError:
        return None


class PerformanceSource:
    def __init__(self, repo: Any, account: dict[str, Any]) -> None:
        self.repo = repo
        self.params = {"broker": account["broker_code"], "account": account["account_id"]}

    def navs(self, currency: str) -> list[tuple[date, float]]:
        rows = self.repo.query(
            "SELECT toDate(snapshot_time, 'America/New_York') AS d, "
            "argMax(value_num, (endsWith(source_channel, 'flex_statement'), snapshot_time)) AS nav, "
            "max(endsWith(source_channel, 'flex_statement')) AS flex "
            "FROM broker.account_state_snapshot FINAL "
            "WHERE broker_code = {broker:String} AND account_id = {account:String} "
            "AND (metric_canonical = 'net_liquidation' OR metric = 'NetLiquidation') "
            "AND segment = '' AND currency = {currency:String} AND value_num IS NOT NULL "
            "GROUP BY d ORDER BY d",
            {**self.params, "currency": currency},
        )
        days = [(_day(row["d"]), to_float(row["nav"]), bool(row["flex"])) for row in rows]
        last_flex = max((day for day, _, flex in days if flex), default=None)
        # Statement closes are authoritative; a Gateway reading (often a weekend or intraday one)
        # only extends the series past the last statement.
        return [
            (day, value)
            for day, value, flex in days
            if value is not None and (flex or last_flex is None or day > last_flex)
        ]

    def rate_book(self, currency: str, currencies: set[str]) -> RateBook:
        """Daily rates to ``currency``: Flex ``ConversionRates`` per report date, else the
        Gateway ``ExchangeRate`` for that New York date."""
        base = currency.upper()
        wanted = sorted(ccy for ccy in currencies if ccy and ccy != base)
        daily: dict[str, dict[date, float]] = defaultdict(dict)
        if not wanted:
            return RateBook(base=base)
        statement = self.repo.query(
            "SELECT attributes['reportDate'] AS d, attributes['fromCurrency'] AS currency, "
            "any(attributes['rate']) AS rate FROM broker.statement_rows FINAL "
            "WHERE broker_code = {broker:String} AND account_id = {account:String} "
            "AND section = 'ConversionRates' AND attributes['toCurrency'] = {base:String} "
            "AND attributes['fromCurrency'] IN {wanted:Array(String)} "
            "GROUP BY d, currency",
            {**self.params, "base": base, "wanted": wanted},
        )
        rows = self.repo.query(
            "SELECT toDate(snapshot_time, 'America/New_York') AS d, currency, "
            "argMax(value_num, snapshot_time) AS rate FROM broker.account_state_snapshot FINAL "
            "WHERE broker_code = {broker:String} AND account_id = {account:String} "
            "AND metric = 'ExchangeRate' AND segment = '' AND value_num > 0 "
            "GROUP BY d, currency ORDER BY currency, d",
            self.params,
        )
        for row in rows:
            rate = to_float(row["rate"])
            if rate:
                daily[text(row["currency"]).upper()][_day(row["d"])] = rate
        for row in statement:  # the statement's rate wins on its own date
            day, rate = _flex_time(str(row["d"])), to_float(row["rate"])
            if day and rate and rate > 0:
                daily[text(row["currency"]).upper()][day] = rate
        return RateBook(
            base=base,
            rates={ccy: sorted(days.items()) for ccy, days in daily.items() if ccy in wanted},
        )

    def change_in_nav(self, currency: str) -> dict[date, dict[str, float]]:
        """IBKR's single-day ``ChangeInNAV`` rows (base currency), latest statement per day."""
        fields = ", ".join(
            f"argMax(attributes['{name}'], generated_at) AS {name}" for name in IBKR_FIELDS
        )
        rows = self.repo.query(
            f"SELECT attributes['toDate'] AS d, {fields} FROM broker.statement_rows FINAL "
            "WHERE broker_code = {broker:String} AND account_id = {account:String} "
            "AND section = 'ChangeInNAV' AND attributes['fromDate'] = attributes['toDate'] "
            "AND attributes['currency'] = {currency:String} GROUP BY d",
            {**self.params, "currency": currency},
        )
        result: dict[date, dict[str, float]] = {}
        for row in rows:
            day = _flex_time(str(row["d"]))
            if day:
                result[day] = {
                    name: value
                    for name in IBKR_FIELDS
                    if (value := to_float(row.get(name))) is not None
                }
        return result

    def own_rates(self) -> dict[tuple[date, str, str], float]:
        """``fxRateToBase`` of Flex cash transactions, keyed by (date, currency, amount)."""
        rows = self.repo.query(
            "SELECT attributes['dateTime'] AS at, currency, attributes['amount'] AS amount, "
            "any(attributes['fxRateToBase']) AS rate FROM broker.statement_rows FINAL "
            "WHERE broker_code = {broker:String} AND account_id = {account:String} "
            "AND section = 'CashTransactions' AND attributes['fxRateToBase'] != '' "
            "GROUP BY at, currency, amount",
            self.params,
        )
        result = {}
        for row in rows:
            day, rate = _flex_time(str(row["at"])), to_float(row["rate"])
            amount = to_float(row["amount"])
            if day and rate and amount is not None:
                result[(day, text(row["currency"]).upper(), f"{amount:.6f}")] = rate
        return result

    def cash_flows(self) -> list[dict[str, Any]]:
        return self.repo.query(
            "SELECT event_time, cash_flow_type, amount, currency FROM broker.cash_flows FINAL "
            "WHERE broker_code = {broker:String} AND account_id = {account:String}",
            self.params,
        )

    def commissions(self) -> list[dict[str, Any]]:
        return self.repo.query(
            "SELECT exec_time, commission, commission_ccy FROM broker.executions FINAL "
            "WHERE broker_code = {broker:String} AND account_id = {account:String} "
            "AND commission IS NOT NULL AND commission != 0",
            self.params,
        )

    def taxes(self) -> list[dict[str, Any]]:
        return self.repo.query(
            "SELECT attributes['ibExecID'] AS exec_id, any(attributes['dateTime']) AS at, "
            "any(currency) AS currency, any(attributes['taxes']) AS taxes "
            "FROM broker.statement_rows FINAL WHERE broker_code = {broker:String} "
            "AND account_id = {account:String} AND section = 'Trades' "
            "AND level_of_detail = 'EXECUTION' AND attributes['taxes'] NOT IN ('', '0') "
            "GROUP BY exec_id",
            self.params,
        )


CASH_FLOW_LINE = {
    "dividend": "dividends",
    "tax_withholding": "withholding",
    "fee": "fees",
    "commission": "fees",
    "other": "other",
    "corporate_action_cash": "other",
}


def performance_report(repo: Any, account: dict[str, Any], since: date) -> Performance:
    currency = text(account.get("base_currency")).upper()
    if not currency:
        return empty(None, since, ["No base currency is registered for this account."])
    source = PerformanceSource(repo, account)
    navs = source.navs(currency)
    if not navs:
        return empty(currency, since, ["No net liquidation is recorded for this account."])
    own = source.own_rates()
    flow_rows, commission_rows, tax_rows = source.cash_flows(), source.commissions(), source.taxes()
    currencies = {text(row["currency"]).upper() for row in [*flow_rows, *tax_rows]}
    currencies |= {text(row["commission_ccy"]).upper() for row in commission_rows}
    book = source.rate_book(currency, currencies | {"USD"})
    end = navs[-1][0]
    opening = max((day for day, _ in navs if day < since), default=None)

    def in_period(day: date) -> bool:
        return (day > opening if opening else day >= since) and day <= end

    externals: list[tuple[date, str, float, float | None]] = []
    lines: dict[str, list[tuple[date, str, float, float | None]]] = defaultdict(list)
    for row in flow_rows:
        amount = to_float(row["amount"])
        if amount is None:
            continue
        day = ny_date(row["event_time"])
        ccy = text(row["currency"]).upper()
        rate = own.get((day, ccy, f"{amount:.6f}"))
        kind = text(row["cash_flow_type"])
        if kind in EXTERNAL_TYPES:
            externals.append((day, ccy, amount, rate))
        elif in_period(day):
            if kind == "interest":
                key = "interest_received" if amount >= 0 else "interest_paid"
            else:
                key = CASH_FLOW_LINE.get(kind)
            if key:  # fx_conversion and sweeps move cash between currencies, not wealth
                lines[key].append((day, ccy, amount, rate))
    for row in commission_rows:
        amount, day = to_float(row["commission"]), ny_date(row["exec_time"])
        if amount and in_period(day):
            # Fill commissions are positive costs.
            lines["commissions"].append((day, text(row["commission_ccy"]), -abs(amount), None))
    for row in tax_rows:
        amount, day = to_float(row["taxes"]), _flex_time(str(row["at"]))
        if amount and day and in_period(day):
            # Flex reports taxes as signed cash (a cost is negative).
            lines["taxes"].append((day, text(row["currency"]), amount, None))

    cash_lines = {key: convert(book, items) for key, items in lines.items()}
    cash_lines.setdefault("interest_paid", Converted())
    cash_lines.setdefault("commissions", Converted())
    report = build(
        currency=currency,
        since=since,
        nav_rows=navs,
        flow_rows=externals,
        cash_lines=cash_lines,
        book=book,
        ibkr_days=source.change_in_nav(currency),
    )
    if not report.nav:
        return report
    start = report.start or report.since
    closes: dict[str, list[tuple[date, float]]] = {}
    try:
        found = IndexRepository(repo.client).tracked(
            [code for code, _, _ in BENCHMARKS], start - timedelta(days=10), report.end or start
        )
        closes = {code: [(p.trade_date, p.value) for p in points] for code, points in found.items()}
    except Exception:  # noqa: BLE001 - a benchmark read never fails the report
        report.warnings.append("Benchmark prices are unavailable right now.")
    return add_analytics(report, closes, lambda day: book.rate("USD", day)[0], start)


# Benchmarks with daily closes in market.bars (Schwab, USD). Price series: no dividends.
BENCHMARKS: list[tuple[str, str, str]] = [
    ("$SPX", "S&P 500", "Price index; excludes dividends"),
    ("IWV", "Russell 3000 (IWV)", "ETF price; excludes dividends"),
    ("RSP", "S&P 500 equal weight (RSP)", "ETF price; excludes dividends"),
]


def add_analytics(
    report: Performance,
    closes: dict[str, list[tuple[date, float]]],
    usd_rate: Callable[[date], float | None],
    start: date,
) -> Performance:
    """Add statistical risk and benchmarks (USD closes converted with ``usd_rate``)."""
    days = [point.date for point in report.nav]
    benchmarks: list[Benchmark] = []
    primary: list[float | None] | None = None
    primary_label: str | None = None
    for code, label, note in BENCHMARKS:
        series = closes.get(code) or []
        if not series:
            continue
        values = benchmark_values(days, series, usd_rate)
        base = benchmark_values([start], series, usd_rate)[0]
        points = rebased(days, values, base)
        if not points:
            continue
        twr = points[-1].value
        excess = report.twr - twr if report.twr is not None else None
        benchmarks.append(
            Benchmark(code=code, label=label, note=note, twr=twr, excess=excess, points=points)
        )
        if primary is None:
            primary, primary_label = daily_changes(values, base), label
    risk = risk_stats(
        [(point.date, point.twr, point.ret) for point in report.nav],
        report.closing_nav,
        primary,
        primary_label,
    )
    if risk.beta is not None and primary is not None:
        beta = risk.beta
        points = compound_series(days, [None if r is None else beta * r for r in primary])
        twr = points[-1].value if points else None
        benchmarks.append(
            Benchmark(
                code="risk_matched",
                label=f"{primary_label} x beta {beta:.2f}",
                note="The benchmark's daily moves scaled by the account's measured beta",
                twr=twr,
                excess=report.twr - twr if report.twr is not None and twr is not None else None,
                points=points,
            )
        )
    elif not benchmarks:
        report.warnings.append("No benchmark has prices for this period.")
    return report.model_copy(update={"risk": risk, "benchmarks": benchmarks})


@router.get("/accounts/{broker_code}/{account_id}/performance", response_model=Performance)
def performance(
    repo: Repo,
    broker_code: str,
    account_id: str,
    user_id: ProfileFilter = "",
    since: Annotated[date | None, Query()] = None,
) -> Performance:
    """Performance from ``since`` (default 1 January this year, New York) to the last NAV date."""
    account = repo.account(broker_code, account_id, validate_profile_filter(user_id))
    start = since or date(datetime.now(NEW_YORK).year, 1, 1)
    if start > datetime.now(NEW_YORK).date():
        raise HTTPException(422, "since must not be in the future")
    return performance_report(repo, account, start)
