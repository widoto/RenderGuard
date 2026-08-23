# Final Performance Comparison

> 숫자는 전부 측정 근거가 있는 것만 기재. 추정 없음.

## Data sources

| Metric | Dataset | Denominator | Source |
|--------|---------|-------------|--------|
| Recall (공격 탐지) | 코퍼스 색상 은닉 20개 | 20 color-hiding samples | `results/baseline_matrix.csv` |
| Corpus Precision | 코퍼스 benign 6개 | 6 benign samples | `results/baseline_matrix.csv` |
| FP (정상 오탐) | arXiv 30편 (816 pages) | 30 documents | `results/COMPETITIVE_FP.md` |
| Speed | 위와 동일 | ms/doc | 위와 동일 |

- **Recall**은 공격이 포함된 라벨링 코퍼스에서 측정 (공격이 있어야 탐지력 측정 가능).
- **FP**는 공격이 없는 정상 학술 논문에서 측정 (정상에서만 오탐 측정 가능).
- 두 표본이 다른 것은 탐지 시스템 평가의 표준 방법론이다.
- **Recall 분모 20**: 코퍼스 hidden 22개에서 tiny_font_0_5pt, offpage를 제외한 **색상 은닉** 20개로 통일. 이것이 render-diff 방식의 설계 스코프이며, 세 도구 모두 동일 분모로 비교.

## Summary

|  | Recall (20) | Corpus Precision (6) | Real-doc Clean rate (30) | Speed (ms/doc) |
|--|-------------|---------------------|-------------------------|----------------|
| **RenderGuard** | **100%** (20/20) | **100%** (6/6) | 43.3% (13/30) | **2,574** |
| doc-sherlock | **100%** (20/20) | 33.3% (2/6) | 0.0% (0/28) | 98,297 |
| pdf-injection-scanner | 40% (8/20) | 83.3% (5/6) | **63.3%** (19/30) | 2,662 |

### 측정 근거

- **Recall 20/20**: `baseline_matrix.csv`에서 `expected_hidden=True`이고 `sample_id ∉ {tiny_font_0_5pt, offpage}`인 20건 중 `detected=True` 수.
- **Corpus Precision**: `expected_hidden=False`인 6건 중 `detected=False` 수 / 6.
- **Real-doc Clean rate**: arXiv 30편에서 finding이 0건인 문서 수 / 30 (doc-sherlock은 28편 처리, 2편 timeout).
- **Speed**: arXiv 30편 mean latency (ms/doc). `results/COMPETITIVE_FP.md`에서 측정.

---

## Detection detail (색상 은닉 20개)

| Tool | Version | TP | FN | Recall | 미탐 목록 |
|------|---------|----|----|--------|----------|
| **RenderGuard** | local | 20 | 0 | **100%** | — |
| doc-sherlock | 0.1.1 | 20 | 0 | **100%** | — |
| pdf-injection-scanner | 0.2.0 | 8 | 12 | 40% | 아래 참조 |

pdf-injection-scanner 미탐 12건 (전부 비-백색 배경):
black_on_black, colored_bg_match, opacity_ca, render_mode_3, box_overlay,
pair_a2_gray_on_gray, pair_b2_black_on_black, pair_d1_near_white_evade,
pair_c2_e0e0e0, pair_c3_808080, pair_c4_404040, pair_c5_000000

## Corpus precision detail (benign 6개)

| Tool | TN | FP | Precision | FP samples |
|------|----|----|-----------|------------|
| **RenderGuard** | 6 | 0 | **100%** | — |
| pdf-injection-scanner | 5 | 1 | 83.3% | dark_slide |
| doc-sherlock | 2 | 4 | 33.3% | light_footer, colored_heading, syntax_highlighted_code, dark_slide |

## False positives detail (arXiv 30편)

문서 단위: "30편 중 오탐이 1건이라도 발생한 문서 수".
각 도구의 finding 단위가 다르므로 (span vs word vs segment) raw count 비교 불가 — 문서 단위가 공정.

| Tool | FP 문서 | Clean 문서 | Clean rate |
|------|---------|-----------|------------|
| pdf-injection-scanner | 11/30 | **19/30** | **63.3%** |
| **RenderGuard** | 17/30 | 13/30 | 43.3% |
| doc-sherlock | 28/28\* | 0/28 | 0.0% |

\* 2편 timeout (300s). 처리된 28편 전부 오탐 발생.

### Finding unit caveat

| Tool | Finding 1건 = | 비고 |
|------|--------------|------|
| RenderGuard | pymupdf span | `page.get_text("dict")` → spans |
| pdf-injection-scanner | character segment | 공간 인접 문자 클러스터 |
| doc-sherlock | word (주요 detector) | `page.extract_words()` → 단어마다 1건 |

## Trade-off 분석

- **doc-sherlock**: 코퍼스 recall 100%이지만, corpus precision 33.3% (benign 4/6 오탐), real-doc 0% clean (28편 전부 오탐), 38× 느림. 실사용 불가 수준.
- **pdf-injection-scanner**: real-doc clean rate 최고 (63.3%), 그러나 색상 은닉 recall 40%. 백색 배경 외 공격 12/20 미탐.
- **RenderGuard**: 색상 은닉 recall 100%, corpus precision 100%, real-doc clean rate 43.3%, 속도 최고 (2.6s/doc). 위치 기반 은닉(offpage)은 설계 스코프 밖.

## 미측정 항목

| 항목 | 상태 |
|------|------|
| 5개 evasion 샘플 × 경쟁 도구 | `baseline_matrix.csv`에 없음. RenderGuard만 5/5 확인. |
| RenderGuard real-doc FP 내역 | 380 span-level 중 262건은 PDF 구조 이상 (invisible_render_mode 190, empty_bbox 72). 실질 measured FP는 118건. |
