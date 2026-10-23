"""RUNTIME-CA-01 (#193): the CA bundle a one-file exe extracts to %TEMP%\\_MEI... can vanish while the app runs. The pinned
copy must keep every new HTTPS connection verifiable (and verifying, never CERT_NONE) after the extraction-folder bundle
and even the pinned copy itself are deleted - without a restart and without touching anything but the CA file."""
import os, sys
import pytest
import requests
import requests.adapters as RA
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ca_bundle as CA                                                   # noqa: E402

REAL = CA._source()


class Conn:                                                              # what cert_verify configures on a pooled connection
    cert_reqs = ca_certs = ca_cert_dir = None


@pytest.fixture
def mei(tmp_path, monkeypatch):
    """A fake one-file extraction folder holding the bundle requests was imported with, and a clean module state."""
    src = tmp_path / '_MEI12345' / 'certifi' / 'cacert.pem'
    src.parent.mkdir(parents=True)
    src.write_bytes(open(REAL, 'rb').read())
    monkeypatch.setattr(RA, 'DEFAULT_CA_BUNDLE_PATH', str(src))
    monkeypatch.setattr(RA.HTTPAdapter, 'cert_verify', RA.HTTPAdapter.cert_verify)   # restored after the test
    monkeypatch.setattr(CA, '_PEM', None)
    monkeypatch.setattr(CA, '_PATH', None)
    monkeypatch.setattr(CA, 'STATS', dict(CA.STATS, installed=False, restored=0, redirected=0, error=None))
    monkeypatch.delenv('REQUESTS_CA_BUNDLE', raising=False)
    monkeypatch.delenv('CURL_CA_BUNDLE', raising=False)
    if getattr(RA.HTTPAdapter.cert_verify, '_runtime_ca01', False):        # app already imported in this run: unwrap
        monkeypatch.setattr(RA.HTTPAdapter, 'cert_verify', RA.HTTPAdapter.cert_verify.__wrapped_orig__)
    return src, tmp_path / 'data' / 'certs'


def session_verify(url='https://testnet.binancefuture.com/fapi/v1/time'):
    s = requests.Session()
    return s.merge_environment_settings(url, {}, None, None, None)['verify']


def check(verify, url='https://testnet.binancefuture.com/'):
    c = Conn()
    RA.HTTPAdapter().cert_verify(c, url, verify, None)
    return c


def test_without_the_pin_a_vanished_extraction_bundle_breaks_every_new_connection(mei):
    src, _ = mei
    src.unlink()                                                         # the 15:15 Cairo event
    with pytest.raises(OSError, match='Could not find a suitable TLS CA certificate bundle'):
        check(session_verify())


def test_pinned_bundle_survives_the_extraction_folder_disappearing(mei):
    src, certs = mei
    path = CA.install(str(certs), source=str(src))
    assert path == str(certs / 'cacert.pem') and os.environ['REQUESTS_CA_BUNDLE'] == path
    assert session_verify() == path                                      # every trust_env Session uses the pinned copy
    src.unlink(); src.parent.rmdir()
    c = check(session_verify())
    assert c.cert_reqs == 'CERT_REQUIRED' and c.ca_certs == path         # still verifying, never CERT_NONE
    assert CA.status()['installed'] and CA.status()['present']


def test_a_deleted_pinned_copy_is_restored_from_memory_on_the_next_connection(mei):
    src, certs = mei
    path = CA.install(str(certs), source=str(src))
    want = open(path, 'rb').read()
    src.unlink()
    os.remove(path)                                                      # even the pinned copy goes
    c = check(session_verify())
    assert c.cert_reqs == 'CERT_REQUIRED' and c.ca_certs == path
    assert open(path, 'rb').read() == want and CA.STATS['restored'] == 1
    with open(path, 'wb') as f:
        f.write(b'truncated')                                            # an altered copy is rewritten as well
    check(session_verify())
    assert open(path, 'rb').read() == want and CA.STATS['restored'] == 2


def test_a_session_on_the_default_path_is_moved_to_the_pinned_copy(mei):
    src, certs = mei
    path = CA.install(str(certs), source=str(src))
    s = requests.Session(); s.trust_env = False                          # ignores REQUESTS_CA_BUNDLE: verify stays True
    v = s.merge_environment_settings('https://x.test/', {}, None, None, None)['verify']
    assert v is True
    src.unlink()
    c = check(v)
    assert c.cert_reqs == 'CERT_REQUIRED' and c.ca_certs == path and CA.STATS['redirected'] == 1


def test_verification_off_and_plain_http_are_untouched(mei):
    src, certs = mei
    CA.install(str(certs), source=str(src))
    assert check(False).cert_reqs == 'CERT_NONE'
    assert check(session_verify('http://x.test/'), url='http://x.test/').cert_reqs == 'CERT_NONE'


def test_an_unreadable_source_changes_nothing_and_stays_fail_closed(mei, tmp_path):
    src, certs = mei
    bad = tmp_path / 'bad.pem'
    bad.write_text('not a bundle')
    before = RA.HTTPAdapter.cert_verify
    assert CA.install(str(certs), source=str(bad)) is None
    assert CA.install(str(certs), source=str(tmp_path / 'missing.pem')) is None
    assert 'REQUESTS_CA_BUNDLE' not in os.environ and RA.HTTPAdapter.cert_verify is before
    assert not (certs / 'cacert.pem').exists() and CA.STATS['error']
    src.unlink()
    with pytest.raises(OSError):                                         # exactly the old behaviour: no silent CERT_NONE
        check(session_verify())


def test_install_is_idempotent_and_wraps_once(mei):
    src, certs = mei
    CA.install(str(certs), source=str(src))
    first = RA.HTTPAdapter.cert_verify
    CA.install(str(certs), source=str(src))
    assert RA.HTTPAdapter.cert_verify is first and first._runtime_ca01


def test_the_status_carries_no_certificate_bytes(mei):
    src, certs = mei
    CA.install(str(certs), source=str(src))
    st = CA.status()
    assert set(st) == {'installed', 'path', 'sha256', 'restored', 'redirected', 'last_restore', 'error', 'present'}
    assert 'BEGIN CERTIFICATE' not in repr(st)
