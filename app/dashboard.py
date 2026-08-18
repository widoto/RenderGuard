"""Streamlit PDF Security Dashboard.

Single-file demo that imports the frozen core detection pipeline
and presents results with interactive visualizations.

Usage:
    streamlit run app/dashboard.py
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

import numpy as np
import pymupdf
import streamlit as st

# Ensure project root is importable
_project_root = str(Path(__file__).resolve().parents[1])
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from core import (
    DetectorConfig,
    PolicyDecision,
    ScanResult,
    SpanFinding,
    Verdict,
    evaluate,
    load_patterns,
    scan_document,
    score_findings,
)
from PIL import Image, ImageDraw
from core.render_diff import _build_glyph_mask, _open_textless, _pixel_rect, _render_page

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_DPI = 150
_SCALE = _DPI / 72.0
_BBOX_WIDTH = 3  # pixels
_COLOR_HIDDEN = (255, 40, 40)       # red
_COLOR_SUSPICIOUS = (255, 165, 0)   # orange
_TEXT_PREVIEW_LEN = 20
_COLOR_GLYPH_HIGHLIGHT = (255, 255, 0)  # yellow for glyph mask pixels
_CORE_THRESHOLD = 0.9

st.set_page_config(page_title="RenderGuard", layout="wide")


# ---------------------------------------------------------------------------
# Drawing helpers
# ---------------------------------------------------------------------------
def _draw_bbox(
    img: np.ndarray,
    rect: tuple[int, int, int, int],
    color: tuple[int, int, int],
    width: int = _BBOX_WIDTH,
) -> None:
    """Draw a colored rectangle border on a numpy RGB image (in-place)."""
    x0, y0, x1, y1 = rect
    h, w = img.shape[:2]
    # Clamp
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(w, x1), min(h, y1)
    if x1 <= x0 or y1 <= y0:
        return
    c = np.array(color, dtype=np.uint8)
    # Top / bottom
    img[y0 : min(y0 + width, y1), x0:x1] = c
    img[max(y1 - width, y0) : y1, x0:x1] = c
    # Left / right
    img[y0:y1, x0 : min(x0 + width, x1)] = c
    img[y0:y1, max(x1 - width, x0) : x1] = c


def _color_for_verdict(verdict: Verdict) -> tuple[int, int, int]:
    if verdict == Verdict.HIDDEN:
        return _COLOR_HIDDEN
    return _COLOR_SUSPICIOUS


def _mask_text(text: str) -> str:
    if len(text) <= _TEXT_PREVIEW_LEN:
        return text
    return text[:_TEXT_PREVIEW_LEN] + "\u2026"


def _draw_labels(
    img: np.ndarray,
    labels: list[tuple[tuple[int, int, int, int], str]],
    color: tuple[int, int, int],
) -> np.ndarray:
    """Draw text labels on image via PIL. Returns modified array."""
    pil_img = Image.fromarray(img)
    draw = ImageDraw.Draw(pil_img)
    for rect, text in labels:
        x0, y0, _x1, _y1 = rect
        label_h = 14
        label_y = y0 - label_h - 2 if y0 > label_h + 2 else y0 + 2
        tw = len(text) * 7 + 4
        draw.rectangle([x0, label_y, x0 + tw, label_y + label_h], fill=color)
        draw.text((x0 + 2, label_y + 1), text, fill=(255, 255, 255))
    return np.array(pil_img)


def _defang_text(text: str) -> str:
    """Insert spaces between characters to prevent direct copy-paste reuse.

    The payload remains human-readable but cannot be pasted verbatim
    into an LLM prompt or system without manual reconstruction.
    """
    return " ".join(text)


# ---------------------------------------------------------------------------
# Scanning
# ---------------------------------------------------------------------------
def _run_scan(path: str) -> tuple[ScanResult, float]:
    """Run the full detection pipeline. Returns (result, elapsed_seconds)."""
    t0 = time.perf_counter()
    findings, page_count, page_times = scan_document(path)
    score_findings(findings)
    result = evaluate(findings, page_count, page_times)
    elapsed = time.perf_counter() - t0
    return result, elapsed


# ---------------------------------------------------------------------------
# UI sections
# ---------------------------------------------------------------------------
def _section_banner(result: ScanResult, elapsed: float) -> None:
    """Summary banner + metric cards."""
    hidden = result.hidden_count
    suspicious = result.suspicious_count
    total_spans = len(result.findings)

    if result.decision == PolicyDecision.PASS:
        st.success(
            f"PASS \u2014 Scanned {total_spans} text spans across "
            f"{result.page_count} page(s) in {elapsed:.1f}s. "
            f"No hidden or suspicious text detected."
        )
    elif result.decision == PolicyDecision.WARN:
        st.warning(
            f"WARN \u2014 {suspicious} suspicious span(s) out of "
            f"{total_spans} spans across {result.page_count} page(s)"
        )
    else:
        st.error(
            f"BLOCK \u2014 {hidden} hidden text span(s) detected "
            f"out of {total_spans} spans across {result.page_count} page(s)"
        )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Hidden", hidden)
    c2.metric("Suspicious", suspicious)
    c3.metric("Pages", result.page_count)
    c4.metric("Scan Time", f"{elapsed:.1f}s")


def _section_keyword_alert(findings: list[SpanFinding]) -> None:
    """Show injection keyword matches if any."""
    patterns = load_patterns()
    if not patterns:
        return

    flagged = []
    for f in findings:
        if f.verdict == Verdict.NORMAL:
            continue
        text_lower = f.text.lower()
        for pat in patterns:
            if pat in text_lower:
                flagged.append(
                    {"Page": f.page + 1, "Score": f"{f.score:.2f}", "Keyword": pat}
                )
                break  # one match per finding is enough

    if not flagged:
        return

    st.error("\u26a0 Injection keywords detected")
    st.dataframe(flagged, use_container_width=True, hide_index=True)


def _section_threat_table(findings: list[SpanFinding]) -> None:
    """Threat report table with masked payloads."""
    flagged = [
        f for f in findings if f.verdict in (Verdict.HIDDEN, Verdict.SUSPICIOUS)
    ]
    if not flagged:
        return

    st.subheader("Threat Report")
    rows = []
    for f in flagged:
        rows.append(
            {
                "Page": f.page + 1,
                "Verdict": f.verdict.value.upper(),
                "Technique": f.technique,
                "CR": f"{f.cr:.2f}" if f.cr is not None else "\u2014",
                "\u0394E": f"{f.delta_e:.1f}" if f.delta_e is not None else "\u2014",
                "Score": f"{f.score:.2f}",
                "Text": _mask_text(f.text),
            }
        )
    st.dataframe(rows, use_container_width=True, hide_index=True)

    with st.expander("Show full detected text (caution: adversarial payloads — text is spaced to prevent copy-paste)"):
        for f in flagged:
            st.code(
                f"[Page {f.page + 1}] ({f.verdict.value}) {_defang_text(f.text)}",
                language=None,
            )


def _section_page_viz(result: ScanResult, pdf_path: str) -> None:
    """Side-by-side page visualizations with glyph mask highlighting."""
    flagged = [
        f for f in result.findings
        if f.verdict in (Verdict.HIDDEN, Verdict.SUSPICIOUS)
    ]
    if not flagged:
        return

    pages_with_findings: dict[int, list[SpanFinding]] = {}
    for f in flagged:
        pages_with_findings.setdefault(f.page, []).append(f)

    st.subheader("Page Visualizations")

    doc = pymupdf.open(pdf_path)
    doc_wo = _open_textless(pdf_path)
    try:
        for page_num in sorted(pages_with_findings):
            page = doc[page_num]
            page_wo = doc_wo[page_num]
            img_original = _render_page(page, _DPI)
            img_wo = _render_page(page_wo, _DPI)

            # Left: original with bbox overlays
            img_left = img_original.copy()
            for f in pages_with_findings[page_num]:
                rect = _pixel_rect(f.bbox, _SCALE, img_left.shape)
                if rect:
                    _draw_bbox(img_left, rect, _color_for_verdict(f.verdict))

            # Right: dark background + glyph mask highlights
            img_right = (img_original.astype(np.float32) * 0.2).astype(np.uint8)
            invisible_labels: list[tuple[tuple[int, int, int, int], str]] = []

            for f in pages_with_findings[page_num]:
                rect = _pixel_rect(f.bbox, _SCALE, img_right.shape)
                if rect is None:
                    continue
                x0, y0, x1, y1 = rect

                if f.verdict == Verdict.HIDDEN:
                    if f.glyph_px > 0:
                        crop_w = img_original[y0:y1, x0:x1]
                        crop_wo = img_wo[y0:y1, x0:x1]
                        _, mask = _build_glyph_mask(
                            crop_w, crop_wo, _CORE_THRESHOLD,
                        )
                        if mask is not None:
                            region = img_right[y0:y1, x0:x1]
                            region[mask] = _COLOR_GLYPH_HIGHLIGHT
                    else:
                        invisible_labels.append(
                            (rect, "invisible (no pixels)")
                        )

                _draw_bbox(img_right, rect, _color_for_verdict(f.verdict))

            if invisible_labels:
                img_right = _draw_labels(
                    img_right, invisible_labels, _COLOR_HIDDEN,
                )

            st.markdown(f"**Page {page_num + 1}**")
            col_l, col_r = st.columns(2)
            with col_l:
                st.image(img_left, caption="Original", use_container_width=True)
            with col_r:
                st.image(
                    img_right,
                    caption="Detected hidden text (glyph mask)",
                    use_container_width=True,
                )
    finally:
        doc.close()
        doc_wo.close()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    st.title("RenderGuard")
    st.caption("Hidden-text detection for PDF prompt injection · PDF 은닉 텍스트 탐지기")
    st.markdown(
        "PDF를 업로드하면 프롬프트 인젝션에 악용될 수 있는 "
        "숨겨진 텍스트를 자동으로 탐지합니다."
    )

    uploaded = st.file_uploader("Choose a PDF file", type=["pdf"])
    if uploaded is None:
        return

    # Write to temp file for pymupdf
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(uploaded.getvalue())
        tmp_path = tmp.name

    with st.spinner(f"Scanning {uploaded.name}\u2026"):
        result, elapsed = _run_scan(tmp_path)

    _section_banner(result, elapsed)
    _section_keyword_alert(result.findings)
    _section_threat_table(result.findings)
    _section_page_viz(result, tmp_path)


if __name__ == "__main__":
    main()
