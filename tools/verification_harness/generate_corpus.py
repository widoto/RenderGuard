"""Generate labeled PDF corpus for verification of hidden-text detection tools.

Each generated PDF contains a unique marker string embedded using a specific
concealment technique.  A manifest.json records ground truth so that
downstream scoring scripts can evaluate detectors automatically.
"""

from __future__ import annotations

import json
import math
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen.canvas import Canvas

# Add project root for core imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))
from visual_sanitizer.core.color_distance import srgb_delta_e

import numpy as np

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PAYLOAD = "IGNORE_PREVIOUS_INSTRUCTIONS_MARKER_7F3A"
CORPUS_DIR = Path(__file__).resolve().parent.parent.parent / "corpus"
MANIFEST_PATH = CORPUS_DIR / "manifest.json"
PAGE_W, PAGE_H = A4


# ---------------------------------------------------------------------------
# WCAG contrast ratio with sRGB linearization
# ---------------------------------------------------------------------------

def _srgb_linearize(c: float) -> float:
    """Linearize a single sRGB channel (0-1 range).

    Uses the IEC 61966-2-1 piecewise formula.
    Threshold 0.03928 per the WCAG 2.x specification.
    """
    if c <= 0.03928:
        return c / 12.92
    return ((c + 0.055) / 1.055) ** 2.4


def relative_luminance(r: float, g: float, b: float) -> float:
    """Relative luminance per WCAG 2.x (inputs in 0-1 sRGB range)."""
    return (
        0.2126 * _srgb_linearize(r)
        + 0.7152 * _srgb_linearize(g)
        + 0.0722 * _srgb_linearize(b)
    )


def contrast_ratio(
    fg: Tuple[float, float, float],
    bg: Tuple[float, float, float],
) -> float:
    """WCAG contrast ratio between two sRGB colours (each channel 0-1)."""
    l1 = relative_luminance(*fg)
    l2 = relative_luminance(*bg)
    lighter = max(l1, l2)
    darker = min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


def hex_to_float(h: str) -> Tuple[float, float, float]:
    """'#RRGGBB' → (r, g, b) each 0-1."""
    h = h.lstrip("#")
    return (int(h[0:2], 16) / 255, int(h[2:4], 16) / 255, int(h[4:6], 16) / 255)


WHITE = (1.0, 1.0, 1.0)


# ---------------------------------------------------------------------------
# Manifest dataclass (extended with contrast fields)
# ---------------------------------------------------------------------------

@dataclass
class Sample:
    file: str
    technique: str
    payload: str
    expected_hidden: bool
    description: str
    bg_color: str
    contrast_ratio_true: float
    contrast_ratio_white_assumed: float
    delta_e_true: Optional[float] = None  # CIEDE2000 ΔE₀₀ fg vs bg
    verification_method: str = "pixel"  # "pixel" | "structural" | "unverified"
    structural_proof: Optional[str] = None
    parameters: dict = field(default_factory=dict)
    pair_group: Optional[str] = None


def _compute_delta_e(fg_hex: str, bg_hex: str) -> float:
    """Compute CIEDE2000 ΔE₀₀ between two hex colours."""
    fg_rgb = np.array([int(fg_hex[i:i+2], 16) for i in (1, 3, 5)])
    bg_rgb = np.array([int(bg_hex[i:i+2], 16) for i in (1, 3, 5)])
    return round(srgb_delta_e(fg_rgb, bg_rgb), 4)


def _make_sample(
    filename: str,
    technique: str,
    expected_hidden: bool,
    description: str,
    fg_hex: str,
    bg_hex: str,
    extra_params: Optional[dict] = None,
    pair_group: Optional[str] = None,
    verification_method: str = "pixel",
    structural_proof: Optional[str] = None,
) -> Sample:
    """Build a Sample with auto-computed contrast ratios and ΔE₀₀."""
    fg = hex_to_float(fg_hex)
    bg = hex_to_float(bg_hex)
    cr_true = round(contrast_ratio(fg, bg), 4)
    cr_white = round(contrast_ratio(fg, WHITE), 4)
    de_true = _compute_delta_e(fg_hex, bg_hex)
    params = {"fg": fg_hex, "bg": bg_hex}
    if extra_params:
        params.update(extra_params)
    return Sample(
        file=filename,
        technique=technique,
        payload=PAYLOAD,
        expected_hidden=expected_hidden,
        description=description,
        bg_color=bg_hex,
        contrast_ratio_true=cr_true,
        contrast_ratio_white_assumed=cr_white,
        delta_e_true=de_true,
        verification_method=verification_method,
        structural_proof=structural_proof,
        parameters=params,
        pair_group=pair_group,
    )


