"""Authenticated loopback CLI for an explicit PAPER cohort boundary.

The CLI never imports or constructs an Engine.  It verifies the saved per-launch token against
ZackBot's HMAC ping, then calls the same authenticated POST route as the panel.
"""
import argparse
import hashlib
import hmac
import json
import os
import secrets
import sys
from urllib.request import Request, urlopen


def _session(path):
    try:
        with open(path, encoding='utf-8-sig') as fh: row = json.load(fh)
    except (OSError, ValueError, TypeError) as ex:
        raise ValueError(f'session record unavailable ({type(ex).__name__})')
    token, port = row.get('token'), row.get('port')
    if not isinstance(token, str) or len(token) < 32 or isinstance(port, bool) or not isinstance(port, int) \
            or not 1 <= port <= 65535:
        raise ValueError('session record is malformed')
    return token, port


def _json(req, timeout=5):
    with urlopen(req, timeout=timeout) as response:
        body = json.loads(response.read())
    if not isinstance(body, dict): raise ValueError('ZackBot returned a malformed response')
    return body


def call_boundary(session_path, evidence_path, note, accept_drift, timeout=5):
    token, port = _session(session_path); base = f'http://127.0.0.1:{port}'
    nonce = secrets.token_hex(16)
    ping = _json(Request(f'{base}/api/ping?nonce={nonce}'), timeout)
    want = hmac.new(token.encode(), nonce.encode(), hashlib.sha256).hexdigest()
    if ping.get('app') != 'zackbot' or not hmac.compare_digest(str(ping.get('proof') or ''), want):
        raise ValueError('session token does not authenticate this ZackBot process')
    payload = json.dumps(dict(evidence_path=os.path.abspath(evidence_path), note=note,
                              accept_drift=accept_drift), separators=(',', ':')).encode()
    req = Request(f'{base}/api/paper_cohort/boundary', data=payload, method='POST',
                  headers={'Content-Type': 'application/json', 'X-ZB-Token': token})
    out = _json(req, timeout)
    if out.get('ok') is not True: raise ValueError(str(out.get('error') or 'cohort boundary refused'))
    return out.get('msg')


def main(argv=None):
    p = argparse.ArgumentParser(description='Close a flat PAPER cohort through the authenticated ZackBot process')
    p.add_argument('--session', required=True, help='running ZackBot session.json')
    p.add_argument('--evidence-path', required=True, help='absolute evidence root outside the runtime/repository')
    p.add_argument('--note', default='')
    drift = p.add_mutually_exclusive_group(required=True)
    drift.add_argument('--accept-drift', action='store_true')
    drift.add_argument('--reject-drift', action='store_true')
    args = p.parse_args(argv)
    try:
        result = call_boundary(args.session, args.evidence_path, args.note, bool(args.accept_drift))
        print(json.dumps(result, sort_keys=True)); return 0
    except Exception as ex:
        print(f'REFUSED: {ex}', file=sys.stderr); return 2


if __name__ == '__main__':
    raise SystemExit(main())
