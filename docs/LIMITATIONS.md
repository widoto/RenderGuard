# Known Limitations

These are out of scope for the current detector. Each item states
**why** it is excluded and **what approach** would be needed.

## 1. ToUnicode Spoofing

A custom font maps payload text (e.g. "IGNORE PREVIOUS INSTRUCTIONS")
to visually normal glyphs via a falsified ToUnicode CMap. The rendered
text looks legitimate; only the text extraction layer carries the payload.

- **Why out of scope:** CR and ΔE measure visual contrast. If the visual
  render is normal, both metrics are high — the attack is invisible to
  render-diff.
- **Needed:** OCR-vs-text-layer comparison (cf. doc-sherlock
  RENDERING_DISCREPANCY).

## 2. Font Encoding Artifacts

TeX math extension fonts (cmex10 / TeX-mathx10) map delimiter char codes
to wrong Unicode codepoints via auto-generated ToUnicode CMaps. The text
layer extracts "ÿ" / "ř"; the visual render shows a math symbol or nothing.

- **Why out of scope:** Not an intentional attack. The extracted text is
  meaningless (no coherent payload). Currently counted as FP (17 of 20
  total FP spans on 30-paper set).
- **Needed:** Font encoding validation or glyph-name → Unicode cross-check.
  See `results/ENCODING_ARTIFACT.md`.

## 3. Tiny Font Hiding (< 0.5 pt)

Text rendered at extremely small font sizes produces fewer than 5 glyph
pixels at 150 DPI, falling below the noise filter.

- **Why out of scope:** This is a size-based hiding technique, orthogonal
  to the color-matching model (CR + ΔE).
- **Needed:** Minimum font-size check on the text extraction layer
  (independent of render-diff).

## 4. Off-Page Text

Text placed outside the visible page boundary (negative coordinates or
beyond MediaBox) is never rendered, so render-diff produces no signal.

- **Why out of scope:** No rendered pixels exist for comparison. The
  2-axis model requires a visible render area.
- **Needed:** Bbox-vs-MediaBox boundary check on the text extraction layer.

## 5. Tr=3 Scan / OCR Layer Ambiguity

PDF text rendering mode 3 (invisible) is used both by OCR tools
(Tesseract, Adobe, ABBYY) for accessibility and by attackers for
payload injection. Distinguishing the two requires structural analysis.

- **Why out of scope:** The detector correctly reports Tr=3 spans as
  `invisible_render_mode`. The policy layer flags them. Distinguishing
  benign OCR from attack requires heuristics (GlyphLessFont, image
  XObject presence, ActualText) that are not part of the contrast model.
- **Needed:** Structural signal analysis (e.g. GlyphLessFont heuristic,
  image XObject presence, ActualText attribute).

## 6. DPI Sensitivity

The detector requires ≥ 150 DPI for reliable glyph pixel detection.
Lower DPI may cause thin strokes to fall below the noise threshold,
producing false negatives on legitimate hidden text.

- **Why out of scope:** 150 DPI is the validated default. Lower values
  have not been tested and may degrade measurement accuracy.
- **Needed:** Multi-DPI validation sweep if lower-DPI operation is desired.

## 7. Thin-Glyph Measurement Artifacts

Very thin strokes (hyphens, periods, math label fragments) can produce
fewer than 5 glyph pixels at 150 DPI. The render-diff detects only
anti-aliased edge residue, yielding near-background CR/ΔE values that
fall inside the hidden threshold. 3 such false positives were observed
in the 30-paper set (0.0077% FP rate inclusive).

- **Why out of scope:** The declared font color is #000000 (black) in
  all observed cases — these glyphs would have full contrast if rendered
  at sufficient stroke width. A tie-breaker using declared color was
  investigated but rejected: it correctly filters thin-glyph artifacts
  but also demotes render-mode-3 (Tr=3) and box-overlay attacks to
  SUSPICIOUS, creating false negatives on real attack vectors.
- **Needed:** Stroke-width-aware glyph detection or multi-DPI
  confirmation (render at 300 DPI if gpx < 5 at 150 DPI).