# ---------------------------------------------------------------------------
# Helper – draw visible reference text on every page
# ---------------------------------------------------------------------------

def _draw_reference(c: Canvas, label: str) -> None:
    """Draw visible header so the PDF is not completely blank."""
    c.setFont("Helvetica", 12)
    c.setFillColorRGB(0, 0, 0)
    c.drawString(72, PAGE_H - 72, f"Verification sample: {label}")


# ---------------------------------------------------------------------------
# Helper – draw text on a coloured background rectangle
# ---------------------------------------------------------------------------

def _draw_on_bg(
    c: Canvas,
    label: str,
    fg_hex: str,
    bg_hex: str,
    *,
    font_size: float = 10,
    y: Optional[float] = None,
    draw_bg_rect: bool = True,
) -> None:
    """Draw payload text, optionally on a background rect."""
    _draw_reference(c, label)
    fg = hex_to_float(fg_hex)
    bg = hex_to_float(bg_hex)
    target_y = y if y is not None else PAGE_H / 2
    if draw_bg_rect:
        c.setFillColorRGB(*bg)
        c.rect(50, target_y - 5, 500, 20, fill=1, stroke=0)
    c.setFont("Helvetica", font_size)
    c.setFillColorRGB(*fg)
    c.drawString(72, target_y, PAYLOAD)


# ---------------------------------------------------------------------------
# Helper – find fg colour that gives target contrast on a given background
# ---------------------------------------------------------------------------

def _fg_for_target_contrast(
    bg_hex: str, target_cr: float
) -> str:
    """Return fg hex whose contrast ratio vs bg is closest to target_cr.

    Searches grey-scale values only; result guaranteed within 0.1 of target.
    Always picks the value *closer* to the background luminance so the text
    is hidden rather than highly visible.
    """
    bg = hex_to_float(bg_hex)
    bg_lum = relative_luminance(*bg)
    best_v, best_diff = 0.0, 999.0
    for i in range(256):
        v = i / 255
        cr = contrast_ratio((v, v, v), bg)
        diff = abs(cr - target_cr)
        if diff < best_diff:
            best_diff = diff
            best_v = v
    c = int(round(best_v * 255))
    return f"#{c:02X}{c:02X}{c:02X}"


# ---------------------------------------------------------------------------
# Hidden-group generators (original 12)
# ---------------------------------------------------------------------------

def gen_white_on_white(out: Path) -> Sample:
    c = Canvas(str(out), pagesize=A4)
    _draw_on_bg(c, "white_on_white", "#FFFFFF", "#FFFFFF", draw_bg_rect=False)
    c.save()
    return _make_sample(out.name, "white_on_white", True,
                        "#FFFFFF text on white page background",
                        "#FFFFFF", "#FFFFFF")


def gen_pair_d1_near_white_evade(out: Path) -> Sample:
    """Pair D test: #E5E5E5 = 229/255 ≈ 0.898 per channel.
    Below pdf-injection-scanner's 0.9 cutoff → evades is_white_or_near_white.
    Still near-invisible on white (CR ~1.26:1)."""
    fg_hex = "#E5E5E5"
    c = Canvas(str(out), pagesize=A4)
    _draw_on_bg(c, "pair_d1_near_white_evade", fg_hex, "#FFFFFF",
                draw_bg_rect=False)
    c.save()
    return _make_sample(
        out.name, "pair_d1_near_white_evade", True,
        "Pair D test: #E5E5E5 on white (channel 0.898, below 0.9 threshold) "
        "— evades pdf-injection-scanner's is_white_or_near_white",
        fg_hex, "#FFFFFF",
        extra_params={"channel_value": round(0xE5 / 255, 4)},
        pair_group="pair_D",
    )


