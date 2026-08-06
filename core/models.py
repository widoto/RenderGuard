"""Public data types for the visual sanitizer detector."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Verdict(str, Enum):
    """Per-span classification."""

    HIDDEN = "hidden"
    SUSPICIOUS = "suspicious"
    NORMAL = "normal"


class PolicyDecision(str, Enum):
    """Document-level policy outcome."""

    BLOCK = "block"
    WARN = "warn"
    PASS = "pass"


@dataclass
class SpanFinding:
    """Measurement and classification for a single text span.

    Fields page..technique are set by the measurement engine.
    Fields score..reason are set by the detector / scorer.
    """

    page: int
    bbox: tuple[float, float, float, float]
    text: str
    cr: float | None
    delta_e: float | None
    glyph_px: int
    technique: str  # "invisible_render_mode" | "measured" | "empty_bbox"
    score: float = 0.0
    verdict: Verdict = Verdict.NORMAL
    reason: str = ""


@dataclass
class DetectorConfig:
    """Configurable detection thresholds.

    Defaults derived from 30-paper arXiv distribution (6 categories, 259k spans).
    Hidden corpus max ΔE₀₀ = 6.74, nearest normal ΔE₀₀ = 12.03, gap = 5.29.
    See results/DISTRIBUTION_2D.md §6 for full derivation.
    """

    # Hidden thresholds: span is HIDDEN when CR < cr_hidden AND ΔE < delta_e_hidden
    cr_hidden: float = 3.0
    delta_e_hidden: float = 10.0
    # Suspicious thresholds: SUSPICIOUS when below these but above hidden
    cr_suspicious: float = 4.5
    delta_e_suspicious: float = 20.0
    # Render settings
    dpi: int = 150
    core_threshold: float = 0.9
    min_glyph_px: int = 5


@dataclass
class ScanResult:
    """Aggregate result of scanning a document."""

    findings: list[SpanFinding]
    decision: PolicyDecision
    page_count: int
    page_times: list[float]
    hidden_count: int = 0
    suspicious_count: int = 0
    summary: str = ""
