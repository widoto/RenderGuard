"""Render-diff measurement engine for PDF hidden text detection.

Renders each page with and without text, then measures WCAG contrast
ratio and CIEDE2000 color distance of glyph strokes against background.

Algorithm:
  1. Render page normally (with text)
  2. Strip BT..ET blocks from content streams + Form XObjects
  3. Re-render (without text)
  4. Pixel diff → glyph mask (core pixels only, AA boundary excluded)
  5. Measure sRGB luminance contrast ratio per text span
  6. Measure CIEDE2000 color distance (ΔE₀₀) per text span

This module provides measurement only.  Classification (hidden /
suspicious / normal) is done by core.detector.
"""

from __future__ import annotations

import numpy as np
import pymupdf

from .color_distance import compute_delta_e
from .models import SpanFinding  # canonical public type


# ---------------------------------------------------------------------------
# WCAG sRGB luminance
# ---------------------------------------------------------------------------
def srgb_luminance(rgb: np.ndarray) -> np.ndarray:
    """Vectorised sRGB -> relative luminance (ITU-R BT.709)."""
    c = rgb.astype(np.float64) / 255.0
    lin = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    return 0.2126 * lin[..., 0] + 0.7152 * lin[..., 1] + 0.0722 * lin[..., 2]


def wcag_contrast_ratio(lum_a: float, lum_b: float) -> float:
    """WCAG 2.x contrast ratio between two relative luminance values."""
    return (max(lum_a, lum_b) + 0.05) / (min(lum_a, lum_b) + 0.05)


# ---------------------------------------------------------------------------
# Token-aware BT..ET removal
# ---------------------------------------------------------------------------
# Design choice: a byte-level scanner instead of pikepdf's tokeniser.
# pikepdf.parse_content_stream() is spec-correct but adds a heavy dependency.
# After page.clean_contents() normalises the stream, this scanner handles all
# cases in our 28-sample corpus and arXiv validation papers.  The scanner
# correctly skips parenthesised strings (...) and hex strings <...> that may
# contain the bytes 'BT' or 'ET' as text content rather than operators.

_WS = frozenset((0x00, 0x09, 0x0A, 0x0D, 0x20, 0x0C))
# PDF delimiters (ISO 32000-1 §7.2.2): ( ) < > [ ] { } / %
_DELIM = frozenset((0x28, 0x29, 0x3C, 0x3E, 0x5B, 0x5D, 0x7B, 0x7D, 0x2F, 0x25))
_BOUNDARY = _WS | _DELIM


def _skip_paren_string(stream: bytes, pos: int) -> int:
    """Advance past a parenthesised string literal, handling nesting."""
    depth, i, n = 1, pos + 1, len(stream)
    while i < n and depth > 0:
        if stream[i] == 0x5C:   # backslash escape
            i += 2
            continue
        if stream[i] == 0x28:   # '('
            depth += 1
        elif stream[i] == 0x29: # ')'
            depth -= 1
        i += 1
    return i


def _skip_hex_string(stream: bytes, pos: int) -> int:
    """Advance past a hex string '<...>'."""
    idx = stream.find(0x3E, pos + 1)  # '>'
    return idx + 1 if idx >= 0 else len(stream)


def _is_operator(stream: bytes, pos: int, tok: bytes) -> bool:
    """True if *tok* at *pos* is a standalone PDF operator."""
    tlen = len(tok)
    if stream[pos : pos + tlen] != tok:
        return False
    before = pos == 0 or stream[pos - 1] in _BOUNDARY
    after = pos + tlen >= len(stream) or stream[pos + tlen] in _BOUNDARY
    return before and after


def strip_bt_et(stream: bytes) -> bytes:
    """Remove all BT..ET text blocks from a PDF content stream.

    Token-aware: skips parenthesised strings ``(...)`` and hex strings
    ``<...>`` that may contain ``BT``/``ET`` as literal text content.
    """
    out = bytearray()
    i, n, inside = 0, len(stream), False

    while i < n:
        ch = stream[i]
        if ch == 0x28:  # '(' — parenthesised string
            end = _skip_paren_string(stream, i)
            if not inside:
                out.extend(stream[i:end])
            i = end
            continue
        if ch == 0x3C and i + 1 < n and stream[i + 1] != 0x3C:  # '<' not '<<'
            end = _skip_hex_string(stream, i)
            if not inside:
                out.extend(stream[i:end])
            i = end
            continue
        if not inside and _is_operator(stream, i, b"BT"):
            inside = True
            i += 2
            continue
        if inside and _is_operator(stream, i, b"ET"):
            inside = False
            i += 2
            continue
        if not inside:
            out.append(ch)
        i += 1

    return bytes(out)