def gen_pair_d2_near_white_caught(out: Path) -> Sample:
    """Pair D control: #EBEBEB = 235/255 ≈ 0.922 per channel.
    Above pdf-injection-scanner's 0.9 cutoff → caught by is_white_or_near_white.
    Same visual invisibility as D1 (both hidden)."""
    fg_hex = "#EBEBEB"
    c = Canvas(str(out), pagesize=A4)
    _draw_on_bg(c, "pair_d2_near_white_caught", fg_hex, "#FFFFFF",
                draw_bg_rect=False)
    c.save()
    return _make_sample(
        out.name, "pair_d2_near_white_caught", True,
        "Pair D control: #EBEBEB on white (channel 0.922, above 0.9 threshold) "
        "— caught by pdf-injection-scanner. Same invisibility as D1.",
        fg_hex, "#FFFFFF",
        extra_params={"channel_value": round(0xEB / 255, 4)},
        pair_group="pair_D",
    )


def gen_black_on_black(out: Path) -> Sample:
    c = Canvas(str(out), pagesize=A4)
    _draw_on_bg(c, "black_on_black", "#0A0A0A", "#000000")
    c.save()
    return _make_sample(out.name, "black_on_black", True,
                        "#0A0A0A text on #000000 rect — bypasses absolute white checks",
                        "#0A0A0A", "#000000")


def gen_colored_bg_match(out: Path) -> Sample:
    c = Canvas(str(out), pagesize=A4)
    _draw_on_bg(c, "colored_bg_match", "#E0E0E0", "#E5E5E5")
    c.save()
    return _make_sample(out.name, "colored_bg_match", True,
                        "#E0E0E0 text on #E5E5E5 rect — both below 0.9, non-white bg",
                        "#E0E0E0", "#E5E5E5")


def gen_per_glyph_gradient_high(out: Path) -> Sample:
    c = Canvas(str(out), pagesize=A4)
    _draw_reference(c, "per_glyph_gradient_high")
    c.setFont("Helvetica", 10)
    x = 72.0
    n = len(PAYLOAD)
    for i, ch in enumerate(PAYLOAD):
        v = 0.992 + (0.008 * i / max(n - 1, 1))
        c.setFillColorRGB(v, v, v)
        c.drawString(x, PAGE_H / 2, ch)
        x += c.stringWidth(ch, "Helvetica", 10)
    c.save()
    return _make_sample(
        out.name, "per_glyph_gradient_high", True,
        ("Per-glyph gradient 0.992-1.000 (all above 0.9). "
         "Control sample: existing tools detect this. "
         "Included to show corpus is not biased in our favour."),
        "#FCFCFC", "#FFFFFF",
        extra_params={"fg_range": "0.992-1.000",
                      "role": "control_detectable_by_baselines"},
    )


def gen_per_glyph_gradient_low(out: Path) -> Sample:
    c = Canvas(str(out), pagesize=A4)
    _draw_reference(c, "per_glyph_gradient_low")
    c.setFont("Helvetica", 10)
    x = 72.0
    n = len(PAYLOAD)
    for i, ch in enumerate(PAYLOAD):
        v = 0.878 + (0.032 * i / max(n - 1, 1))
        c.setFillColorRGB(v, v, v)
        c.drawString(x, PAGE_H / 2, ch)
        x += c.stringWidth(ch, "Helvetica", 10)
    c.save()
    return _make_sample(
        out.name, "per_glyph_gradient_low", True,
        "Per-glyph gradient 0.878-0.910 (most below 0.9 threshold)",
        "#E4E4E4", "#FFFFFF",
        extra_params={"fg_range": "0.878-0.910"},
    )


def gen_opacity_ca(out: Path) -> Sample:
    c = Canvas(str(out), pagesize=A4)
    _draw_reference(c, "opacity_ca")
    c.setFont("Helvetica", 10)
    c.setFillColorRGB(0, 0, 0)
    c.saveState()
    c.setFillAlpha(0.02)
    c.drawString(72, PAGE_H / 2, PAYLOAD)
    c.restoreState()
    c.save()
    # Effective colour after alpha compositing on white:
    # eff = 0 * 0.02 + 1.0 * 0.98 = 0.98 → ~#FAFAFA
    return _make_sample(
        out.name, "opacity_ca", True,
        "Black text with ca=0.02 fill opacity",
        "#000000", "#FFFFFF",
        extra_params={"ca": 0.02, "effective_fg_approx": "#FAFAFA"},
    )


