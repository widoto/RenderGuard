# RenderGuard

Detect hidden text in PDFs by measuring rendered pixels, not declared colors.
OCR-free, CPU-only detection at OCR-grade accuracy.

Prompt injection via invisible text is a real threat: white-on-white
instructions survive into LLM context when PDFs are parsed for RAG pipelines,
resume screening, or document review. Existing tools check declared font
colors against fixed thresholds — RenderGuard renders the page twice
(with and without text), diffs the pixels, and measures what the human eye
would actually see.


## Key Results

Three-tool comparison on the same datasets, same conditions.<sup>1</sup>

|  | pdf-injection-scanner | doc-sherlock | **RenderGuard** |
|--|----------------------|-------------|----------------|
| **Approach** | Declared-color threshold | Heuristic detectors | Render-diff (CR + ΔE₀₀) |
| **Color-hiding recall**<sup>2</sup> | 40% (8/20) | **100%** (20/20) | **100%** (20/20) |
| **Corpus precision**<sup>3</sup> | 83.3% (5/6) | 33.3% (2/6) | **100%** (6/6) |
| **Real-doc clean rate**<sup>4</sup> | **63.3%** (19/30) | 0% (0/28) | 43.3% (13/30) |
| **Speed** (ms/doc) | 2,662 | 98,297 | **2,574** |
| **Resources** | CPU | CPU | CPU (no OCR, no GPU, no ML) |

- No OCR, no GPU, no ML model. Pure pixel measurement on CPU.
- Thresholds (CR < 3.0, ΔE₀₀ < 10.0) are derived from the gap between the
  hidden-corpus maximum and the nearest normal span in a 290,044-span
  distribution — not hand-picked constants. Hidden max ΔE₀₀ = 6.74, nearest
  normal span ΔE₀₀ = 12.03, gap = 5.29 units.

<sup>1</sup> All tools run with default settings, no tuning. Detection is
measured on an attack corpus; false positives on clean real documents.
These are separate datasets — standard practice for detection systems
(attacks require labeled attack samples; FP requires clean documents).
See `results/FINAL_COMPARISON.md` for full methodology.

<sup>2</sup> Corpus: 20 color-based hidden samples (full corpus 33 = 27 hidden
\+ 6 benign; `tiny_font` and `offpage` excluded as size/position-based
techniques outside the color-matching model — see Limitations §3, §4).
pdf-injection-scanner misses all non-white-background attacks (12/20).
Source: `results/baseline_matrix.csv`.

<sup>3</sup> 6 benign corpus samples (gray caption, light footer, colored heading,
syntax-highlighted code, dark slide, gray-on-white pair). doc-sherlock
false-positives on 4 of 6. Source: `results/baseline_matrix.csv`.

<sup>4</sup> 30 arXiv papers (816 pages, no hidden text). "Clean" = zero false
positives in the document. doc-sherlock processed 28/30 (2 timed out
at 300s); all 28 had false positives. Each tool reports findings in
different units (span / word / segment), so document-level clean rate
is used for fair comparison. Source: `results/COMPETITIVE_FP.md`.

![290k span scatter plot](results/distribution_2d.png)

*Left: full distribution (log-scale CR vs ΔE₀₀). Right: low-CR quadrant
zoom. Red ✕ = corpus hidden samples, clustered at bottom-left. Blue dots =
real-document spans. The hidden cluster is isolated from normal text by a
clear gap on both axes.*


## Why This Exists

### Absolute-color detectors miss non-white backgrounds

