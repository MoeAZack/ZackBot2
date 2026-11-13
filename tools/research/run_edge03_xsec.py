"""Bounded Stage-A packet runner for the research-only ``xsec_rs_4h.v1`` candidate.

The runner has no network, runtime registry, PAPER, or holdout capability.  It
accepts only two audited local manifests: a bounded Binance USD-M 4h candle
manifest and an explicit point-in-time funding manifest.  Every manifest and
referenced file is SHA-256 bound, and output is write-once.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from dataclasses import asdict
from pathlib import Path

import pandas as pd

import edge03_xsec as X


FORMAT = 'zackbot.edge03.xsec-stage-a/1'
CANDLE_SCHEMA = 'zackbot.edge03.candles/1'
FUNDING_SCHEMA = 'zackbot.edge03.funding-pit/1'
VENUE = 'BINANCE_USDM'
START = '2024-10-14T00:00:00Z'
END = '2026-09-28T00:00:00Z'


def _utc_ms(text: str) -> int:
    value = pd.Timestamp(text)
    if value.tz is None or str(value.tz) != 'UTC':
        raise RunnerError(f'timestamp must be explicit UTC: {text!r}')
    return int(value.value // 1_000_000)


START_MS, END_MS = _utc_ms(START), _utc_ms(END)
SLICES = (
    ('train', START, '2024-12-30T00:00:00Z'),
    ('WF0', '2024-12-30T00:00:00Z', '2025-03-31T00:00:00Z'),
    ('WF1', '2025-03-31T00:00:00Z', '2025-06-30T00:00:00Z'),
    ('WF2', '2025-06-30T00:00:00Z', '2025-09-29T00:00:00Z'),
    ('WF3', '2025-09-29T00:00:00Z', '2025-12-29T00:00:00Z'),
    ('WF4', '2025-12-29T00:00:00Z', '2026-03-30T00:00:00Z'),
    ('WF5', '2026-03-30T00:00:00Z', '2026-06-29T00:00:00Z'),
    ('WF6', '2026-06-29T00:00:00Z', END),
)
FORWARD = tuple(x for x in SLICES if x[0] != 'train')


class RunnerError(RuntimeError):
    pass


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def _json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as e:
        raise RunnerError(f'cannot read manifest {path}: {e}') from None
    if not isinstance(value, dict):
        raise RunnerError(f'manifest must be an object: {path}')
    return value


def _bounded_file(manifest: Path, spec: dict, label: str) -> tuple[Path, dict]:
    if not isinstance(spec, dict) or set(spec) != {'path', 'sha256'}:
        raise RunnerError(f'{label}: file entry must contain only path and sha256')
    root = manifest.parent.resolve()
    path = (root / str(spec['path'])).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        raise RunnerError(f'{label}: path escapes manifest directory') from None
    if not path.is_file():
        raise RunnerError(f'{label}: missing file {path.name}')
    digest = _sha(path)
    if digest != spec['sha256']:
        raise RunnerError(f'{label}: sha256 mismatch')
    return path, {'path': str(spec['path']), 'sha256': digest, 'bytes': path.stat().st_size}


def _manifest_common(doc: dict, schema: str, label: str):
    if doc.get('schema') != schema or doc.get('venue') != VENUE:
        raise RunnerError(f'{label}: wrong schema or venue')
    if doc.get('audited') is not True:
        raise RunnerError(f'{label}: audited=true is required')
    if doc.get('window') != {'start': START, 'end_exclusive': END}:
        raise RunnerError(f'{label}: window must be the frozen development interval')
    if tuple(doc.get('symbols', ())) != X.CORE_10_ELIGIBLE_9:
        raise RunnerError(f'{label}: symbols must be frozen CORE-10-ELIGIBLE-9 in order')


def clean_head(repo) -> str:
    repo = Path(repo).resolve()
    try:
        head = subprocess.run(['git', '-C', str(repo), 'rev-parse', 'HEAD'], check=True, capture_output=True,
                              text=True).stdout.strip()
        dirty = subprocess.run(['git', '-C', str(repo), 'status', '--porcelain', '--untracked-files=normal'], check=True,
                               capture_output=True, text=True).stdout
    except (OSError, subprocess.CalledProcessError) as e:
        raise RunnerError(f'cannot bind git head: {e}') from None
    if dirty:
        raise RunnerError('dirty git tree; exact-head packet refused')
    if len(head) != 40:
        raise RunnerError('git did not return an exact 40-character head')
    return head


def load_candles(manifest_path) -> tuple[dict[str, pd.DataFrame], dict]:
    manifest = Path(manifest_path).resolve()
    doc = _json(manifest)
    _manifest_common(doc, CANDLE_SCHEMA, 'candles')
    if doc.get('timeframe') != '4h' or doc.get('terminal_open') is not True:
        raise RunnerError('candles: timeframe=4h and terminal_open=true are required')
    files = doc.get('files')
    if not isinstance(files, dict) or tuple(files) != X.CORE_10_ELIGIBLE_9:
        raise RunnerError('candles: one ordered file is required for every headline symbol')
    frames, provenance = {}, {}
    for symbol in X.CORE_10_ELIGIBLE_9:
        path, rec = _bounded_file(manifest, files[symbol], f'candles {symbol}')
        try:
            frame = pd.read_csv(path)
            frame = X._frame(frame, symbol)
        except Exception as e:
            raise RunnerError(f'candles {symbol}: invalid CSV: {e}') from None
        t = frame.t.to_numpy('int64')
        if not len(t) or t[0] != START_MS or t[-1] != END_MS or (t < START_MS).any() or (t > END_MS).any():
            raise RunnerError(f'candles {symbol}: file must be bounded [{START}, {END}] with terminal open only')
        frames[symbol], provenance[symbol] = frame, dict(rec, rows=len(frame), first_ms=int(t[0]), last_ms=int(t[-1]))
    return frames, {'manifest': {'path': str(manifest), 'sha256': _sha(manifest)}, 'files': provenance}


def load_funding(manifest_path) -> tuple[X.FundingTape, dict]:
    manifest = Path(manifest_path).resolve()
    doc = _json(manifest)
    _manifest_common(doc, FUNDING_SCHEMA, 'funding')
    if doc.get('point_in_time') is not True:
        raise RunnerError('funding: point_in_time=true is required')
    calendar_path, calendar_prov = _bounded_file(manifest, doc.get('calendar'), 'funding calendar')
    records_path, records_prov = _bounded_file(manifest, doc.get('records'), 'funding records')
    try:
        calendar_df = pd.read_csv(calendar_path)
        records_df = pd.read_csv(records_path)
    except Exception as e:
        raise RunnerError(f'funding: invalid CSV: {e}') from None
    if list(calendar_df.columns) != ['settlement_ms']:
        raise RunnerError('funding calendar: exact settlement_ms column required')
    try:
        settlements = tuple(int(v) for v in calendar_df.settlement_ms)
    except (TypeError, ValueError, OverflowError):
        raise RunnerError('funding calendar: integer milliseconds required') from None
    if settlements != tuple(sorted(set(settlements))):
        raise RunnerError('funding calendar: timestamps must be sorted and unique')
    if not settlements or settlements[0] <= START_MS or settlements[-1] != END_MS or any(t > END_MS for t in settlements):
        raise RunnerError('funding calendar: must cover explicit settlements in (start, end] including end')
    columns = ['symbol', 'settlement_ms', 'available_ms', 'rate', 'mark_price']
    if list(records_df.columns) != columns:
        raise RunnerError(f'funding records: exact columns required: {columns}')
    records, seen = [], set()
    for raw in records_df.itertuples(index=False):
        try:
            symbol = str(raw.symbol)
            settlement_ms, available_ms = int(raw.settlement_ms), int(raw.available_ms)
            key = (symbol, settlement_ms)
            record = X.FundingRecord(symbol, settlement_ms, available_ms, float(raw.rate), float(raw.mark_price))
        except (TypeError, ValueError, OverflowError, X.XsecError) as e:
            raise RunnerError(f'funding records: invalid row: {e}') from None
        if key in seen:
            raise RunnerError(f'funding records: duplicate {key}')
        if settlement_ms not in settlements:
            raise RunnerError(f'funding records: off-calendar {key}')
        if available_ms > settlement_ms:
            raise RunnerError(f'funding records: non-causal {key}')
        seen.add(key)
        records.append(record)
    expected = {(symbol, t) for symbol in X.CORE_10_ELIGIBLE_9 for t in settlements}
    missing, extra = expected - seen, seen - expected
    if missing or extra:
        first = sorted(missing or extra)[0]
        raise RunnerError(f'funding records: {"missing" if missing else "unexpected"} {first}')
    try:
        tape = X.FundingTape(settlements, records)
    except X.XsecError as e:
        raise RunnerError(f'funding: {e}') from None
    return tape, {
        'manifest': {'path': str(manifest), 'sha256': _sha(manifest)},
        'calendar': dict(calendar_prov, rows=len(settlements), first_ms=settlements[0], last_ms=settlements[-1]),
        'records': dict(records_prov, rows=len(records)),
    }


def _summary(trades: list[dict], lo: int, hi: int) -> dict:
    values = [float(t['net_r']) for t in trades]
    wins = sum(v for v in values if v > 0)
    losses = -sum(v for v in values if v < 0)
    equity = peak = drawdown = 0.0
    for trade in sorted(trades, key=lambda t: (t['exit_ms'], t['symbol'])):
        equity += float(trade['net_r'])
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    weeks = (hi - lo) / (7 * X.DAY_MS)
    return {
        'closes': len(trades),
        'trades_per_week': len(trades) / weeks,
        'gross_r': sum(float(t['gross_r']) for t in trades),
        'fee_r': sum(float(t['fee_r']) for t in trades),
        'funding_r': sum(float(t['funding_r']) for t in trades),
        'net_r': sum(values),
        'mean_net_r': sum(values) / len(values) if values else None,
        'profit_factor': (wins / losses if losses else None),
        'profit_factor_infinite': bool(wins and not losses),
        'max_drawdown_r': drawdown,
    }


def _integrity(frames: dict[str, pd.DataFrame], lo: int, hi: int) -> dict:
    expected = set(range(lo, hi, X.TF_MS))
    out = {}
    for symbol in X.CORE_10_ELIGIBLE_9:
        actual = {int(t) for t in frames[symbol].t if lo <= int(t) < hi}
        missing, off_grid = expected - actual, actual - expected
        out[symbol] = {'expected': len(expected), 'rows': len(actual), 'missing': len(missing),
                       'off_grid': len(off_grid), 'complete': not missing and not off_grid}
    return out


def _slice(result: X.RunResult, name: str, start: str, end: str) -> dict:
    lo, hi = _utc_ms(start), _utc_ms(end)
    trades = [asdict(t) for t in result.trades if lo <= t.decision_ms < hi]
    incomplete = [asdict(e) for e in result.incomplete if lo <= e.decision_ms < hi]
    decisions = [{
        'close_ms': d.close_ms,
        'eligible': list(d.eligible),
        'ranking': [asdict(r) for r in d.ranking],
        'selected': list(getattr(d, result.side)),
        'skipped': d.skipped,
    } for d in result.decisions if lo <= d.close_ms < hi]
    return {
        'name': name, 'start': start, 'end_exclusive': end, 'decisions': decisions,
        'trades': trades, 'incomplete': incomplete,
        'blocked_by_lock': [list(x) for x in result.blocked_by_lock if lo <= x[0] < hi],
        'cooldowns': [list(x) for x in result.cooldowns if lo <= x[0] < hi],
        'summary': _summary(trades, lo, hi),
    }


def build_packet(candle_manifest, funding_manifest, repo) -> dict:
    head = clean_head(repo)  # before any research input is read
    frames, candle_prov = load_candles(candle_manifest)
    funding, funding_prov = load_funding(funding_manifest)
    runs = {}
    for side in ('long', 'short'):
        for label, multiplier in (('base', 1.0), ('stress_1_5x', 1.5)):
            result = X.run_side(frames, side, cost_multiplier=multiplier, funding=funding,
                                start_ms=_utc_ms(FORWARD[0][1]), end_ms=END_MS)
            folds = [_slice(result, *spec) for spec in FORWARD]
            all_trades = [trade for fold in folds for trade in fold['trades']]
            runs[f'{side}.{label}'] = {
                'side': side, 'cost_multiplier': multiplier, 'folds': folds,
                'forward_summary': _summary(all_trades, _utc_ms(FORWARD[0][1]), END_MS),
            }
    runner = Path(__file__).resolve()
    packet = {
        'format': FORMAT,
        'candidate': X.RULE_ID,
        'contract_commit': X.CONTRACT_COMMIT,
        'research_only': True,
        'non_promotable': True,
        'git': {'head': head, 'clean': True},
        'window': {'start': START, 'end_exclusive': END, 'holdout_read': False},
        'universe': {'id': 'CORE-10-ELIGIBLE-9', 'symbols': list(X.CORE_10_ELIGIBLE_9),
                     'minimum_complete_per_decision': X.MIN_ELIGIBLE},
        'train': {'name': 'train', 'start': SLICES[0][1], 'end_exclusive': SLICES[0][2],
                  'purpose': 'warm-up, schema and feasibility only; not a result fold',
                  'integrity': _integrity(frames, _utc_ms(SLICES[0][1]), _utc_ms(SLICES[0][2]))},
        'inputs': {'candles': candle_prov, 'funding': funding_prov},
        'runner': {'path': runner.name, 'sha256': _sha(runner),
                   'engine_path': Path(X.__file__).name, 'engine_sha256': _sha(Path(X.__file__).resolve())},
        'funding_boundary': '(entry_ms, exit_ms]',
        'runs': runs,
    }
    raw = json.dumps(packet, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    packet['payload_sha256'] = hashlib.sha256(raw).hexdigest()
    return packet


def run(candle_manifest, funding_manifest, repo, out) -> dict:
    out = Path(out).resolve()
    if out.exists():
        raise RunnerError(f'output is write-once and already exists: {out}')
    packet = build_packet(candle_manifest, funding_manifest, repo)
    raw = (json.dumps(packet, sort_keys=True, indent=2, allow_nan=False) + '\n').encode()
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise RunnerError(f'output is write-once and already exists: {out}') from None
    with os.fdopen(fd, 'wb') as f:
        f.write(raw)
    return packet


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--candles-manifest', required=True)
    p.add_argument('--funding-manifest', required=True)
    p.add_argument('--repo', required=True)
    p.add_argument('--out', required=True)
    args = p.parse_args(argv)
    run(args.candles_manifest, args.funding_manifest, args.repo, args.out)


if __name__ == '__main__':
    main()