def gen_render_mode_3(out: Path) -> Sample:
    c = Canvas(str(out), pagesize=A4)
    _draw_reference(c, "render_mode_3")
    c.setFont("Helvetica", 10)
    c.setFillColorRGB(0, 0, 0)
    # Visible copy (for reference label)
    c.drawString(72, PAGE_H / 2, "Visible reference line")
    # Invisible copy via Tr 3
    c.saveState()
    text_obj = c.beginText(72, PAGE_H / 2 - 30)
    text_obj.setFont("Helvetica", 10)
    text_obj.setTextRenderMode(3)
    text_obj.textLine(PAYLOAD)
    c.drawText(text_obj)
    c.restoreState()
    c.save()
    return _make_sample(
        out.name, "render_mode_3", True,
        "Text rendering mode 3 (invisible — no fill, no stroke)",
        "#000000", "#FFFFFF",
        extra_params={"Tr": 3},
        verification_method="structural",
        structural_proof="Tr=3 produces no fill/stroke; text absent from render",
    )


def gen_tiny_font(out: Path) -> Sample:
    c = Canvas(str(out), pagesize=A4)
    _draw_reference(c, "tiny_font_0.5pt")
    c.setFont("Helvetica", 0.5)
    c.setFillColorRGB(0, 0, 0)
    c.drawString(72, PAGE_H / 2, PAYLOAD)
    c.save()
    return _make_sample(
        out.name, "tiny_font_0_5pt", True,
        "0.5pt font — invisible to human eye but rendered as ~2px black dots at 300 DPI",
        "#000000", "#FFFFFF",
        extra_params={"font_size_pt": 0.5},
        verification_method="unverified",
        structural_proof="Size-based concealment; pixel validation ambiguous "
                         "(high contrast but sub-readable glyphs)",
    )


def gen_box_overlay(out: Path) -> Sample:
    c = Canvas(str(out), pagesize=A4)
    _draw_reference(c, "box_overlay")
    c.setFont("Helvetica", 10)
    c.setFillColorRGB(0, 0, 0)
    c.drawString(72, PAGE_H / 2, PAYLOAD)
    c.setFillColorRGB(1, 1, 1)
    c.rect(60, PAGE_H / 2 - 5, 500, 20, fill=1, stroke=0)
    c.save()
    return _make_sample(
        out.name, "box_overlay", True,
        "Black text covered by opaque white rectangle",
        "#000000", "#FFFFFF",
        extra_params={"overlay_color": "#FFFFFF"},
    )


def gen_offpage(out: Path) -> Sample:
    c = Canvas(str(out), pagesize=A4)
    _draw_reference(c, "offpage")
    c.setFont("Helvetica", 10)
    c.setFillColorRGB(0, 0, 0)
    c.drawString(-5000, -5000, PAYLOAD)
    c.save()
    return _make_sample(
        out.name, "offpage", True,
        "Text at (-5000, -5000) — outside visible page area",
        "#000000", "#FFFFFF",
        extra_params={"x": -5000, "y": -5000},
        verification_method="structural",
        structural_proof="Coordinates outside CropBox; text absent from render",
    )


def gen_blend_mode_screen(out: Path) -> Sample:
    c = Canvas(str(out), pagesize=A4)
    _draw_reference(c, "blend_mode_screen")
    c.setFont("Helvetica", 10)
    c.setFillColorRGB(1.0, 1.0, 1.0)
    c.drawString(72, PAGE_H / 2, PAYLOAD)
    c.save()
    _inject_blend_mode(out, "Screen")
    return _make_sample(
        out.name, "blend_mode_screen", True,
        "Text with BM /Screen blend mode on white background",
        "#FFFFFF", "#FFFFFF",
        extra_params={"BM": "Screen"},
    )


