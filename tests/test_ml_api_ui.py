"""ML/API header indicators stay explicit and separate from the entry-review filter."""
import os


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_header_has_distinct_ml_and_api_status_indicators():
    html = open(os.path.join(ROOT, 'panel.html'), encoding='utf-8').read()
    assert 'id="mlstate"' in html and 'id="apistate"' in html
    assert "!!S.CLAUDE_SHADOW_ANALYST" in html
    assert "!!D.ai_key" in html
    assert "ML '+(ml?'ON':'OFF')" in html
    assert "API '+(api?'ON':'OFF')" in html


def test_ml_indicator_explains_that_entry_filter_is_separate():
    html = open(os.path.join(ROOT, 'panel.html'), encoding='utf-8').read()
    assert 'the entry-review filter is separate' in html
    assert "D.mode==='PAPER'" in html
