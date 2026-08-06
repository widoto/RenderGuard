"""visual_sanitizer core detection library.

Public API:
    scan_document  — measure + classify all spans in a PDF
    evaluate       — aggregate findings into a document-level decision
    SpanFinding    — per-span measurement and classification
    DetectorConfig — configurable detection thresholds
    PolicyConfig   — policy behaviour switches
    ScanResult     — document-level result
    Verdict        — HIDDEN | SUSPICIOUS | NORMAL
    PolicyDecision — BLOCK | WARN | PASS
"""

from .detector import scan_document
from .models import (
    DetectorConfig,
    PolicyDecision,
    ScanResult,
    SpanFinding,
    Verdict,
)
from .policy import PolicyConfig, evaluate
from .scorer import load_patterns, score_findings

__all__ = [
    "scan_document",
    "score_findings",
    "load_patterns",
    "evaluate",
    "SpanFinding",
    "DetectorConfig",
    "PolicyConfig",
    "ScanResult",
    "Verdict",
    "PolicyDecision",
]