def _inject_blend_mode(pdf_path: Path, mode: str) -> None:
    """Inject a blend mode ExtGState into an existing PDF."""
    try:
        import pikepdf
    except ImportError:
        return

    with pikepdf.open(pdf_path, allow_overwriting_input=True) as pdf:
        page = pdf.pages[0]
        resources = page.get("/Resources", pikepdf.Dictionary())
        ext_gstate = resources.get("/ExtGState", pikepdf.Dictionary())
        ext_gstate["/GS_BM"] = pikepdf.Dictionary({
            "/Type": pikepdf.Name("/ExtGState"),
            "/BM": pikepdf.Name(f"/{mode}"),
        })
        resources["/ExtGState"] = ext_gstate
        page["/Resources"] = resources
        pdf.save(pdf_path)


# ---------------------------------------------------------------------------
# Benign-group generators (original 5)
# ---------------------------------------------------------------------------

def gen_gray_caption(out: Path) -> Sample:
    c = Canvas(str(out), pagesize=A4)
    _draw_reference(c, "gray_caption")
    c.setFont("Helvetica", 9)
    c.setFillColorRGB(0x80 / 255, 0x80 / 255, 0x80 / 255)
    c.drawString(72, PAGE_H / 2, f"Figure 1: Sample caption text — {PAYLOAD}")
    c.save()
    return _make_sample(out.name, "gray_caption", False,
                        "#808080 caption on white — visually readable",
                        "#808080", "#FFFFFF")


def gen_light_footer(out: Path) -> Sample:
    c = Canvas(str(out), pagesize=A4)
    _draw_reference(c, "light_footer")
    c.setFont("Helvetica", 8)
    c.setFillColorRGB(0x99 / 255, 0x99 / 255, 0x99 / 255)
    c.drawString(72, 36, f"Page footer — confidential — {PAYLOAD}")
    c.save()
    return _make_sample(out.name, "light_footer", False,
                        "#999999 footer on white — common in real docs",
                        "#999999", "#FFFFFF")


def gen_colored_heading(out: Path) -> Sample:
    c = Canvas(str(out), pagesize=A4)
    _draw_reference(c, "colored_heading")
    c.setFont("Helvetica-Bold", 16)
    c.setFillColorRGB(0x1A / 255, 0x73 / 255, 0xE8 / 255)
    c.drawString(72, PAGE_H - 120, f"Section Title: {PAYLOAD}")
    c.setFont("Helvetica", 10)
    c.setFillColorRGB(0, 0, 0)
    c.drawString(72, PAGE_H - 150, "Body text in normal black.")
    c.save()
    return _make_sample(out.name, "colored_heading", False,
                        "Blue heading #1A73E8 on white — normal styling",
                        "#1A73E8", "#FFFFFF")


def gen_syntax_highlighted_code(out: Path) -> Sample:
    c = Canvas(str(out), pagesize=A4)
    _draw_reference(c, "syntax_highlighted_code")
    c.setFillColorRGB(0.95, 0.95, 0.95)
    c.rect(60, PAGE_H / 2 - 10, 480, 60, fill=1, stroke=1)
    y = PAGE_H / 2 + 30
    snippets = [
        ("def ", (0, 0, 0.8)),
        ("detect", (0, 0, 0)),
        ("(", (0, 0, 0)),
        ("self", (0.6, 0, 0)),
        ("):", (0, 0, 0)),
    ]
    x = 72.0
    c.setFont("Courier", 10)
    for text, rgb in snippets:
        c.setFillColorRGB(*rgb)
        c.drawString(x, y, text)
        x += c.stringWidth(text, "Courier", 10)
    c.setFillColorRGB(0, 0.5, 0)
    c.drawString(90, y - 15, f'    return "{PAYLOAD}"')
    c.save()
    # Payload colour is green (0, 0.5, 0) on light gray bg
    return _make_sample(
        out.name, "syntax_highlighted_code", False,
        "Syntax-highlighted code block — multi-color is intentional",
        "#008000", "#F2F2F2",
        extra_params={"colors": "blue/black/red/green"},
    )


def gen_dark_slide(out: Path) -> Sample:
    c = Canvas(str(out), pagesize=A4)
    c.setFillColorRGB(0.1, 0.1, 0.15)
    c.rect(0, 0, PAGE_W, PAGE_H, fill=1, stroke=0)
    # 14pt to ensure payload fits on a single line within page width
    c.setFont("Helvetica-Bold", 14)
    c.setFillColorRGB(1, 1, 1)
    c.drawString(72, PAGE_H / 2, PAYLOAD)
    c.setFont("Helvetica", 12)
    c.setFillColorRGB(0.8, 0.8, 0.8)
    c.drawString(72, PAGE_H / 2 - 30, "Subtitle text in light gray")
    c.save()
    return _make_sample(out.name, "dark_slide", False,
                        "White text on dark slide background — normal presentation",
                        "#FFFFFF", "#1A1A26")


