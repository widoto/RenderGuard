# Competitive False-Positive Measurement

> **Dataset**: 30 arXiv papers (same set used for RenderGuard FP measurement)
> **All papers are clean** — zero hidden text — every detection is a false positive
> **Tool settings**: All tools run with default configurations (no tuning)

## Finding unit differences (important caveat)

Each tool counts "findings" in different granularity. Raw finding counts are
**not directly comparable** across tools:

| Tool | Finding 1건 = ? | 근거 |
|------|-----------------|------|
| RenderGuard | pymupdf **span** (같은 서식의 연속 문자열) | `page.get_text("dict")` → spans |
| pdf-injection-scanner | **character segment** (공간 인접 문자 클러스터) | `group_chars_into_segments()` |
| doc-sherlock | **word** (주요 detector: contrast, boundary) | `page.extract_words()` → 단어마다 1건 |
| pdf-prompt-injection-toolkit | **페이지당 최대 1건** (break 문으로 cap) | `break  # One finding per page` |

같은 10단어 은닉 문장에 대해: RenderGuard ≈ 1건, pdf-injection-scanner ≈ 1~2건,
doc-sherlock ≈ **10+건**, toolkit ≈ 1건. 따라서 raw FP count 직접 비교는 **참고용**이다.

## Summary — Document-level (primary metric)

단위 무관하게 비교 가능한 공정 지표: **30편 중 오탐이 1건이라도 발생한 문서 수**.

| Tool | Version | FP 문서 | Clean 문서 | Clean rate | Mean speed (ms/doc) |
|------|---------|---------|-----------|------------|---------------------|
| pdf-injection-scanner | 0.2.0 | 11/30 | **19/30** | **63.3%** | 2,662 |
| **RenderGuard** | local | 17/30 | 13/30 | 43.3% | 2,574 |
| pdf-prompt-injection-toolkit | git:02f9ec4 | 23/30 | 7/30 | 23.3% | 8,456 |
| doc-sherlock | 0.1.1 | 28/28* | 0/28 | 0.0% | 98,297 |

\* doc-sherlock: 2편 timeout (300s), 처리된 28편 전부 오탐 발생

### Key observations

1. **pdf-injection-scanner**이 문서 단위 clean rate 63.3%로 1위. 오탐 시 집중적으로 여러 건 나오지만, 선택성이 높아 19편은 완전 통과.
2. **RenderGuard**는 2위 (43.3%). 오탐 17편 중 대부분은 `invisible_render_mode`(190건), `empty_bbox`(72건) 등 PDF 구조적 이상에 의한 것.
3. **toolkit**은 raw FP count가 91로 가장 적지만, 실제로는 23편(76.7%)에서 오탐 발생. 페이지당 1건 cap이 count를 낮춰 보이게 했을 뿐.
4. **doc-sherlock**은 처리한 28편 전부 오탐. word-level 검출 때문에 FP 12,243건으로 부풀림. 속도도 38× 느림.
5. 어떤 경쟁 도구도 render-diff 분석을 하지 않는다 — 휴리스틱 규칙(작은 폰트, 인코딩 이상, 경계 밖 텍스트)에 의존.

## Raw finding counts (reference only — units differ)

| Tool | Raw FP count | Finding unit | Notes |
|------|-------------|--------------|-------|
| pdf-prompt-injection-toolkit | 91 | page-capped | 단위가 거칠어 count 낮음 |
| pdf-injection-scanner | 345 | segment | — |
| RenderGuard | 380 | span | — |
| doc-sherlock | 12,243 | word | 단위가 세밀해 count 높음 |

**주의**: 위 수치를 동일 분모로 나눠 "FP rate"를 산출하는 것은 단위 불일치로 부정확.
각 도구의 자체 분모가 다르므로, raw count 비율 비교는 참고용으로만 사용.

### RenderGuard FP breakdown

The published FP rate (20 / 259,043 = 0.0077%) in `measure_distribution.py` applies additional post-hoc filtering.
The full pipeline (used here) counts all flagged spans without filtering:

| Category | Count | Notes |
|----------|-------|-------|
| `invisible_render_mode` | 190 | Font render mode set to invisible by PDF |
| `empty_bbox` | 72 | Zero-area bounding box in PDF structure |
| `measured` (glyph_px < 5) | 74 | Thin sub-pixel glyph noise |
| `measured` (glyph_px ≥ 5) | 44 | Substantive render-diff false positives |
| **Total** | **380** | |

---

## RenderGuard — Detail

### False positives

