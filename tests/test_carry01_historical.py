import hashlib
import json

import pytest

from tools.research import carry01_historical as S


def test_module_import_has_no_embedded_data_root_or_result(tmp_path):
    source = S.Path(S.__file__).read_text(encoding="utf-8")
    assert "ZackBot_carry01_data" not in source
    assert "historical_gate\": {" not in source


def test_write_report_once_is_checksum_bound_and_refuses_overwrite(tmp_path):
    output = tmp_path / "report.json"
    report = {"format": "test", "verdict": "FAIL"}
    digest = S.write_report_once(report, output)
    assert digest == hashlib.sha256(output.read_bytes()).hexdigest()
    assert output.with_name("report.json.sha256").read_text().strip() == digest
    assert json.loads(output.read_text()) == report
    with pytest.raises(FileExistsError):
        S.write_report_once(report, output)


def test_usdc_overlap_never_extrapolates_before_signed_start():
    windows = [["2023-01-01T00:00:00Z", "2024-01-01T00:00:00Z"],
               ["2024-01-01T00:00:00Z", "2025-01-01T00:00:00Z"]]
    start = S._timestamp_ms("2024-01-03T00:00:00Z")
    overlaps = S._overlap_windows(windows, start)
    assert len(overlaps) == 1
    assert overlaps[0][0] == "2024-01-03T00:00:00Z"
