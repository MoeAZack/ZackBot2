import pytest

from tools.research import authority_v6 as V6


@pytest.mark.parametrize("symbol", ["BTCUSDT", "哈基米USDT", "币安人生USDT"])
def test_official_checksum_matches_exact_utf8_filename(symbol):
    name = f"{symbol}-1d-2026-11-01.zip"
    digest = "a"*64
    raw = f"{digest}  {name}\n".encode("utf-8")
    assert V6.parse_official_checksum(raw, name) == digest
    with pytest.raises(V6.AuthorityV6Error):
        V6.parse_official_checksum(raw, name+"other")


def test_invalid_filename_utf8_bytes_fail_as_authority_error():
    with pytest.raises(V6.AuthorityV6Error):
        V6.parse_official_checksum(b"a"*64+b"  \xffUSDT.zip\n", "USDT.zip")