| Paper | FP count | Detector(s) | Latency (ms) |
|-------|----------|-------------|--------------|
| 2301.00808 | — | — | — |
| 2301.01264 | 36 | empty_bbox, invisible_render_mode, measured | 3,741 |
| 2301.02635 | 19 | empty_bbox | 1,406 |
| 2301.07268 | 37 | empty_bbox, measured | 2,780 |
| 2301.08362 | 119 | invisible_render_mode, measured | 1,762 |
| 2301.10226 | 33 | empty_bbox, measured | 2,192 |
| 2303.01469 | 42 | invisible_render_mode, measured | 3,554 |
| 2303.05510 | 7 | measured | 1,870 |
| 2303.08774 | 2 | measured | 5,140 |
| 2303.17580 | 6 | invisible_render_mode | 1,962 |
| 2304.02643 | 1 | measured | 5,460 |
| 2305.04723 | 3 | empty_bbox | 1,709 |
| 2305.10601 | 15 | invisible_render_mode, measured | 823 |
| 2306.09375 | 9 | empty_bbox | 6,949 |
| 2306.11794 | 7 | empty_bbox, measured | 3,150 |
| 2307.09288 | 7 | measured | 10,598 |
| 2312.00752 | 6 | invisible_render_mode | 2,105 |

**FP breakdown by detector/type:**

- `invisible_render_mode`: 190 finding(s) — PDF font render mode = invisible
- `measured`: 118 finding(s) — render-diff detected glyph pixels (74 thin noise + 44 substantive)
- `empty_bbox`: 72 finding(s) — zero-area bounding box artifacts

---

## pdf-injection-scanner — Detail

### False positives

| Paper | FP count | Detector(s) | Latency (ms) |
|-------|----------|-------------|--------------|
| 2301.10226 | 3 | Tiny Text | 3,597 |
| 2302.05543 | 4 | White/Invisible Text | 1,032 |
| 2303.08774 | 1 | Suspicious Pattern | 5,545 |
| 2304.01393 | 1 | Tiny Text | 1,264 |
| 2304.06035 | 1 | Suspicious Pattern | 690 |
| 2305.10601 | 67 | Off-Page Text | 1,328 |
| 2305.18404 | 154 | Off-Page Text, Tiny Text | 1,164 |
| 2306.05685 | 6 | Suspicious Pattern | 1,785 |
| 2306.09375 | 24 | White/Invisible Text | 3,230 |
| 2307.09288 | 52 | Suspicious Pattern, Tiny Text | 16,651 |
| 2312.00752 | 32 | Tiny Text, White/Invisible Text | 2,805 |

**FP breakdown by detector/type:**

- `Tiny Text`: 228 finding(s) — legitimate small font usage (footnotes, subscripts, labels)
- `Off-Page Text`: 68 finding(s) — text near page edges falsely flagged
- `White/Invisible Text`: 31 finding(s) — light-colored text in normal layouts
- `Suspicious Pattern`: 18 finding(s) — pattern matching on benign academic text

---

## doc-sherlock — Detail

> doc-sherlock is AGPL-licensed. It was run via subprocess isolation in a separate venv — no code imported into RenderGuard's codebase.

### Summary

- **28 papers processed**, 2 timeouts (2303.08774, 2307.09288) at 300s cutoff
- **12,243 total false positives** — highest among all tools by a wide margin
- **Mean latency: 98,297 ms/doc** (~1.6 min per paper) — 38× slower than RenderGuard

### False positives

| Paper | FP count | Top detector(s) | Latency (ms) |
|-------|----------|-----------------|--------------|
| 2301.00808 | 401 | obscured_text (321), outside_boundary (33) | 86,429 |
| 2301.01264 | 2,664 | obscured_text (2,528), outside_boundary (67) | 102,830 |
| 2301.02635 | 52 | outside_boundary (36), rendering_discrepancy (15) | 72,360 |
| 2301.07268 | 107 | encoding_anomaly (35), outside_boundary (35) | 150,546 |
| 2301.08362 | 104 | encoding_anomaly (37), outside_boundary (35) | 115,180 |
| 2301.10226 | 953 | obscured_text (791), tiny_font (63) | 142,152 |
| 2302.00628 | 283 | obscured_text (195), outside_boundary (33) | 62,167 |
| 2302.04898 | 278 | obscured_text (137), outside_boundary (83) | 67,242 |
| 2302.05543 | 86 | outside_boundary (34), obscured_text (23) | 64,811 |
| 2302.06576 | 101 | outside_boundary (33), encoding_anomaly (26) | 93,157 |
| 2302.10205 | 80 | outside_boundary (34), encoding_anomaly (20) | 77,099 |
| 2302.13971 | 86 | outside_boundary (34), rendering_discrepancy (24) | 115,534 |
| 2303.01469 | 102 | encoding_anomaly (32), rendering_discrepancy (35) | 135,726 |
| 2303.05510 | 765 | obscured_text (615), outside_boundary (89) | 107,238 |
| 2303.08774 | — | TIMEOUT | 300,014 |
| 2303.15056 | 128 | obscured_text (59), outside_boundary (46) | 46,956 |
| 2303.17580 | 526 | obscured_text (432), outside_boundary (33) | 112,633 |
| 2304.01393 | 170 | tiny_font (122), outside_boundary (34) | 41,141 |
| 2304.02643 | 1,308 | obscured_text (1,110), tiny_font (111) | 209,534 |
| 2304.06035 | 53 | outside_boundary (40), rendering_discrepancy (7) | 48,079 |
| 2304.09512 | 24 | encoding_anomaly (16), rendering_discrepancy (8) | 52,802 |
| 2305.04723 | 56 | outside_boundary (33), rendering_discrepancy (15) | 82,796 |
| 2305.10601 | 838 | obscured_text (678), outside_boundary (133) | 64,073 |
| 2305.14314 | 179 | obscured_text (80), outside_boundary (34) | 107,945 |
| 2305.18404 | 755 | obscured_text (666), tiny_font (41) | 64,635 |
| 2306.05685 | 789 | obscured_text (698), outside_boundary (34) | 112,023 |
| 2306.09375 | 340 | obscured_text (188), rendering_discrepancy (45) | 186,567 |
| 2306.11794 | 119 | outside_boundary (60), tiny_font (21) | 78,216 |
| 2307.09288 | — | TIMEOUT | 300,019 |
| 2312.00752 | 896 | obscured_text (678), tiny_font (110) | 152,454 |

