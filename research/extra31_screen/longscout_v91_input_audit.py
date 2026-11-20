"""Audit own copied batch data; writes only <output>/input_audit.json.

Usage: python longscout_v91_input_audit.py <fresh_dir> <extra_root> <output>
"""
import hashlib
import json
import sys
from pathlib import Path


def main():
    fresh, extra, out = map(Path, sys.argv[1:])
    root = Path(__file__).resolve().parents[2]
    sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    assert sha(fresh/'input_manifest.json') == 'fb0c0572b6c2bc5f569c64d89bd0ee2ca1dd9e3cf96b1ed8bd1110c341ed2f3e'
    assert sha(extra/'input_manifest.json') == '99af9bfb4a9b29ede80f8f3ff54541912d74d03b3b93d984432832c934bdfa95'
    fm = json.loads((fresh/'input_manifest.json').read_text())
    em = json.loads((extra/'input_manifest.json').read_text())
    extra_coins = json.loads((Path(__file__).parent/'contract.json').read_text())['universe']['extra31']
    coins = sorted(set(fm['symbols']) | set(extra_coins))
    copied = {}
    for sym in coins:
        expected = fm['symbols'][sym]['sha256'] if sym in fm['symbols'] else em['symbols'][sym]['csv_sha256']
        got = sha(out/'_data'/f'{sym}_1h.csv')
        assert got == expected, sym
        copied[sym] = got
    rules_path = root/'data/exchange_rules_testnet.json'
    rules = json.loads(rules_path.read_text())['symbols']
    report = dict(copied_files=copied, all_hashes_match=True,
                  fresh_manifest_sha=sha(fresh/'input_manifest.json'), extra_manifest_sha=sha(extra/'input_manifest.json'),
                  missing_exchange_rules=sorted(set(coins)-set(rules)), exchange_rules_sha=sha(rules_path),
                  snapshot_is_historical_point_in_time=False,
                  note='One frozen testnet exchange-rule snapshot; not a point-in-time historical rules series. Fixed native funding, not realized per-symbol rates.')
    (out/'input_audit.json').write_text(json.dumps(report, indent=1)+'\n', encoding='utf-8')
    print(f'Verified {len(copied)} copied CSVs')


if __name__ == '__main__':
    main()
