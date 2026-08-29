"""The Alpaca adapter, exercised against recorded-shape responses.

The live API is unreachable from this environment, so correctness is established
where it can be: the request the adapter builds, the pagination it performs, the
parsing of each documented response shape, the mapping of HTTP errors onto
actionable messages, and -- most importantly -- that the IEX feed is never
described as full-market data.
"""

from __future__ import annotations

import datetime as dt

import pytest

from tradingbrain.data.providers.alpaca import (AlpacaProvider, _normalise_corporate_action,
                                                _parse_occ)
from tradingbrain.data.providers.base import ProviderUnavailable
from tradingbrain.data.types import Timeframe


class FakeAlpaca(AlpacaProvider):
    """Replaces the HTTP layer with a scripted one, recording every request."""

    def __init__(self, pages, **kw):
        super().__init__(api_key="k", api_secret="s", **kw)
        self.pages = list(pages)
        self.requests: list[tuple[str, dict]] = []

    def _get(self, url, params=None, endpoint=""):
        self.requests.append((url, dict(params or {})))
        if not self.pages:
            raise AssertionError("more requests than scripted responses")
        return self.pages.pop(0)


def _bar(day: str, o=100.0, h=101.0, l=99.0, c=100.5, v=1e6):
    return {"t": f"{day}T05:00:00Z", "o": o, "h": h, "l": l, "c": c, "v": v, "n": 10,
            "vw": 100.2}


# ------------------------------------------------------------------ feed --

def test_iex_is_never_described_as_full_market():
    p = AlpacaProvider("k", "s", feed="iex")
    assert p.feed_label() == "IEX"
    assert p.is_full_market() is False
    d = p.feed_description().lower()
    assert "not full-market" in d
    assert "single exchange" in d


def test_sip_is_labelled_consolidated():
    p = AlpacaProvider("k", "s", feed="sip")
    assert p.is_full_market() is True
    assert "consolidated" in p.feed_description().lower()


def test_describe_exposes_the_feed_and_credential_presence_but_not_the_secret():
    p = AlpacaProvider("supersecretkey", "supersecretsecret", feed="iex")
    d = p.describe()
    assert d["credentials_present"] is True
    assert d["full_market_coverage"] is False
    assert "supersecret" not in str(d)


def test_missing_credentials_is_reported_not_raised():
    ok, why = AlpacaProvider(None, None).available()
    assert ok is False
    assert "ALPACA_API_KEY" in why


# ------------------------------------------------------------------ bars --

def test_daily_bars_are_parsed_and_sorted():
    p = FakeAlpaca([{"bars": {"AAPL": [_bar("2024-01-03", c=101.0),
                                       _bar("2024-01-02", c=100.0)]},
                     "next_page_token": None}])
    s = p.daily("AAPL")
    assert len(s) == 2
    assert s.ts[0] < s.ts[1], "bars must come back in chronological order"
    assert s.close[-1] == 101.0
    assert s.adjusted is True
    assert s.origin.value == "REAL"


def test_bars_request_carries_feed_adjustment_and_timeframe():
    p = FakeAlpaca([{"bars": {"AAPL": [_bar("2024-01-02")]}, "next_page_token": None}],
                   feed="iex")
    p.daily("AAPL", dt.date(2024, 1, 1), dt.date(2024, 1, 31))
    _, params = p.requests[0]
    assert params["timeframe"] == "1Day"
    assert params["feed"] == "iex"
    assert params["adjustment"] == "all"
    assert params["sort"] == "asc"
    assert params["start"].startswith("2024-01-01")
    assert params["end"].startswith("2024-01-31")


