"""NC-03 wire layer: the testnet guard (pinned host + explicit environment binding)."""
import pytest

from newcore.venue.guard import (TESTNET_BASE_URL, TESTNET_ENVIRONMENT, VenueGuardError, check_binding,
                                 check_request_url)


def test_pinned_constants():
    assert TESTNET_BASE_URL == 'https://testnet.binancefuture.com'
    assert TESTNET_ENVIRONMENT == 'testnet'


def test_exact_testnet_pair_accepted():
    assert check_binding('testnet', 'https://testnet.binancefuture.com') == ('testnet', TESTNET_BASE_URL)


@pytest.mark.parametrize('url', [
    'https://fapi.binance.com',                                  # USD-M mainnet
    'https://dapi.binance.com',                                  # COIN-M mainnet
    'https://api.binance.com',                                   # spot mainnet
    'https://fapi1.binance.com',
    'https://testnet.binancefuture.com/',                        # trailing slash: no normalisation
    'http://testnet.binancefuture.com',                          # scheme
    'HTTPS://TESTNET.BINANCEFUTURE.COM',                         # case
    'https://testnet.binancefuture.com:443',                     # port
    'https://testnet.binancefuture.com.evil.example',            # look-alike suffix
    'https://testnet.binancefuture.com@fapi.binance.com',        # user-info trick
    'https://fapi.binance.com/?h=testnet.binancefuture.com',
    'https://testnet.binancefuture.com/fapi',                    # path on the base
    ' https://testnet.binancefuture.com',
    '',
])
def test_any_other_base_url_refused(url):
    with pytest.raises(VenueGuardError):
        check_binding('testnet', url)


@pytest.mark.parametrize('url', [None, b'https://testnet.binancefuture.com', 1])
def test_non_string_base_url_refused(url):
    with pytest.raises(VenueGuardError):
        check_binding('testnet', url)


@pytest.mark.parametrize('env', ['mainnet', 'live', 'prod', 'TESTNET', 'Testnet', ' testnet', 'testnet ', '', None,
                                 True, 1, b'testnet'])
def test_mismatched_environment_refused_even_with_testnet_url(env):
    with pytest.raises(VenueGuardError):
        check_binding(env, TESTNET_BASE_URL)


def test_mainnet_environment_with_mainnet_url_refused():
    with pytest.raises(VenueGuardError):
        check_binding('mainnet', 'https://fapi.binance.com')


def test_str_subclass_is_not_accepted():
    class Sneaky(str):
        def __eq__(self, other): return True
        __hash__ = str.__hash__
    with pytest.raises(VenueGuardError):
        check_binding(Sneaky('mainnet'), TESTNET_BASE_URL)
    with pytest.raises(VenueGuardError):
        check_binding('testnet', Sneaky('https://fapi.binance.com'))


@pytest.mark.parametrize('url', [
    TESTNET_BASE_URL + '/fapi/v1/order',
    TESTNET_BASE_URL + '/fapi/v2/positionRisk',
    TESTNET_BASE_URL + '/fapi/v1/algoOrder',
])
def test_request_url_inside_pin_ok(url):
    assert check_request_url(url) == url


@pytest.mark.parametrize('url', [
    'https://fapi.binance.com/fapi/v1/order',
    TESTNET_BASE_URL + '/sapi/v1/account',
    TESTNET_BASE_URL + '/fapi/',
    TESTNET_BASE_URL + '/fapi/v1/order?x=1',
    TESTNET_BASE_URL + '/fapi/v1/order#x',
    TESTNET_BASE_URL + '/fapi/../sapi/v1/x',
    TESTNET_BASE_URL + '/fapi//v1/order',
    TESTNET_BASE_URL + '/fapi/v1/@fapi.binance.com',
    TESTNET_BASE_URL + '.evil.example/fapi/v1/order',
    None,
])
def test_request_url_outside_pin_refused(url):
    with pytest.raises(VenueGuardError):
        check_request_url(url)


# Cowork finding 4: only canonical letter paths reach the sender.
@pytest.mark.parametrize('path', ['v1/%2e%2e/sapi', 'v1/order%2e', 'v1/order;jsessionid=1', 'v1/order%0d%0aHost:x',
                                  'v1/order\r\nHost: evil', 'v1/order\n', 'v1/order\x00', 'v1/ord er', 'v1/order/',
                                  'v1/order.json', 'v1/order-x', 'v1/./order', 'x1/order', 'v1', 'v1/ordér',
                                  'v1/order\t', 'v1/‥/order', 'v1/order‥', '‥‥/v1/order'])
def test_non_canonical_paths_refused(path):
    with pytest.raises(VenueGuardError):
        check_request_url(TESTNET_BASE_URL + '/fapi/' + path)


@pytest.mark.parametrize('path', ['v1/order', 'v2/positionRisk', 'v1/positionSide/dual', 'v1/listenKey'])
def test_canonical_paths_accepted(path):
    assert check_request_url(TESTNET_BASE_URL + '/fapi/' + path)