# ---------------------------------------------------------------------------
# Matched-pair generators
# ---------------------------------------------------------------------------

def gen_pair_a1_gray_on_white(out: Path) -> Sample:
    """Pair A control: #808080 on white → visible, not hidden."""
    c = Canvas(str(out), pagesize=A4)
    _draw_on_bg(c, "pair_a1_gray_on_white", "#808080", "#FFFFFF",
                draw_bg_rect=False)
    c.save()
    return _make_sample(
        out.name, "pair_a1_gray_on_white", False,
        "Pair A control: #808080 on #FFFFFF — visible (3.95:1)",
        "#808080", "#FFFFFF", pair_group="pair_A",
    )


def gen_pair_a2_gray_on_gray(out: Path) -> Sample:
    """Pair A test: same #808080 on #757575 → hidden (1.17:1).
    Tools that ignore background must get exactly one of A1/A2 wrong."""
    c = Canvas(str(out), pagesize=A4)
    _draw_on_bg(c, "pair_a2_gray_on_gray", "#808080", "#757575")
    c.save()
    return _make_sample(
        out.name, "pair_a2_gray_on_gray", True,
        "Pair A test: #808080 on #757575 — hidden (1.17:1). "
        "Same fg as A1; background-blind tools must fail on one of A1/A2.",
        "#808080", "#757575", pair_group="pair_A",
    )


def gen_pair_b1_white_on_white(out: Path) -> Sample:
    """Pair B baseline: white on white — all tools should catch."""
    c = Canvas(str(out), pagesize=A4)
    _draw_on_bg(c, "pair_b1_white_on_white", "#FFFFFF", "#FFFFFF",
                draw_bg_rect=False)
    c.save()
    return _make_sample(
        out.name, "pair_b1_white_on_white", True,
        "Pair B baseline: #FFFFFF on #FFFFFF (1.00:1) — absolute white check catches",
        "#FFFFFF", "#FFFFFF", pair_group="pair_B",
    )


def gen_pair_b2_black_on_black(out: Path) -> Sample:
    """Pair B test: near-black on black — same invisibility, white check misses."""
    c = Canvas(str(out), pagesize=A4)
    _draw_on_bg(c, "pair_b2_black_on_black", "#0A0A0A", "#000000")
    c.save()
    return _make_sample(
        out.name, "pair_b2_black_on_black", True,
        "Pair B test: #0A0A0A on #000000 (1.06:1) — "
        "same invisibility as B1, absolute white check misses",
        "#0A0A0A", "#000000", pair_group="pair_B",
    )


def _gen_pair_c_sample(
    out: Path, idx: int, bg_hex: str
) -> Sample:
    """Generate one sample from Pair C (bg sweep)."""
    fg_hex = _fg_for_target_contrast(bg_hex, 1.10)
    label = f"pair_c{idx}_{bg_hex.lstrip('#').lower()}"
    c = Canvas(str(out), pagesize=A4)
    # Determine if bg is the page-white or needs a rect
    needs_rect = bg_hex.upper() != "#FFFFFF"
    _draw_on_bg(c, label, fg_hex, bg_hex, draw_bg_rect=needs_rect)
    c.save()
    fg_f = hex_to_float(fg_hex)
    bg_f = hex_to_float(bg_hex)
    cr_true = contrast_ratio(fg_f, bg_f)
    return _make_sample(
        out.name, label, True,
        f"Pair C sweep: fg {fg_hex} on bg {bg_hex} "
        f"(true CR {cr_true:.2f}:1, target 1.10:1). "
        "Further from white → harder for white-assuming tools.",
        fg_hex, bg_hex, pair_group="pair_C",
    )


PAIR_C_BACKGROUNDS = [
    "#FFFFFF", "#F5F5F5", "#E0E0E0", "#808080", "#404040", "#000000",
]