def test_pagination_follows_the_page_token():
    p = FakeAlpaca([
        {"bars": {"AAPL": [_bar("2024-01-02")]}, "next_page_token": "tok1"},
        {"bars": {"AAPL": [_bar("2024-01-03")]}, "next_page_token": "tok2"},
        {"bars": {"AAPL": [_bar("2024-01-04")]}, "next_page_token": None},
    ])
    s = p.daily("AAPL")
    assert len(s) == 3
    assert len(p.requests) == 3
    assert p.requests[1][1]["page_token"] == "tok1"
    assert p.requests[2][1]["page_token"] == "tok2"


def test_symbols_are_batched_into_one_request():
    syms = [f"S{i:03d}" for i in range(100)]
    p = FakeAlpaca([{"bars": {s: [_bar("2024-01-02")] for s in syms},
                     "next_page_token": None}])
    out = p.bars_multi(syms, Timeframe.D1)
    assert len(p.requests) == 1, "100 symbols must cost one request, not 100"
    assert len(out) == 100
    assert p.requests[0][1]["symbols"].count(",") == 99


def test_batches_split_above_the_symbol_limit():
    syms = [f"S{i:03d}" for i in range(150)]
    p = FakeAlpaca([{"bars": {}, "next_page_token": None},
                    {"bars": {}, "next_page_token": None}])
    p.bars_multi(syms, Timeframe.D1)
    assert len(p.requests) == 2


def test_intraday_maps_the_timeframe():
    p = FakeAlpaca([{"bars": {"AAPL": [_bar("2024-01-02")]}, "next_page_token": None}])
    p.intraday("AAPL", Timeframe.M5)
    assert p.requests[0][1]["timeframe"] == "5Min"


def test_empty_response_is_an_explicit_failure_not_an_empty_series():
    p = FakeAlpaca([{"bars": {}, "next_page_token": None}])
    with pytest.raises(ProviderUnavailable) as e:
        p.daily("AAPL")
    assert "no daily bars" in str(e.value)


def test_incomplete_candles_are_skipped():
    p = FakeAlpaca([{"bars": {"AAPL": [_bar("2024-01-02"),
                                       {"t": "2024-01-03T05:00:00Z", "o": 1, "h": 2,
                                        "l": 0, "c": None, "v": 5}]},
                     "next_page_token": None}])
    assert len(p.daily("AAPL")) == 1


# ----------------------------------------------------------------- quote --

def test_latest_quote_carries_the_feed_caveat():
    p = FakeAlpaca([{"quote": {"bp": 10.0, "bs": 1, "ap": 10.1, "as": 2,
                               "t": "2024-01-02T15:00:00Z"}}])
    q = p.quote("AAPL")
    assert q["bid"] == 10.0 and q["ask"] == 10.1
    assert q["feed"] == "iex"
    assert "not full-market" in q["feed_note"].lower()


# ----------------------------------------------------- corporate actions --

def test_forward_split_ratio():
    a = _normalise_corporate_action("forward_split",
                                    {"ex_date": "2024-06-10", "new_rate": "10",
                                     "old_rate": "1"})
    assert a["ratio"] == 10.0 and a["action_type"] == "forward_split"


def test_reverse_split_ratio_is_below_one():
    a = _normalise_corporate_action("reverse_split",
                                    {"ex_date": "2024-06-10", "new_rate": "1",
                                     "old_rate": "10"})
    assert a["ratio"] == pytest.approx(0.1)


def test_cash_dividend_amount():
    a = _normalise_corporate_action("cash_dividend",
                                    {"ex_date": "2024-05-10", "rate": "0.24"})
    assert a["cash_amount"] == 0.24 and a["ratio"] is None


def test_malformed_rates_do_not_crash():
    a = _normalise_corporate_action("forward_split", {"ex_date": "x", "new_rate": "abc",
                                                      "old_rate": "0"})
    assert a["ratio"] is None


def test_corporate_actions_are_flattened_and_sorted():
    p = FakeAlpaca([{"corporate_actions": {
        "cash_dividends": [{"ex_date": "2024-05-10", "rate": "0.24"}],
        "forward_splits": [{"ex_date": "2024-01-10", "new_rate": "4", "old_rate": "1"}],
    }}])
    out = p.corporate_actions("AAPL")
    assert [a["ex_date"] for a in out] == ["2024-01-10", "2024-05-10"]


