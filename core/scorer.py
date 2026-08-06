"""Threat scorer: augments SpanFinding.score with pattern matching.

Score composition:
  structural_weight (0.0–1.0)  — from glyph_px, CR, ΔE signals
  pattern_weight   (0.0–0.3)  — from injection keyword matches

Structural signals carry higher weight because they are hard to
evade.  Keyword patterns are a weak signal (easily bypassed by
rewording) and never raise score above 0.3 on their own.

Patterns are loaded from text files under core/patterns/.
Adding patterns requires editing those files, not code.
"""

from __future__ import annotations

from pathlib import Path

from .models import DetectorConfig, SpanFinding, Verdict

_PATTERNS_DIR = Path(__file__).resolve().parent / "patterns"


def load_patterns(directory: Path | None = None) -> list[str]:
    """Load all injection patterns from .txt files in *directory*.

    Returns lowercased pattern strings.  Empty list if no files exist.
    """
    d = directory or _PATTERNS_DIR
    patterns: list[str] = []
    if not d.is_dir():
        return patterns
    for txt_file in sorted(d.glob("*.txt")):
        for line in txt_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                patterns.append(line.lower())
    return patterns


def score_findings(
    findings: list[SpanFinding],
    config: DetectorConfig | None = None,
    patterns: list[str] | None = None,
) -> None:
    """Update ``score`` on each finding in place.

    Score = max(structural, structural + pattern_boost).
    Capped at 1.0.  Pattern-only score capped at 0.3.
    """
    cfg = config or DetectorConfig()
    pats = patterns if patterns is not None else load_patterns()
    for sf in findings:
        structural = _structural_score(sf, cfg)
        pattern = _pattern_score(sf.text, pats)
        sf.score = min(1.0, structural + pattern)


def _structural_score(sf: SpanFinding, cfg: DetectorConfig) -> float:
    """Score from render-diff signals (0.0–1.0)."""
    if sf.verdict == Verdict.HIDDEN:
        return 1.0
    if sf.verdict == Verdict.SUSPICIOUS:
        return sf.score  # already set by detector._suspicious_score
    return 0.0


def _pattern_score(text: str, patterns: list[str]) -> float:
    """Score boost from injection keyword matching (0.0–0.3).

    Returns 0.3 if any pattern matches, 0.0 otherwise.
    A single match is sufficient — multiple matches do not stack.
    """
    if not patterns:
        return 0.0
    lower = text.lower()
    for pat in patterns:
        if pat in lower:
            return 0.3
    return 0.0
