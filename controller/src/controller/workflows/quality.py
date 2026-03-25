"""Quality checks for workflow step outputs.

Runs as part of the aggregate step to validate results before merging.
All checks are deterministic — no LLM reasoning.
"""

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class QualityReport:
    """Result of quality checks on a dataset."""

    total_items: int = 0
    valid_items: int = 0
    checks: dict = field(default_factory=dict)  # check_name -> {passed, score, details}
    score: float = 0.0  # 0.0-1.0 composite score
    warnings: list[str] = field(default_factory=list)


class QualityChecker:
    """Run quality checks on workflow results."""

    def check(self, data: list[dict], schema: dict | None = None) -> QualityReport:
        """Run all quality checks on a dataset.

        Checks:
        1. Schema compliance — required fields present
        2. Completeness — non-empty required fields
        3. Freshness — dates in future (for events)
        4. URL validity — source_url looks like a URL
        5. Dedup rate — percentage of duplicates found
        6. Source diversity — how many unique sources
        """
        report = QualityReport(total_items=len(data))
        if not data:
            report.score = 0.0
            report.warnings.append("Empty dataset")
            return report

        scores = []

        # 1. Schema compliance
        schema_result = self._check_schema(data, schema)
        report.checks["schema_compliance"] = schema_result
        scores.append(schema_result["score"])

        # 2. Completeness
        completeness_result = self._check_completeness(data)
        report.checks["completeness"] = completeness_result
        scores.append(completeness_result["score"])

        # 3. Freshness (if dates present)
        freshness_result = self._check_freshness(data)
        report.checks["freshness"] = freshness_result
        if freshness_result["applicable"]:
            scores.append(freshness_result["score"])

        # 4. URL validity
        url_result = self._check_urls(data)
        report.checks["url_validity"] = url_result
        if url_result["applicable"]:
            scores.append(url_result["score"])

        # 5. Dedup rate
        dedup_result = self._check_dedup_rate(data)
        report.checks["dedup_rate"] = dedup_result
        scores.append(dedup_result["score"])

        # 6. Source diversity
        diversity_result = self._check_source_diversity(data)
        report.checks["source_diversity"] = diversity_result
        if diversity_result["applicable"]:
            scores.append(diversity_result["score"])

        # Composite score
        report.score = sum(scores) / len(scores) if scores else 0.0
        report.valid_items = sum(
            1 for item in data if self._is_valid_item(item, schema)
        )

        # Add warnings
        if report.score < 0.5:
            report.warnings.append(f"Low quality score: {report.score:.2f}")
        if dedup_result["duplicate_count"] > len(data) * 0.5:
            report.warnings.append(
                f"High duplicate rate: {dedup_result['duplicate_count']}/{len(data)}"
            )

        return report

    def _check_schema(
        self, data: list, schema: dict | None
    ) -> dict:
        if not schema or "properties" not in schema:
            return {"passed": True, "score": 1.0, "details": "No schema to validate"}
        required = schema.get("required", [])
        if not required:
            required = list(schema.get("properties", {}).keys())

        compliant = sum(
            1
            for item in data
            if isinstance(item, dict) and all(k in item for k in required)
        )
        score = compliant / len(data) if data else 0
        return {
            "passed": score >= 0.8,
            "score": score,
            "details": f"{compliant}/{len(data)} items have all required fields",
        }

    def _check_completeness(self, data: list) -> dict:
        if not data:
            return {"passed": False, "score": 0.0, "details": "No data"}
        non_empty = 0
        for item in data:
            if isinstance(item, dict):
                values = [v for v in item.values() if v is not None and str(v).strip()]
                if len(values) >= len(item) * 0.5:
                    non_empty += 1
            elif item:
                non_empty += 1
        score = non_empty / len(data)
        return {
            "passed": score >= 0.7,
            "score": score,
            "details": f"{non_empty}/{len(data)} items have >50% fields filled",
        }

    def _check_freshness(self, data: list) -> dict:
        dates = []
        for item in data:
            if isinstance(item, dict):
                for key in ("date", "start_date", "event_date"):
                    if key in item and item[key]:
                        dates.append(item[key])

        if not dates:
            return {
                "applicable": False,
                "passed": True,
                "score": 1.0,
                "details": "No date fields found",
            }

        now = datetime.now().strftime("%Y-%m-%d")
        future = sum(1 for d in dates if str(d) >= now)
        score = future / len(dates) if dates else 0
        return {
            "applicable": True,
            "passed": score >= 0.5,
            "score": score,
            "details": f"{future}/{len(dates)} dates are in the future",
        }

    def _check_urls(self, data: list) -> dict:
        urls = []
        for item in data:
            if isinstance(item, dict):
                for key in ("url", "source_url", "link"):
                    if key in item and item[key]:
                        urls.append(item[key])

        if not urls:
            return {
                "applicable": False,
                "passed": True,
                "score": 1.0,
                "details": "No URL fields",
            }

        url_pattern = re.compile(r"^https?://")
        valid = sum(1 for u in urls if url_pattern.match(str(u)))
        score = valid / len(urls)
        return {
            "applicable": True,
            "passed": score >= 0.8,
            "score": score,
            "details": f"{valid}/{len(urls)} URLs valid",
        }

    def _check_dedup_rate(self, data: list) -> dict:
        if not data:
            return {
                "passed": True,
                "score": 1.0,
                "duplicate_count": 0,
                "details": "No data",
            }
        seen: set[str] = set()
        dupes = 0
        for item in data:
            sig = json.dumps(item, sort_keys=True) if isinstance(item, dict) else str(item)
            if sig in seen:
                dupes += 1
            seen.add(sig)
        score = 1.0 - (dupes / len(data))
        return {
            "passed": dupes == 0,
            "score": score,
            "duplicate_count": dupes,
            "details": f"{dupes} duplicates in {len(data)} items",
        }

    def _check_source_diversity(self, data: list) -> dict:
        sources: set[str] = set()
        for item in data:
            if isinstance(item, dict):
                for key in ("source", "source_url", "provider"):
                    if key in item and item[key]:
                        sources.add(str(item[key]))

        if not sources:
            return {
                "applicable": False,
                "passed": True,
                "score": 1.0,
                "details": "No source fields",
            }

        score = min(len(sources) / 3.0, 1.0)  # 3+ sources = perfect score
        return {
            "applicable": True,
            "passed": len(sources) >= 2,
            "score": score,
            "details": f"{len(sources)} unique sources",
        }

    def _is_valid_item(self, item: object, schema: dict | None) -> bool:
        if not isinstance(item, dict):
            return bool(item)
        if schema and "required" in schema:
            return all(k in item for k in schema["required"])
        return len(item) > 0
