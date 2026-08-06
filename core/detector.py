"""Single entry point for hidden text detection.

Combines render-diff measurement with CIEDE2000 color distance to
classify each text span as hidden, suspicious, or normal.

Threshold defaults are derived from the 30-paper arXiv distribution:
  - Hidden corpus max ΔE₀₀ = 6.74
  - Nearest normal span ΔE₀₀ = 12.03
  - Gap = 5.29 (see results/DISTRIBUTION_2D.md §6)
"""

from __future__ import annotations

import time
from pathlib import Path

import pymupdf

from .models import DetectorConfig, SpanFinding, Verdict
from .render_diff import (
    _extract_spans,
    _measure_span,
    _open_textless,
    _render_page,
)


def scan_document(
    doc_path: str | Path,
    config: DetectorConfig | None = None,
) -> tuple[list[SpanFinding], int, list[float]]:
    """Scan all pages of a PDF for hidden text.

    Returns ``(findings, page_count, page_times)`` where *findings*
    is one :class:`SpanFinding` per text span with verdict and score
    assigned, and *page_times* is per-page elapsed seconds.
    """
    cfg = config or DetectorConfig()
    path = str(doc_path)
    doc_w = pymupdf.open(path)
    doc_wo = _open_textless(path)
    scale = cfg.dpi / 72.0

    findings: list[SpanFinding] = []
    page_times: list[float] = []

    for pn in range(len(doc_w)):
        t0 = time.monotonic()
        page_findings = _scan_page(
            doc_w[pn], doc_wo[pn], scale, pn, cfg,
        )
        page_times.append(round(time.monotonic() - t0, 4))
        findings.extend(page_findings)

    page_count = len(doc_w)
    doc_w.close()
    doc_wo.close()
    return findings, page_count, page_times


def _scan_page(
    page_w: pymupdf.Page,
    page_wo: pymupdf.Page,
    scale: float,
    page_num: int,
    cfg: DetectorConfig,
) -> list[SpanFinding]:
    """Measure and classify all spans on a single page.

    Skips rendering if the page has no non-whitespace text spans.
    """
    spans = _extract_spans(page_w)
    text_spans = [s for s in spans if s.get("text", "").strip()]
    if not text_spans:
        return []
    img_w = _render_page(page_w, cfg.dpi)
    img_wo = _render_page(page_wo, cfg.dpi)
    # CR-first gate: skip expensive ΔE for spans with CR >= cr_suspicious.
    # >99% of spans in typical documents are high-contrast and exit early.
    cr_gate = cfg.cr_suspicious
    findings: list[SpanFinding] = []
    for span in text_spans:
        sf = _measure_span(
            span, img_w, img_wo, scale, page_num, cfg.core_threshold,
            cr_gate=cr_gate,
        )
        _classify(sf, cfg)
        findings.append(sf)
    return findings


def _classify(sf: SpanFinding, cfg: DetectorConfig) -> None:
    """Assign verdict and score to a SpanFinding in place.

    Classification rules (order matters):
      1. glyph_px == 0  → HIDDEN  (invisible render mode / Tr=3)
      2. CR < cr_hidden AND ΔE < delta_e_hidden  → HIDDEN (color-matched)
      3. CR < cr_suspicious AND ΔE < delta_e_suspicious → SUSPICIOUS
      4. Otherwise → NORMAL
    """
    if sf.glyph_px == 0:
        sf.verdict = Verdict.HIDDEN
        sf.score = 1.0
        sf.reason = "invisible_render_mode (glyph_px=0)"
        return

    cr, de = sf.cr, sf.delta_e
    if cr is None or de is None:
        return  # keep defaults (NORMAL, 0.0)

    if cr < cfg.cr_hidden and de < cfg.delta_e_hidden:
        sf.verdict = Verdict.HIDDEN
        sf.score = 1.0
        sf.reason = f"color_matched (CR={cr:.2f}, ΔE={de:.2f})"
    elif cr < cfg.cr_suspicious and de < cfg.delta_e_suspicious:
        sf.verdict = Verdict.SUSPICIOUS
        sf.score = _suspicious_score(cr, de, cfg)
        sf.reason = f"low_contrast (CR={cr:.2f}, ΔE={de:.2f})"


def _suspicious_score(
    cr: float, de: float, cfg: DetectorConfig,
) -> float:
    """Score for spans in the suspicious zone (0.0 – 0.9).

    Linear interpolation: closer to hidden threshold → higher score.
    """
    cr_range = cfg.cr_suspicious - cfg.cr_hidden
    de_range = cfg.delta_e_suspicious - cfg.delta_e_hidden
    cr_ratio = max(0.0, 1.0 - (cr - cfg.cr_hidden) / cr_range) if cr_range > 0 else 0.0
    de_ratio = max(0.0, 1.0 - (de - cfg.delta_e_hidden) / de_range) if de_range > 0 else 0.0
    return round(min(cr_ratio, de_ratio) * 0.9, 3)