**FP breakdown by detector/type:**

- `obscured_text`: 9,242 finding(s) — dominant source, flags normal text overlapping figures/backgrounds
- `outside_boundary`: 1,196 finding(s) — text near margins/bleeds
- `tiny_font`: 710 finding(s) — small fonts in footnotes, axis labels, equations
- `rendering_discrepancy`: 561 finding(s) — rendering artifacts in complex layouts
- `encoding_anomaly`: 520 finding(s) — unusual encodings common in academic PDFs
- `suspicious_metadata`: 7 finding(s) — metadata heuristic false alarms
- `prompt_injection_jailbreak`: 7 finding(s) — pattern-match on benign text in 2 papers

---

## pdf-prompt-injection-toolkit — Detail

### False positives

| Paper | FP count | Detector(s) | Latency (ms) |
|-------|----------|-------------|--------------|
| 2301.00808 | 4 | Micro Font Injection | 6,122 |
| 2301.01264 | 4 | Micro Font Injection, Prompt Injection Pattern | 10,618 |
| 2301.07268 | 1 | Prompt Injection Pattern | 9,156 |
| 2301.10226 | 7 | Micro Font Injection | 10,460 |
| 2302.00628 | 3 | Micro Font Injection, Prompt Injection Pattern | 4,937 |
| 2302.04898 | 2 | Micro Font Injection | 6,451 |
| 2302.05543 | 1 | White Text Injection | 3,523 |
| 2302.06576 | 1 | Prompt Injection Pattern | 5,160 |
| 2302.10205 | 3 | Micro Font Injection | 3,355 |
| 2302.13971 | 1 | Micro Font Injection | 6,104 |
| 2303.05510 | 2 | Micro Font Injection | 5,542 |
| 2303.08774 | 2 | Micro Font Injection, Prompt Injection Pattern | 16,433 |
| 2303.17580 | 3 | Micro Font Injection | 6,507 |
| 2304.01393 | 1 | Micro Font Injection | 4,042 |
| 2304.02643 | 8 | Micro Font Injection | 23,352 |
| 2305.10601 | 5 | Off-Page Text, Text Extraction Discrepancy, White Text Injection | 3,846 |
| 2305.14314 | 1 | Micro Font Injection | 5,121 |
| 2305.18404 | 7 | Micro Font Injection, Off-Page Text | 3,845 |
| 2306.05685 | 3 | Micro Font Injection, Prompt Injection Pattern | 5,614 |
| 2306.09375 | 5 | Micro Font Injection, White Text Injection | 10,388 |
| 2306.11794 | 5 | Micro Font Injection, White Text Injection | 12,568 |
| 2307.09288 | 14 | Micro Font Injection | 51,414 |
| 2312.00752 | 8 | Micro Font Injection, Text Extraction Discrepancy, White Text Injection | 8,560 |

**FP breakdown by detector/type:**

- `Micro Font Injection`: 71 finding(s) — small font sizes in footnotes, labels, equations
- `White Text Injection`: 8 finding(s) — light-colored text in normal layouts
- `Prompt Injection Pattern`: 6 finding(s) — pattern-match on benign academic text
- `Off-Page Text`: 4 finding(s) — text near page edges
- `Text Extraction Discrepancy`: 2 finding(s) — extraction inconsistency in complex PDFs

---

## White-only checker bypass proof

Tools using absolute color thresholds (e.g., `channel > 0.9` = white)
miss dark-on-dark hiding:

- A near-black payload (#101010 on #000000) has channel value 0.063 — far below 0.9
- White-only checkers classify it as normal dark text
- RenderGuard measures actual background-relative CR = 1.10, detecting it as HIDDEN

This is verified in `demos/make_dark_demo_pdf.py` and `demos/demo_dark_paper.pdf`.

---

## Reproduction

```bash
# Download papers (if needed)
bash data/arxiv_30/download.sh

# Run full measurement
python tools/run_competitive_fp.py

# doc-sherlock is very slow (~30 min for 30 papers)
# Timeout: 300s per document
```

## Environment

- macOS (Darwin), Python 3.x
- Each tool in an isolated venv under `tools/verification_harness/venvs/`
- doc-sherlock: AGPL — subprocess-only, no import