# ---------------------------------------------------------------------------
# Page text stripping — content streams + Form XObjects
# ---------------------------------------------------------------------------
def _strip_page_text(
    doc: pymupdf.Document, page: pymupdf.Page, done: set[int]
) -> None:
    """Strip BT..ET from page content and all Form XObjects.

    Form XObjects (LaTeX TikZ, matplotlib figures, etc.) may contain
    axis labels and legend text.  ``page.get_xobjects()`` returns a flat
    list including nested XObjects — no manual recursion needed.
    """
    for xref in page.get_contents():
        if xref not in done:
            doc.update_stream(xref, strip_bt_et(doc.xref_stream(xref)))
            done.add(xref)
    for xref, _name, _inv, _bbox in page.get_xobjects():
        if xref not in done:
            stream = doc.xref_stream(xref)
            if stream:
                doc.update_stream(xref, strip_bt_et(stream))
            done.add(xref)


def _open_textless(doc_path: str) -> pymupdf.Document:
    """Open PDF with all text removed from every page and XObject."""
    doc = pymupdf.open(doc_path)
    done: set[int] = set()
    for page in doc:
        page.clean_contents()
        _strip_page_text(doc, page, done)
    return doc


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------
def _render_page(page: pymupdf.Page, dpi: int) -> np.ndarray:
    """Render page to an RGB numpy array."""
    pix = page.get_pixmap(dpi=dpi)
    return np.frombuffer(
        pix.samples, dtype=np.uint8
    ).reshape(pix.h, pix.w, pix.n).copy()


def _extract_spans(page: pymupdf.Page) -> list[dict]:
    """Extract all text spans with position and style metadata."""
    spans: list[dict] = []
    for block in page.get_text("dict")["blocks"]:
        if block.get("type") == 0:
            for line in block.get("lines", []):
                spans.extend(line.get("spans", []))
    return spans


# ---------------------------------------------------------------------------
# Per-span measurement
# ---------------------------------------------------------------------------
def _pixel_rect(
    bbox: tuple[float, ...], scale: float, img_shape: tuple[int, ...]
) -> tuple[int, int, int, int] | None:
    """Convert span bbox (points) to pixel rect; None if empty."""
    x0 = max(0, int(bbox[0] * scale))
    y0 = max(0, int(bbox[1] * scale))
    x1 = min(img_shape[1], int(bbox[2] * scale))
    y1 = min(img_shape[0], int(bbox[3] * scale))
    return (x0, y0, x1, y1) if x1 > x0 and y1 > y0 else None


def _build_glyph_mask(
    crop_w: np.ndarray, crop_wo: np.ndarray, core_threshold: float,
) -> tuple[int, np.ndarray | None]:
    """Detect glyph pixels via render diff.

    Returns ``(glyph_px, mask)`` where *mask* is a boolean array
    or ``None`` if no glyph pixels were found.
    """
    diff = np.abs(crop_w.astype(np.int16) - crop_wo.astype(np.int16))
    diff_mag = np.max(diff, axis=2)
    raw_mask = diff_mag > 3
    if not np.any(raw_mask):
        return 0, None

    max_d = float(np.max(diff_mag))
    core = (raw_mask & (diff_mag >= core_threshold * max_d)
            if max_d > 0 else raw_mask)
    mask = core if np.any(core) else raw_mask
    return int(np.count_nonzero(mask)), mask


def _compute_cr(
    crop_w: np.ndarray, crop_wo: np.ndarray, mask: np.ndarray,
) -> float:
    """Compute WCAG contrast ratio from glyph-masked crops."""
    fg = float(np.median(srgb_luminance(crop_w[mask])))
    bg = float(np.median(srgb_luminance(crop_wo[mask])))
    return wcag_contrast_ratio(fg, bg)


