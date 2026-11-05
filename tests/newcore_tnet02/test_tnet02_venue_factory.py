"""``make_testnet_venue``: the public one-call venue for launchers outside ``newcore`` (RT runtime CLI)."""
from __future__ import annotations

import pytest

from newcore.tnet02.live import API_KEY_ENV, API_SECRET_ENV, LiveError, make_testnet_venue
from newcore.tnet02.wiring import key_digest
from newcore.venue import TestnetHttpSender
from newcore.venue.binance_usdm_testnet import DEMO_TESTNET_BASE_URL, TESTNET_BASE_URL, BinanceUsdMTestnetVenue

from .fake_binance import FakeBinance, FakeClock
from .support import T0
from .test_tnet02_live import DOC_KEY, DOC_SECRET

ENV = {API_KEY_ENV: DOC_KEY, API_SECRET_ENV: DOC_SECRET}


@pytest.mark.parametrize("origin", [TESTNET_BASE_URL, DEMO_TESTNET_BASE_URL], ids=["legacy", "demo"])
def test_factory_builds_a_venue_on_the_named_origin_with_the_real_sender(origin):
    venue, digest = make_testnet_venue(origin=origin, sender=TestnetHttpSender(origin), clock=lambda: T0,
                                       environ=ENV)
    assert isinstance(venue, BinanceUsdMTestnetVenue) and venue._base_url == origin
    assert isinstance(venue._sender, TestnetHttpSender) and venue._sender.origin == origin
    assert digest == key_digest(DOC_KEY)


def test_factory_sends_through_an_injected_sender_and_feeds_the_observer():
    fake = FakeBinance(FakeClock(T0))
    fake.origin = TESTNET_BASE_URL
    seen = []
    venue, _digest = make_testnet_venue(origin=TESTNET_BASE_URL, clock=fake.clock, environ=ENV, sender=fake,
                                        rate_observer=seen.append)
    venue.positions(None)
    assert fake.requests and seen and seen[0].http_status == 200


@pytest.mark.parametrize("origin", [None, "https://fapi.binance.com", TESTNET_BASE_URL + "/"],
                         ids=["none", "mainnet", "slash"])
def test_factory_refuses_unapproved_origins(origin):
    with pytest.raises(LiveError) as caught:
        make_testnet_venue(origin=origin, sender=TestnetHttpSender(), clock=lambda: T0, environ=ENV)
    assert caught.value.code == "venue.origin"


def test_factory_refuses_a_sender_bound_to_the_other_origin():
    with pytest.raises(LiveError) as caught:
        make_testnet_venue(origin=TESTNET_BASE_URL, clock=lambda: T0, environ=ENV,
                           sender=TestnetHttpSender(DEMO_TESTNET_BASE_URL))
    assert caught.value.code == "venue.origin_mismatch"


def test_factory_refuses_a_sender_that_declares_no_origin():
    with pytest.raises(LiveError) as caught:
        make_testnet_venue(origin=TESTNET_BASE_URL, sender=lambda request: None, clock=lambda: T0, environ=ENV)
    assert caught.value.code == "venue.origin_mismatch"


def test_factory_refuses_missing_credentials_without_echoing_them():
    with pytest.raises(LiveError) as caught:
        make_testnet_venue(origin=TESTNET_BASE_URL, sender=TestnetHttpSender(), clock=lambda: T0,
                           environ={API_KEY_ENV: DOC_KEY})
    assert caught.value.code == "credentials.api_secret_missing" and DOC_KEY not in str(caught.value)