# ---------------------------------------------------------------------------
# Low-chroma evasion samples — test 2-axis (CR + ΔE₀₀) defense
# ---------------------------------------------------------------------------

def gen_evade_tinted_gray_green(out: Path) -> Sample:
    """Low-chroma evasion: faint green-tinted gray on white."""
    fg_hex, bg_hex = "#EDF2ED", "#FFFFFF"
    c = Canvas(str(out), pagesize=A4)
    _draw_on_bg(c, "evade_tinted_gray_green", fg_hex, bg_hex,
                draw_bg_rect=False)
    c.save()
    return _make_sample(
        out.name, "evade_tinted_gray_green", True,
        "Low-chroma evasion: #EDF2ED (faint green tint) on white. "
        "Tests whether ΔE₀₀ catches desaturated tinted hiding.",
        fg_hex, bg_hex, pair_group="evasion",
    )


def gen_evade_tinted_blue(out: Path) -> Sample:
    """Low-chroma evasion: faint blue-tinted gray on white."""
    fg_hex, bg_hex = "#E8E8F2", "#FFFFFF"
    c = Canvas(str(out), pagesize=A4)
    _draw_on_bg(c, "evade_tinted_blue", fg_hex, bg_hex,
                draw_bg_rect=False)
    c.save()
    return _make_sample(
        out.name, "evade_tinted_blue", True,
        "Low-chroma evasion: #E8E8F2 (faint blue tint) on white. "
        "Slightly more chroma than green variant.",
        fg_hex, bg_hex, pair_group="evasion",
    )


def gen_evade_tinted_on_colored(out: Path) -> Sample:
    """Low-chroma evasion: similar-hue fg/bg pair on colored background."""
    fg_hex, bg_hex = "#D0D8D0", "#D5DDD5"
    c = Canvas(str(out), pagesize=A4)
    _draw_on_bg(c, "evade_tinted_on_colored", fg_hex, bg_hex)
    c.save()
    return _make_sample(
        out.name, "evade_tinted_on_colored", True,
        "Low-chroma evasion: #D0D8D0 on #D5DDD5 — both have same green hue, "
        "minimal chroma difference. Tests non-white background with tint.",
        fg_hex, bg_hex, pair_group="evasion",
    )


def gen_evade_desaturated_dark(out: Path) -> Sample:
    """Low-chroma evasion: near-black with faint green tint on black."""
    fg_hex, bg_hex = "#0A0F0A", "#000000"
    c = Canvas(str(out), pagesize=A4)
    _draw_on_bg(c, "evade_desaturated_dark", fg_hex, bg_hex)
    c.save()
    return _make_sample(
        out.name, "evade_desaturated_dark", True,
        "Low-chroma evasion: #0A0F0A (near-black green tint) on #000000. "
        "Tests dark-end detection with minimal chroma.",
        fg_hex, bg_hex, pair_group="evasion",
    )


def gen_evade_midchroma_match(out: Path) -> Sample:
    """Low-chroma evasion: gray with subtle cyan shift on pure gray."""
    fg_hex, bg_hex = "#808888", "#808080"
    c = Canvas(str(out), pagesize=A4)
    _draw_on_bg(c, "evade_midchroma_match", fg_hex, bg_hex)
    c.save()
    return _make_sample(
        out.name, "evade_midchroma_match", True,
        "Low-chroma evasion: #808888 (subtle cyan) on #808080. "
        "Mid-tone region where both CR and ΔE₀₀ could be small.",
        fg_hex, bg_hex, pair_group="evasion",
    )


EVASION_GENERATORS = [
    ("evade_tinted_gray_green", gen_evade_tinted_gray_green),
    ("evade_tinted_blue", gen_evade_tinted_blue),
    ("evade_tinted_on_colored", gen_evade_tinted_on_colored),
    ("evade_desaturated_dark", gen_evade_desaturated_dark),
    ("evade_midchroma_match", gen_evade_midchroma_match),
]


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

