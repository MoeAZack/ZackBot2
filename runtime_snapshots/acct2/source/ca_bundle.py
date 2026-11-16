"""RUNTIME-CA-01 (#193): the TLS CA bundle must outlive the PyInstaller one-file extraction folder.

A one-file exe unpacks to %TEMP%\\_MEIxxxx and certifi.where() / requests' DEFAULT_CA_BUNDLE_PATH point inside it. On
23 Oct 2026 15:15 Cairo that file vanished while the app ran (temp cleanup), and every new HTTPS connection then failed
("Could not find a suitable TLS CA certificate bundle") until a restart. Here, at startup:

  * the bundle bytes are read ONCE into memory and written to an app-owned folder (atomic replace);
  * REQUESTS_CA_BUNDLE points every requests Session (trust_env) at that pinned copy;
  * `requests.adapters.HTTPAdapter.cert_verify` - the one check every new TLS connection passes - is wrapped so that a
    missing pinned copy is rewritten from memory before the check, and a session still on the default bundle path whose
    file is gone is moved onto the pinned copy. Recovery needs no restart and never touches orders or positions.

Only the public CA certificates are handled; no key, token or account data. If the bundle cannot be read at startup the
module does nothing (requests keeps its own behaviour, so connections fail closed exactly as before).
"""
from __future__ import annotations

import hashlib
import logging
import os
import threading
import time

log = logging.getLogger('zackbot')

_LOCK = threading.Lock()
_PEM: bytes | None = None
_PATH: str | None = None
STATS = dict(installed=False, path=None, sha256=None, restored=0, redirected=0, last_restore=None, error=None)


def _source() -> str | None:
    try:
        import certifi
        return certifi.where()
    except Exception:
        try:
            from requests import certs
            return certs.where()
        except Exception:
            return None


def _write(path: str, data: bytes) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f'{path}.{os.getpid()}.{threading.get_ident()}.tmp'
    with open(tmp, 'wb') as f:
        f.write(data)
    os.replace(tmp, path)


def ensure() -> str | None:
    """The pinned bundle path, rewritten from memory when it is missing or altered (None when never installed)."""
    if _PEM is None or _PATH is None:
        return None
    with _LOCK:
        try:
            ok = os.path.getsize(_PATH) == len(_PEM)
        except OSError:
            ok = False
        if not ok:
            try:
                _write(_PATH, _PEM)
                STATS['restored'] += 1
                STATS['last_restore'] = time.time()
                log.warning(f'RUNTIME-CA-01: TLS CA bundle was missing or altered - restored {_PATH} from memory')
            except OSError as ex:
                STATS['error'] = f'restore failed: {ex}'[:200]
                log.error(f'RUNTIME-CA-01: could not restore the TLS CA bundle ({ex}) - HTTPS stays fail-closed')
    return _PATH


def _same(a, b) -> bool:
    return isinstance(a, str) and isinstance(b, str) and os.path.normcase(os.path.abspath(a)) == os.path.normcase(
        os.path.abspath(b))


def _wrap(orig):
    def cert_verify(self, conn, url, verify, cert):
        try:
            if _PATH is not None and isinstance(url, str) and url.lower().startswith('https') and verify:
                if _same(verify, _PATH):
                    ensure()
                elif verify is True:
                    import requests.adapters as RA
                    if not os.path.exists(RA.DEFAULT_CA_BUNDLE_PATH or ''):
                        verify = ensure() or verify          # the extraction-folder bundle is gone: use the pinned one
                        STATS['redirected'] += 1
        except Exception:
            pass                                             # never mask the real check below
        return orig(self, conn, url, verify, cert)
    cert_verify._runtime_ca01 = True
    cert_verify.__wrapped_orig__ = orig
    return cert_verify


def install(dest_dir: str, source: str | None = None) -> str | None:
    """Pin the CA bundle under dest_dir, point requests at it and guard every new TLS connection. Idempotent.
    Returns the pinned path, or None when the bundle could not be read (nothing changed)."""
    global _PEM, _PATH
    src = source or _source()
    try:
        with open(src, 'rb') as f:
            data = f.read()
        if b'BEGIN CERTIFICATE' not in data:
            raise ValueError('not a PEM certificate bundle')
    except Exception as ex:
        STATS['error'] = f'source unreadable: {ex}'[:200]
        log.error(f'RUNTIME-CA-01: TLS CA bundle not pinned ({ex})')
        return None
    path = os.path.join(dest_dir, 'cacert.pem')
    with _LOCK:
        _PEM, _PATH = data, path
    try:
        _write(path, data)
    except OSError as ex:
        STATS['error'] = f'pin failed: {ex}'[:200]
        log.error(f'RUNTIME-CA-01: could not write the pinned TLS CA bundle ({ex})')
        with _LOCK:
            _PEM = _PATH = None
        return None
    os.environ['REQUESTS_CA_BUNDLE'] = path
    import requests.adapters as RA
    if not getattr(RA.HTTPAdapter.cert_verify, '_runtime_ca01', False):
        RA.HTTPAdapter.cert_verify = _wrap(RA.HTTPAdapter.cert_verify)
    STATS.update(installed=True, path=path, sha256=hashlib.sha256(data).hexdigest(), error=None)
    return path


def status() -> dict:
    return dict(STATS, present=bool(_PATH and os.path.exists(_PATH)))
