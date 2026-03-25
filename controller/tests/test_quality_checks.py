"""Tests for the QualityChecker module.

All checks are deterministic — no LLM reasoning, no network calls.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from controller.workflows.quality import QualityChecker, QualityReport


@pytest.fixture
def checker():
    return QualityChecker()


# ---------------------------------------------------------------------------
# 1. Schema compliance
# ---------------------------------------------------------------------------


def test_schema_compliance_all_valid(checker):
    """All items satisfy the schema's required fields."""
    schema = {
        "properties": {"name": {}, "url": {}},
        "required": ["name", "url"],
    }
    data = [
        {"name": "Event A", "url": "https://a.com"},
        {"name": "Event B", "url": "https://b.com"},
    ]
    report = checker.check(data, schema)
    assert report.checks["schema_compliance"]["passed"] is True
    assert report.checks["schema_compliance"]["score"] == 1.0


def test_schema_compliance_missing_fields(checker):
    """Some items are missing required fields."""
    schema = {
        "properties": {"name": {}, "url": {}},
        "required": ["name", "url"],
    }
    data = [
        {"name": "Event A", "url": "https://a.com"},
        {"name": "Event B"},  # missing url
        {"url": "https://c.com"},  # missing name
    ]
    report = checker.check(data, schema)
    sc = report.checks["schema_compliance"]
    assert sc["score"] == pytest.approx(1 / 3)
    assert sc["passed"] is False


# ---------------------------------------------------------------------------
# 2. Completeness
# ---------------------------------------------------------------------------


def test_completeness_check(checker):
    """Items with >50% non-empty fields are counted as complete."""
    data = [
        {"name": "A", "url": "https://a.com", "desc": "good"},
        {"name": "", "url": None, "desc": ""},  # all empty-ish
        {"name": "C", "url": "https://c.com", "desc": ""},
    ]
    report = checker.check(data)
    comp = report.checks["completeness"]
    # Item 0: 3/3 filled -> complete
    # Item 1: 0/3 filled -> incomplete
    # Item 2: 2/3 filled -> complete
    assert comp["score"] == pytest.approx(2 / 3)


# ---------------------------------------------------------------------------
# 3. Freshness
# ---------------------------------------------------------------------------


def test_freshness_future_dates(checker):
    """All dates in the future -> score 1.0."""
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    data = [
        {"name": "A", "date": tomorrow},
        {"name": "B", "date": tomorrow},
    ]
    report = checker.check(data)
    fr = report.checks["freshness"]
    assert fr["applicable"] is True
    assert fr["score"] == 1.0
    assert fr["passed"] is True


def test_freshness_past_dates(checker):
    """All dates in the past -> score 0.0."""
    yesterday = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")
    data = [
        {"name": "A", "date": yesterday},
        {"name": "B", "date": yesterday},
    ]
    report = checker.check(data)
    fr = report.checks["freshness"]
    assert fr["applicable"] is True
    assert fr["score"] == 0.0
    assert fr["passed"] is False


# ---------------------------------------------------------------------------
# 4. URL validity
# ---------------------------------------------------------------------------


def test_url_validity(checker):
    """Valid URLs start with http:// or https://."""
    data = [
        {"name": "A", "url": "https://example.com"},
        {"name": "B", "url": "http://example.com"},
        {"name": "C", "url": "not-a-url"},
        {"name": "D", "url": "ftp://example.com"},
    ]
    report = checker.check(data)
    uv = report.checks["url_validity"]
    assert uv["applicable"] is True
    assert uv["score"] == pytest.approx(2 / 4)
    assert uv["passed"] is False  # < 0.8


# ---------------------------------------------------------------------------
# 5. Dedup rate
# ---------------------------------------------------------------------------


def test_dedup_rate_no_dupes(checker):
    """No duplicates -> score 1.0."""
    data = [
        {"name": "A"},
        {"name": "B"},
        {"name": "C"},
    ]
    report = checker.check(data)
    dr = report.checks["dedup_rate"]
    assert dr["passed"] is True
    assert dr["score"] == 1.0
    assert dr["duplicate_count"] == 0


def test_dedup_rate_with_dupes(checker):
    """Duplicates detected -> score < 1.0."""
    data = [
        {"name": "A"},
        {"name": "A"},
        {"name": "B"},
        {"name": "A"},
    ]
    report = checker.check(data)
    dr = report.checks["dedup_rate"]
    assert dr["passed"] is False
    assert dr["duplicate_count"] == 2
    assert dr["score"] == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# 6. Source diversity
# ---------------------------------------------------------------------------


def test_source_diversity(checker):
    """3+ unique sources -> perfect score."""
    data = [
        {"name": "A", "source": "google"},
        {"name": "B", "source": "bing"},
        {"name": "C", "source": "duckduckgo"},
    ]
    report = checker.check(data)
    sd = report.checks["source_diversity"]
    assert sd["applicable"] is True
    assert sd["score"] == 1.0
    assert sd["passed"] is True


# ---------------------------------------------------------------------------
# 7. Composite score
# ---------------------------------------------------------------------------


def test_composite_score(checker):
    """Composite score is average of applicable check scores."""
    data = [
        {"name": "A"},
        {"name": "B"},
    ]
    report = checker.check(data)
    # No dates, no URLs, no sources -> only schema(1.0), completeness(1.0), dedup(1.0)
    assert report.score == pytest.approx(1.0)
    assert report.valid_items == 2


# ---------------------------------------------------------------------------
# 8. Edge cases
# ---------------------------------------------------------------------------


def test_empty_dataset(checker):
    """Empty dataset returns score 0 with warning."""
    report = checker.check([])
    assert report.score == 0.0
    assert report.total_items == 0
    assert "Empty dataset" in report.warnings


def test_quality_warnings(checker):
    """Low quality triggers warnings."""
    # All duplicates -> dedup score near 0, completeness low
    data = [
        {"name": ""},
        {"name": ""},
        {"name": ""},
        {"name": ""},
    ]
    report = checker.check(data)
    # Dedup: 3 dupes out of 4 -> score 0.25
    # Completeness: all items have 1 field with empty value -> 0/4 -> 0.0
    # High duplicate rate warning should fire (3 > 4*0.5)
    assert any("duplicate" in w.lower() for w in report.warnings)