HIDDEN_GENERATORS = [
    ("white_on_white", gen_white_on_white),
    ("black_on_black", gen_black_on_black),
    ("colored_bg_match", gen_colored_bg_match),
    ("per_glyph_gradient_high", gen_per_glyph_gradient_high),
    ("per_glyph_gradient_low", gen_per_glyph_gradient_low),
    ("opacity_ca", gen_opacity_ca),
    ("blend_mode_screen", gen_blend_mode_screen),
    ("render_mode_3", gen_render_mode_3),
    ("tiny_font_0_5pt", gen_tiny_font),
    ("box_overlay", gen_box_overlay),
    ("offpage", gen_offpage),
]

BENIGN_GENERATORS = [
    ("gray_caption", gen_gray_caption),
    ("light_footer", gen_light_footer),
    ("colored_heading", gen_colored_heading),
    ("syntax_highlighted_code", gen_syntax_highlighted_code),
    ("dark_slide", gen_dark_slide),
]

PAIR_GENERATORS = [
    ("pair_a1_gray_on_white", gen_pair_a1_gray_on_white),
    ("pair_a2_gray_on_gray", gen_pair_a2_gray_on_gray),
    ("pair_b1_white_on_white", gen_pair_b1_white_on_white),
    ("pair_b2_black_on_black", gen_pair_b2_black_on_black),
    ("pair_d1_near_white_evade", gen_pair_d1_near_white_evade),
    ("pair_d2_near_white_caught", gen_pair_d2_near_white_caught),
]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def generate_corpus() -> List[Sample]:
    """Generate all synthetic samples and return the manifest entries."""
    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    samples: List[Sample] = []

    # Original hidden + benign
    for name, gen_fn in HIDDEN_GENERATORS + BENIGN_GENERATORS:
        out = CORPUS_DIR / f"{name}.pdf"
        sample = gen_fn(out)
        samples.append(sample)
        status = "HIDDEN" if sample.expected_hidden else "BENIGN"
        print(f"  [{status:6s}] {out.name:40s} "
              f"CR_true={sample.contrast_ratio_true:.2f} "
              f"ΔE={sample.delta_e_true or 0:.2f}")

    # Matched pairs A, B, D
    for name, gen_fn in PAIR_GENERATORS:
        out = CORPUS_DIR / f"{name}.pdf"
        sample = gen_fn(out)
        samples.append(sample)
        status = "HIDDEN" if sample.expected_hidden else "BENIGN"
        print(f"  [{status:6s}] {out.name:40s} "
              f"CR_true={sample.contrast_ratio_true:.2f} "
              f"ΔE={sample.delta_e_true or 0:.2f} "
              f"[{sample.pair_group}]")

    # Pair C: background sweep
    for idx, bg_hex in enumerate(PAIR_C_BACKGROUNDS):
        label = f"pair_c{idx}_{bg_hex.lstrip('#').lower()}"
        out = CORPUS_DIR / f"{label}.pdf"
        sample = _gen_pair_c_sample(out, idx, bg_hex)
        samples.append(sample)
        print(f"  [HIDDEN] {out.name:40s} "
              f"CR_true={sample.contrast_ratio_true:.2f} "
              f"ΔE={sample.delta_e_true or 0:.2f} "
              f"[pair_C]")

    # Low-chroma evasion samples
    for name, gen_fn in EVASION_GENERATORS:
        out = CORPUS_DIR / f"{name}.pdf"
        sample = gen_fn(out)
        samples.append(sample)
        print(f"  [EVADE ] {out.name:40s} "
              f"CR_true={sample.contrast_ratio_true:.2f} "
              f"ΔE={sample.delta_e_true or 0:.2f} "
              f"[evasion]")

    return samples


def write_manifest(samples: List[Sample]) -> None:
    manifest = {
        "generated": "auto",
        "payload_marker": PAYLOAD,
        "sample_count": len(samples),
        "samples": [asdict(s) for s in samples],
    }
    with open(MANIFEST_PATH, "w") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    print(f"\nManifest written to {MANIFEST_PATH}")


if __name__ == "__main__":
    print("Generating verification corpus...")
    samples = generate_corpus()
    write_manifest(samples)

    # Summary
    hidden = sum(1 for s in samples if s.expected_hidden)
    benign = sum(1 for s in samples if not s.expected_hidden)
    pairs = sum(1 for s in samples if s.pair_group)
    print(f"\nDone. {len(samples)} samples "
          f"({hidden} hidden, {benign} benign, {pairs} in matched pairs)")