Two open-source scanners
([pdf-injection-scanner](https://github.com/Andy8647/pdf-injection-scanner),
[PDF-Prompt-Injection-Toolkit](https://github.com/zhihuiyuze/PDF-Prompt-Injection-Toolkit))
check whether each character's RGB exceeds a fixed threshold (e.g. > 0.9).
Both achieved **recall 40%** (8/20 color-hiding samples) on our corpus —
missing **60% of attacks** that use non-white backgrounds.

They fail whenever the background is not white. The Pair C series demonstrates
this — every sample has CR = 1.10 (equally invisible), but tools that assume
a white background see progressively higher contrast as the background darkens:

| Sample | Foreground | Background | CR (true) | CR (vs white) | Overestimate | pis | toolkit |
|--------|-----------|-----------|-----------|---------------|--------------|-----|---------|
| pair_c0 | #F4F4F4 | #FFFFFF | 1.10 | 1.10 | 1.0× | TP | TP |
| pair_c1 | #EAEAEA | #F5F5F5 | 1.10 | 1.20 | 1.1× | TP | TP |
| pair_c2 | #D6D6D6 | #E0E0E0 | 1.10 | 1.45 | 1.3× | **FN** | **FN** |
| pair_c3 | #878787 | #808080 | 1.10 | 3.59 | 3.3× | **FN** | **FN** |
| pair_c4 | #464646 | #404040 | 1.10 | 9.44 | 8.6× | **FN** | **FN** |
| pair_c5 | #101010 | #000000 | 1.10 | 19.03 | **17.3×** | **FN** | **FN** |

All six are equally hidden (CR 1.10). From pair_c2 onward, the white-background
assumption inflates the apparent contrast ratio, and both tools classify them
as normal. At pair_c5 (black on black) the overestimate reaches **17×**.

Render-diff measures the actual rendered background, so CR is always 1.10
regardless of background color. All six are detected.

### doc-sherlock's contrast detector has never worked

[Doc-Sherlock](https://github.com/robomotic/doc-sherlock) includes a
`ContrastDetector` that calls `page.extract_words(extra_attrs=["fill"])`.
`"fill"` is not a valid pdfplumber attribute. The call raises an exception,
caught by a bare `except:`, and falls back to extraction without color data.

Result: **zero contrast findings on every input**, including pure
white-on-white text. This has been the case since the initial commit
(2025-04-17) — 15 months, 2 total commits, never corrected. The detector
is fail-open: it silently reports no issues rather than erroring out.

### Why two axes: the 322 green-link lesson

WCAG luminance contrast is blind to chroma. Hyperref's default green links
(`#00FF00` on white) have CR = 1.37 — below any reasonable hidden-text
threshold — yet are clearly visible.

During validation on 5 arXiv papers, **322 hyperref link spans** fell into
the low-CR zone. A single-axis CR threshold would flag all of them.

CIEDE2000 color distance (ΔE₀₀) captures the perceptual difference that
luminance misses:

| Span type | CR | ΔE₀₀ | Hidden? |
|-----------|-----|-------|---------|
| `#0A0A0A` on `#000000` | 1.06 | 1.6 | Yes |
| `#808080` on `#757575` | 1.17 | 4.3 | Yes |
| `#00FF00` on `#FFFFFF` | 1.37 | 33.3 | **No** (link) |

The two axes combined — CR < 3.0 **AND** ΔE₀₀ < 10.0 — catch all hidden text
while excluding all 322 green links. The gap between hidden max (ΔE₀₀ = 6.74)
and the nearest normal span (ΔE₀₀ = 12.03) is 5.29 units.


## How It Works

### Text-layer geometry + render-diff

The detector combines two independent sources: the PDF text layer
(which declares what text exists and where) and two rendered images
(which show what the human eye actually sees).

0. **Extract spans** from the text layer via `page.get_text("dict")`.
   Each span carries a bounding box and the text content — this is
   the ground truth for "what text is supposed to be on this page."
1. **Render** the page at 150 DPI → numpy RGB array (**with text**)
2. **Strip** all BT…ET text-drawing operators from the content stream
3. **Re-render** the same page → numpy RGB array (**without text**)
4. For each span's bbox, compute the **pixel difference** between the
   two renders = glyph mask (core pixels only, anti-aliasing excluded)
5. From the glyph mask, measure:
   - **CR**: WCAG sRGB luminance contrast ratio (glyph vs background)
   - **ΔE₀₀**: CIEDE2000 perceptual color distance

### Two-axis classification

```
HIDDEN if:
  glyph_px == 0                       → text layer has span, but render
                                        shows no stroke pixels (Tr=3,
                                        or fg ≡ bg within noise threshold)
  OR (CR < 3.0 AND ΔE₀₀ < 10.0)      → color-matched hiding

SUSPICIOUS if:
  CR < 4.5 AND ΔE₀₀ < 20.0           → near-threshold

NORMAL otherwise
```

`glyph_px == 0` is a detection signal, not a measurement failure.
The text layer declares the span exists; the render diff confirms no
visible stroke was produced. This mismatch catches invisible render
mode (Tr=3) and cases where foreground exactly matches background
(e.g. #FAFAFA on #FFFFFF — pixel diff falls below the noise floor).

### Visual verification

The dashboard renders original and glyph-mask views side by side.
Hidden text that is invisible in the original is revealed by highlighting
the detected glyph pixels in yellow on a darkened background:

![RenderGuard dashboard — BLOCK with glyph mask visualization](docs/img/dashboard_demo.png)

*RenderGuard dashboard detecting hidden text in a PDF. Top: BLOCK decision
with threat report. Bottom: original render (left) with red bbox overlay,
and glyph-mask view (right) highlighting detected hidden text pixels in
yellow on a darkened background.*


## Installation and Usage

### Requirements

- Python ≥ 3.10
- PyMuPDF (`pymupdf`), NumPy

```bash
pip install pymupdf numpy
```

### Core API

```python
from core import (
    scan_document, score_findings, evaluate,
    PolicyDecision,
)

findings, page_count, page_times = scan_document("document.pdf")
score_findings(findings)
result = evaluate(findings, page_count, page_times)

if result.decision == PolicyDecision.BLOCK:
    print(f"BLOCKED: {result.hidden_count} hidden span(s)")
    for f in result.findings:
        if f.verdict.value == "hidden":
            print(f"  p{f.page + 1}: {f.text[:30]}…")
else:
    print(f"PASS: {len(result.findings)} spans scanned, no threats")
```

### LangChain integration

```python
from integrators.langchain_loader import (
    SecureDocumentLoader,
    HiddenTextBlockedError,
)

try:
    docs = SecureDocumentLoader("report.pdf").load()
except HiddenTextBlockedError as e:
    print(f"Blocked: {e.scan_result.hidden_count} hidden spans")
```

Hidden spans are replaced with `[SANITIZED_HIDDEN_TEXT]` in the document
text. Scan metadata (decision, findings, scores) is attached to each
Document's `metadata` dict.

### Streamlit dashboard

```bash
pip install streamlit
streamlit run app/dashboard.py
```

Upload a PDF to see: decision banner, threat report table with masked
payloads, and side-by-side page visualizations with bbox overlays.


## Reproducing Benchmarks

### 1. Generate the test corpus

```bash
python tools/verification_harness/generate_corpus.py
```

Creates 33 synthetic PDFs in `corpus/` covering: color-matched hiding
(white-on-white through black-on-black), render mode 3, opacity,
blend modes, per-glyph gradients, and threshold evasion pairs.

### 2. Run baseline comparisons

```bash
python tools/verification_harness/run_baselines.py
```

Runs pdf-injection-scanner, doc-sherlock, and PDF-Prompt-Injection-Toolkit
against the corpus. Outputs `results/baseline_matrix.csv`.

### 3. Measure the 30-paper distribution

```bash
python tools/measure_distribution.py
```

Scans 30 arXiv papers (IDs listed in `data/arxiv_30/`), produces
the CR × ΔE₀₀ scatter plot and quadrant analysis used for threshold
derivation.

### 4. Timing benchmark

```bash
python tools/benchmark_timing.py
```

Measures per-page latency with and without the CR-first ΔE₀₀ gate.


## License

Code: MIT

Test corpus (`corpus/`): MIT — synthetic single-page PDFs generated by
`generate_corpus.py`.

arXiv papers: not included in this repository. Paper IDs are listed in
`data/arxiv_30/` for reproducibility; download them directly from arXiv
under their respective licenses.
