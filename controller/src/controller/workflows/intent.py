"""Async intent classifier for workflow template matching.

Classifies user requests into {template_slug, parameters, confidence}.
Uses LLM for classification with rule-based fallback.
Runs as an async worker, not inline in the request path.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class IntentResult:
    template_slug: str | None = None
    parameters: dict = field(default_factory=dict)
    confidence: float = 0.0
    method: str = "none"  # "llm" | "rules" | "none"


class IntentClassifier:
    """Classify user requests into workflow template + parameters."""

    def __init__(
        self,
        template_slugs: list[str] | None = None,
        confidence_threshold: float = 0.7,
        max_input_chars: int = 2000,
    ):
        self._template_slugs = template_slugs or []
        self._confidence_threshold = confidence_threshold
        self._max_input_chars = max_input_chars

    def sanitize_input(self, text: str) -> str:
        """Sanitize user input before LLM classification.

        - Strip XML/HTML tags
        - Truncate to max_input_chars
        - Remove common prompt injection markers
        """
        # Strip XML/HTML tags
        text = re.sub(r"<[^>]+>", "", text)
        # Remove common injection patterns
        for marker in [
            "[INST]",
            "[/INST]",
            "<<SYS>>",
            "<</SYS>>",
            "SYSTEM:",
            "Human:",
            "Assistant:",
        ]:
            text = text.replace(marker, "")
        # Truncate
        return text[: self._max_input_chars].strip()

    async def classify(self, user_request: str) -> IntentResult:
        """Classify a user request into a template + parameters.

        Strategy:
        1. Sanitize input
        2. Try rule-based matching first (fast, deterministic)
        3. If no match and LLM available, try LLM classification
        4. Apply confidence threshold
        """
        sanitized = self.sanitize_input(user_request)
        if not sanitized:
            return IntentResult()

        # Try rule-based first
        result = self._classify_by_rules(sanitized)
        if result.confidence >= self._confidence_threshold:
            return result

        # LLM classification would go here (Phase 3+)
        # For now, return the rule-based result even if below threshold
        return result

    def _classify_by_rules(self, text: str) -> IntentResult:
        """Simple keyword-based classification.

        Matches patterns to known templates.
        """
        text_lower = text.lower()

        # geo-search pattern
        geo_keywords = ["event", "events", "happening", "happenings", "activities"]
        location_pattern = re.compile(
            r"(?:in|around|near|at)\s+([A-Z][a-zA-Z\s,]+?)(?:\s*(?:area|metro|region|\.|$))",
            re.IGNORECASE,
        )
        source_keywords = {
            "google": ["google"],
            "eventbrite": ["eventbrite"],
            "meetup": ["meetup"],
            "facebook": ["facebook", "fb events"],
        }

        if any(kw in text_lower for kw in geo_keywords):
            # Extract regions
            regions: list[str] = []
            for match in location_pattern.finditer(text):
                region = match.group(1).strip().rstrip(",")
                # Split on commas (optionally followed by "and") and standalone "and"
                parts = re.split(r"\s*,\s*(?:and\s+)?|\s+and\s+", region)
                regions.extend([r.strip() for r in parts if r.strip()])

            if not regions:
                # Try to find any capitalized proper nouns after location prepositions
                words = text.split()
                for i, w in enumerate(words):
                    if w.lower() in ("in", "around", "near") and i + 1 < len(words):
                        regions.append(words[i + 1].strip(",."))

            # Extract sources
            sources: list[str] = []
            for source, keywords in source_keywords.items():
                if any(kw in text_lower for kw in keywords):
                    sources.append(source)
            if not sources:
                sources = ["google"]  # default source

            # Extract query (what kind of events)
            query = "events"  # default
            for kw in geo_keywords:
                idx = text_lower.find(kw)
                if idx > 0:
                    # Look for adjectives before the keyword
                    prefix = text[:idx].strip().split()[-3:]  # last 3 words before keyword
                    if prefix:
                        query = " ".join(prefix + [kw])
                    break

            if regions and "geo-search" in self._template_slugs:
                return IntentResult(
                    template_slug="geo-search",
                    parameters={"query": query, "regions": regions, "sources": sources},
                    confidence=0.8 if len(regions) >= 1 else 0.5,
                    method="rules",
                )

        # multi-source-analysis pattern
        research_keywords = ["research", "analyze", "investigate", "compare", "study"]
        if any(kw in text_lower for kw in research_keywords):
            if "multi-source-analysis" in self._template_slugs:
                # Extract topic (everything after the keyword)
                topic = text  # simplified
                return IntentResult(
                    template_slug="multi-source-analysis",
                    parameters={"topic": topic, "sources": ["web"]},
                    confidence=0.6,
                    method="rules",
                )

        # No match
        return IntentResult(confidence=0.0, method="rules")

    def update_templates(self, slugs: list[str]) -> None:
        """Update the list of available template slugs."""
        self._template_slugs = slugs