# --------------------------------------------------------------- options --

def test_occ_symbol_is_parsed():
    assert _parse_occ("AAPL240119C00150000") == {
        "expiration": "2024-01-19", "type": "call", "strike": 150.0, "underlying": "AAPL"}
    assert _parse_occ("SPY251219P00400500")["strike"] == 400.5
    assert _parse_occ("garbage")["strike"] is None


def test_option_snapshots_are_normalised():
    p = FakeAlpaca([{"snapshots": {"AAPL240119C00150000": {
        "greeks": {"delta": 0.5, "gamma": 0.01, "theta": -0.02, "vega": 0.1},
        "latestQuote": {"bp": 5.0, "ap": 5.2},
        "latestTrade": {"p": 5.1},
        "impliedVolatility": 0.35,
        "openInterest": 1234,
        "dailyBar": {"v": 900}}}}])
    c = p.chain("AAPL")[0]
    assert c["strike"] == 150.0 and c["type"] == "call"
    assert c["bid"] == 5.0 and c["ask"] == 5.2
    assert c["open_interest"] == 1234
    assert c["delta"] == 0.5


# -------------------------------------------------------------- universe --

def test_asset_list_is_normalised():
    p = FakeAlpaca([[{"symbol": "AAPL", "name": "Apple Inc", "exchange": "NASDAQ",
                      "class": "us_equity", "status": "active", "tradable": True,
                      "shortable": True, "easy_to_borrow": False},
                     {"symbol": "DEAD", "status": "inactive", "tradable": False}]])
    rows = p.list_symbols()
    assert rows[0]["symbol"] == "AAPL" and rows[0]["active"] == 1
    assert rows[1]["active"] == 0


def test_paper_and_live_use_different_trading_hosts():
    paper = FakeAlpaca([[]], paper=True)
    paper.list_symbols()
    assert "paper-api" in paper.requests[0][0]
    live = FakeAlpaca([[]], paper=False)
    live.list_symbols()
    assert "paper-api" not in live.requests[0][0]


# ---------------------------------------------------------------- stream --

def test_stream_url_includes_the_feed():
    assert AlpacaProvider("k", "s", feed="iex").stream_url().endswith("/v2/iex")
    assert AlpacaProvider("k", "s", feed="sip").stream_url().endswith("/v2/sip")


def test_stream_refuses_without_credentials():
    with pytest.raises(ProviderUnavailable):
        AlpacaProvider(None, None).stream(["AAPL"], lambda m: None)


# ----------------------------------------------------------- http errors --

def test_http_errors_map_to_actionable_messages(monkeypatch):
    import urllib.error
    import urllib.request

    def raise_code(code):
        def fake_urlopen(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, code, "err", {},
                                         __import__("io").BytesIO(b'{"message":"nope"}'))
        return fake_urlopen

    p = AlpacaProvider("k", "s", feed="sip")
    for code, needle in ((401, "authentication rejected"),
                         (403, "not included in this subscription"),
                         (429, "rate limited")):
        monkeypatch.setattr(urllib.request, "urlopen", raise_code(code))
        with pytest.raises(ProviderUnavailable) as e:
            p.daily("AAPL")
        assert needle in str(e.value)


def test_api_usage_is_recorded():
    seen = []
    p = FakeAlpaca([{"bars": {"AAPL": [_bar("2024-01-02")]}, "next_page_token": None}])
    p._usage = lambda **kw: seen.append(kw)
    # FakeAlpaca overrides _get, so exercise the recorder directly through the
    # real path's contract instead.
    p._record("stocks/bars", ok=True, ms=12.0, rows=1, symbols=1)
    assert seen and seen[0]["provider"] == "alpaca" and seen[0]["endpoint"] == "stocks/bars"