def _compute_contrast(
    crop_w: np.ndarray, crop_wo: np.ndarray, core_threshold: float,
) -> tuple[int, float | None, float | None, float | None, float | None]:
    """Compute glyph count, luminance, CR, and ΔE from a cropped image pair.

    Returns ``(glyph_px, fg_lum, bg_lum, cr, delta_e)``.
    Kept for backward compatibility with tools/.
    """
    gpx, mask = _build_glyph_mask(crop_w, crop_wo, core_threshold)
    if mask is None:
        return 0, None, None, None, None
    fg = float(np.median(srgb_luminance(crop_w[mask])))
    bg = float(np.median(srgb_luminance(crop_wo[mask])))
    de = compute_delta_e(crop_w, crop_wo, mask)
    return gpx, fg, bg, wcag_contrast_ratio(fg, bg), de


def _measure_span(
    span: dict,
    img_w: np.ndarray,
    img_wo: np.ndarray,
    scale: float,
    page_num: int,
    core_threshold: float,
    cr_gate: float = 0.0,
) -> SpanFinding:
    """Measure a single span's visibility via render diff.

    When *cr_gate* > 0, ΔE is only computed for spans with CR below
    the gate.  Spans with CR >= cr_gate get delta_e=None (skipped).
    This is a ~5× speedup on typical academic pages where >99% of
    spans are high-contrast black-on-white.
    """
    bbox = tuple(span["bbox"])
    text = span.get("text", "")
    rect = _pixel_rect(bbox, scale, img_w.shape)
    if rect is None:
        return SpanFinding(page_num, bbox, text, None, None, 0, "empty_bbox")

    x0, y0, x1, y1 = rect
    crop_w = img_w[y0:y1, x0:x1]
    crop_wo = img_wo[y0:y1, x0:x1]

    gpx, mask = _build_glyph_mask(crop_w, crop_wo, core_threshold)
    if mask is None:
        return SpanFinding(page_num, bbox, text, None, None, 0,
                           "invisible_render_mode")

    cr = _compute_cr(crop_w, crop_wo, mask)
    de: float | None = None
    if cr_gate <= 0 or cr < cr_gate:
        de = compute_delta_e(crop_w, crop_wo, mask)

    return SpanFinding(page_num, bbox, text, cr, de, gpx, "measured")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def analyze_page(
    doc_path: str,
    page_num: int = 0,
    *,
    dpi: int = 150,
    core_threshold: float = 0.9,
) -> list[SpanFinding]:
    """Analyze a single page for hidden text.

    Returns raw measurements for every text span.  No detection
    threshold is applied — the caller decides policy.
    """
    doc_w = pymupdf.open(doc_path)
    doc_wo = pymupdf.open(doc_path)
    page_wo = doc_wo[page_num]
    page_wo.clean_contents()
    done: set[int] = set()
    _strip_page_text(doc_wo, page_wo, done)

    scale = dpi / 72.0
    spans = _extract_spans(doc_w[page_num])
    img_w = _render_page(doc_w[page_num], dpi)
    img_wo = _render_page(page_wo, dpi)
    doc_w.close()
    doc_wo.close()

    return [
        _measure_span(s, img_w, img_wo, scale, page_num, core_threshold)
        for s in spans
    ]


def analyze_document(
    doc_path: str,
    *,
    dpi: int = 150,
    core_threshold: float = 0.9,
) -> list[SpanFinding]:
    """Analyze all pages of a PDF for hidden text.

    Returns raw measurements for every text span across all pages.
    """
    doc_w = pymupdf.open(doc_path)
    doc_wo = _open_textless(doc_path)
    scale = dpi / 72.0
    findings: list[SpanFinding] = []

    for pn in range(len(doc_w)):
        spans = _extract_spans(doc_w[pn])
        img_w = _render_page(doc_w[pn], dpi)
        img_wo = _render_page(doc_wo[pn], dpi)
        findings.extend(
            _measure_span(s, img_w, img_wo, scale, pn, core_threshold)
            for s in spans
        )

    doc_w.close()
    doc_wo.close()
    return findings
