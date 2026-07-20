"""Unit tests for the EDGAR revenue-growth and Form 4 parsing logic.

The revenue tests are regression tests for real bugs found in production on
the 2026-07-19 shortlist: SKIL scored 100 on "growth" computed from two SPAC
stub periods in a 2022 filing, AVTX from 2016-vs-2017 data on a long-stale
"Revenues" tag, and ZBIO from a $5M->$10M milestone-payment base.
"""

from datetime import date, timedelta

from gempicker.data_sources.sec_edgar import _parse_form4, get_revenue_growth

TODAY = date.today()


def entry(start: date, end: date, val: float, filed: date | None = None, form: str = "10-K", fp: str = "FY") -> dict:
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "val": val,
        "filed": (filed or end + timedelta(days=75)).isoformat(),
        "form": form,
        "fp": fp,
    }


def facts_with(**tags: list[dict]) -> dict:
    return {
        "facts": {
            "us-gaap": {
                tag: {"units": {"USD": units}} for tag, units in tags.items()
            }
        }
    }


def annual(end: date, val: float, **kwargs) -> dict:
    return entry(end - timedelta(days=364), end, val, **kwargs)


# two consecutive fiscal years, the latest ending recently enough to be fresh
LATEST_END = TODAY - timedelta(days=200)
PRIOR_END = LATEST_END - timedelta(days=365)


def test_happy_path_two_consecutive_annual_periods():
    facts = facts_with(Revenues=[annual(PRIOR_END, 100_000_000), annual(LATEST_END, 130_000_000)])
    assert get_revenue_growth(facts) == 30.0


def test_stub_periods_are_not_annual_revenue():
    """SKIL scenario: a SPAC-transition 10-K tags 4-7 month predecessor/
    successor stubs as form=10-K/fp=FY. Comparing stubs produced a fake
    +206% for a company whose revenue was actually shrinking."""
    fy_end = TODAY - timedelta(days=1_600)
    facts = facts_with(
        Revenues=[
            annual(fy_end, 514_000_000),
            # merger-year stubs, exactly the shape in SKIL's FY2022 10-K
            entry(fy_end + timedelta(days=1), fy_end + timedelta(days=131), 139_000_000),
            entry(fy_end + timedelta(days=132), fy_end + timedelta(days=365), 427_000_000),
        ]
    )
    assert get_revenue_growth(facts) is None  # one real annual period isn't a trend


def test_stale_tag_returns_none():
    """AVTX scenario: the legacy Revenues tag stopped updating years ago;
    computing "growth" from it scored a 2018-era comparison as current."""
    old_end = TODAY - timedelta(days=3 * 365)
    facts = facts_with(Revenues=[annual(old_end - timedelta(days=365), 1_100_000), annual(old_end, 27_800_000)])
    assert get_revenue_growth(facts) is None


def test_prefers_tag_with_freshest_data():
    """Filers that moved to ASC 606 keep stale history under Revenues; the
    tag with the most recent annual period must win regardless of order."""
    stale_end = TODAY - timedelta(days=3 * 365)
    facts = facts_with(
        Revenues=[annual(stale_end - timedelta(days=365), 400_000_000), annual(stale_end, 500_000_000)],
        RevenueFromContractWithCustomerExcludingAssessedTax=[
            annual(PRIOR_END, 100_000_000),
            annual(LATEST_END, 90_000_000),
        ],
    )
    assert get_revenue_growth(facts) == -10.0


def test_tiny_prior_year_base_returns_none():
    """ZBIO scenario: $5M -> $10M of milestone revenue is not "100% growth"
    in any sense a 30%-weight fundamentals signal should reward."""
    facts = facts_with(Revenues=[annual(PRIOR_END, 5_000_000), annual(LATEST_END, 10_000_000)])
    assert get_revenue_growth(facts) is None


def test_non_consecutive_periods_return_none():
    gap_prior = LATEST_END - timedelta(days=2 * 365)  # a missing year in between
    facts = facts_with(Revenues=[annual(gap_prior, 100_000_000), annual(LATEST_END, 150_000_000)])
    assert get_revenue_growth(facts) is None


def test_restated_period_uses_latest_filing():
    facts = facts_with(
        Revenues=[
            annual(PRIOR_END, 100_000_000),
            annual(LATEST_END, 110_000_000, filed=LATEST_END + timedelta(days=75)),
            # same period restated in a later filing
            annual(LATEST_END, 120_000_000, filed=LATEST_END + timedelta(days=150)),
        ]
    )
    assert get_revenue_growth(facts) == 20.0


FORM4_XML = """<?xml version="1.0"?>
<ownershipDocument>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <transactionCoding><transactionCode>P</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>1000</value></transactionShares>
        <transactionPricePerShare><value>10.50</value></transactionPricePerShare>
      </transactionAmounts>
    </nonDerivativeTransaction>
    <nonDerivativeTransaction>
      <transactionCoding><transactionCode>S</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>200</value></transactionShares>
        <transactionPricePerShare><value>12.00</value></transactionPricePerShare>
      </transactionAmounts>
    </nonDerivativeTransaction>
    <nonDerivativeTransaction>
      <transactionCoding><transactionCode>A</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>5000</value></transactionShares>
        <transactionPricePerShare><value>0</value></transactionPricePerShare>
      </transactionAmounts>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
</ownershipDocument>
"""


def test_parse_form4_separates_open_market_buys_and_sells():
    summary = _parse_form4(FORM4_XML)
    assert summary["buy_usd"] == 10_500.0
    assert summary["sell_usd"] == 2_400.0
    assert summary["buy_count"] == 1
    assert summary["sell_count"] == 1  # the grant (code A) is ignored
