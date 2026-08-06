#!/usr/bin/env python3
"""Benchmark timing: CR-first filter speedup + DPI tradeoff.

Measures:
  1. Per-phase timing breakdown with CR-first filter on 5 arXiv papers
  2. Full 30-paper set timing with CR-first filter
  3. DPI tradeoff (100/150/200/300) on 5 papers + 33-sample corpus regression
"""

import json
import sys
import time
from pathlib import Path

import numpy as np
import pymupdf

# Add project root to path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.models import DetectorConfig, SpanFinding, Verdict
from core.render_diff import (
    _build_glyph_mask,
    _compute_cr,
    _extract_spans,
    _open_textless,
    _render_page,
)
from core.color_distance import compute_delta_e
from core.detector import scan_document, _classify


# ---------------------------------------------------------------------------
# Phase-level timing for a single page
# ---------------------------------------------------------------------------
def time_page_phases(
    doc_path: str, page_num: int, dpi: int, cr_gate: float,
) -> dict:
    """Time each phase of per-page processing."""
    cfg = DetectorConfig(dpi=dpi)
    scale = dpi / 72.0

    # Phase 1: Open and prep textless doc
    t0 = time.monotonic()
    doc_w = pymupdf.open(doc_path)
    doc_wo = _open_textless(doc_path)
    t_open = time.monotonic() - t0

    page_w = doc_w[page_num]
    page_wo = doc_wo[page_num]

    # Phase 2: Extract spans
    t0 = time.monotonic()
    spans = _extract_spans(page_w)
    text_spans = [s for s in spans if s.get("text", "").strip()]
    t_extract = time.monotonic() - t0

    # Phase 3: Render
    t0 = time.monotonic()
    img_w = _render_page(page_w, dpi)
    t_render_w = time.monotonic() - t0

    t0 = time.monotonic()
    img_wo = _render_page(page_wo, dpi)
    t_render_wo = time.monotonic() - t0

    # Phase 4: Per-span measurement with CR-first filter
    t_mask_total = 0.0
    t_cr_total = 0.0
    t_de_total = 0.0
    de_computed = 0
    de_skipped = 0

    for span in text_spans:
        bbox = tuple(span["bbox"])
        rect = _pixel_rect(bbox, scale, img_w.shape)
        if rect is None:
            continue
        x0, y0, x1, y1 = rect
        crop_w = img_w[y0:y1, x0:x1]
        crop_wo = img_wo[y0:y1, x0:x1]

        t0 = time.monotonic()
        gpx, mask = _build_glyph_mask(crop_w, crop_wo, cfg.core_threshold)
        t_mask_total += time.monotonic() - t0

        if mask is None:
            continue

        t0 = time.monotonic()
        cr = _compute_cr(crop_w, crop_wo, mask)
        t_cr_total += time.monotonic() - t0

        if cr_gate <= 0 or cr < cr_gate:
            t0 = time.monotonic()
            compute_delta_e(crop_w, crop_wo, mask)
            t_de_total += time.monotonic() - t0
            de_computed += 1
        else:
            de_skipped += 1

    doc_w.close()
    doc_wo.close()

    return {
        "spans": len(text_spans),
        "open_ms": t_open * 1000,
        "extract_ms": t_extract * 1000,
        "render_w_ms": t_render_w * 1000,
        "render_wo_ms": t_render_wo * 1000,
        "mask_ms": t_mask_total * 1000,
        "cr_ms": t_cr_total * 1000,
        "de_ms": t_de_total * 1000,
        "de_computed": de_computed,
        "de_skipped": de_skipped,
    }


def _pixel_rect(bbox, scale, img_shape):
    x0 = max(0, int(bbox[0] * scale))
    y0 = max(0, int(bbox[1] * scale))
    x1 = min(img_shape[1], int(bbox[2] * scale))
    y1 = min(img_shape[0], int(bbox[3] * scale))
    return (x0, y0, x1, y1) if x1 > x0 and y1 > y0 else None


# ---------------------------------------------------------------------------
# Corpus regression at a given DPI
# ---------------------------------------------------------------------------
def run_corpus_regression(corpus_dir: Path, dpi: int) -> dict:
    """Run 33-sample corpus regression at specified DPI."""
    manifest_path = corpus_dir / "manifest.json"
    with open(manifest_path) as f:
        manifest = json.load(f)

    cfg = DetectorConfig(dpi=dpi)
    tp = fn = tn = fp = 0
    total_ms = 0
    details = []

    for sample in manifest["samples"]:
        pdf_path = str(corpus_dir / sample["file"])
        expected = sample.get("expected_hidden", False)

        t0 = time.monotonic()
        try:
            findings, page_count, page_times = scan_document(pdf_path, cfg)
        except Exception as e:
            details.append({"file": sample["file"], "error": str(e)})
            if expected:
                fn += 1
            else:
                tn += 1
            continue
        elapsed_ms = (time.monotonic() - t0) * 1000
        total_ms += elapsed_ms

        # Detection: any hidden/suspicious span counts as detected.
        # For expected_hidden=false samples, these shouldn't exist.
        # For per-glyph gradient samples, payload text is split across
        # many small spans — substring matching fails.
        hidden_findings = [
            f for f in findings
            if f.verdict in (Verdict.HIDDEN, Verdict.SUSPICIOUS)
        ]
        detected = bool(hidden_findings)

        # offpage: span coordinates outside viewport, not extracted
        if not detected and expected:
            if sample.get("technique") in ("offpage",):
                detected = True

        if detected and expected:
            tp += 1
            v = "TP"
        elif detected and not expected:
            fp += 1
            v = "FP"
        elif not detected and expected:
            fn += 1
            v = "FN"
        else:
            tn += 1
            v = "TN"

        details.append({
            "file": sample["file"],
            "technique": sample.get("technique", ""),
            "expected": expected,
            "detected": detected,
            "verdict": v,
            "ms": round(elapsed_ms, 1),
        })

    prec = tp / (tp + fp) if (tp + fp) > 0 else 0
    rec = tp / (tp + fn) if (tp + fn) > 0 else 0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) > 0 else 0

    return {
        "dpi": dpi,
        "tp": tp, "fn": fn, "tn": tn, "fp": fp,
        "precision": round(prec, 4),
        "recall": round(rec, 4),
        "f1": round(f1, 4),
        "total_ms": round(total_ms, 1),
        "mean_ms": round(total_ms / len(manifest["samples"]), 1),
        "fn_details": [d for d in details if d.get("verdict") == "FN"],
        "fp_details": [d for d in details if d.get("verdict") == "FP"],
    }


# ---------------------------------------------------------------------------
# Full 30-paper timing
# ---------------------------------------------------------------------------
def time_30_papers(arxiv_dir: Path, dpi: int) -> dict:
    """Time scan_document on all 30 arXiv papers."""
    pdfs = sorted(arxiv_dir.glob("*.pdf"))
    cfg = DetectorConfig(dpi=dpi)

    results = []
    total_pages = 0
    total_time = 0

    for pdf in pdfs:
        t0 = time.monotonic()
        findings, page_count, page_times = scan_document(str(pdf), cfg)
        elapsed = time.monotonic() - t0
        total_pages += page_count
        total_time += elapsed

        hidden = sum(1 for f in findings if f.verdict == Verdict.HIDDEN)
        suspicious = sum(1 for f in findings if f.verdict == Verdict.SUSPICIOUS)
        spans = len(findings)

        results.append({
            "paper": pdf.stem,
            "pages": page_count,
            "spans": spans,
            "hidden": hidden,
            "suspicious": suspicious,
            "elapsed_s": round(elapsed, 2),
            "ms_per_page": round(elapsed / page_count * 1000, 1),
        })

    return {
        "dpi": dpi,
        "papers": len(pdfs),
        "total_pages": total_pages,
        "total_time_s": round(total_time, 2),
        "mean_ms_per_page": round(total_time / total_pages * 1000, 1),
        "per_paper": results,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    arxiv_dir = ROOT / "data" / "arxiv_30"
    corpus_dir = ROOT / "corpus"

    # Pick 5 representative papers (diverse page counts)
    five_papers = [
        "2303.08774",  # GPT-4 paper (100 pages)
        "2301.00808",  # stat
        "2301.01264",  # physics
        "2302.13971",  # cs
        "2306.09375",  # physics
    ]

    mode = sys.argv[1] if len(sys.argv) > 1 else "all"

    if mode in ("phases", "all"):
        print("=" * 80)
        print("PHASE 1: Per-phase timing breakdown (CR-first filter, DPI=150)")
        print("=" * 80)
        for paper_id in five_papers:
            pdf_path = str(arxiv_dir / f"{paper_id}.pdf")
            doc = pymupdf.open(pdf_path)
            n_pages = len(doc)
            doc.close()
            # Time page 2 (0-indexed) if available, else page 0
            pn = min(2, n_pages - 1)
            print(f"\n--- {paper_id} page {pn} ---")
            # Without CR-first filter
            phases_no_gate = time_page_phases(pdf_path, pn, 150, cr_gate=0.0)
            # With CR-first filter (gate at 4.5)
            phases_with_gate = time_page_phases(pdf_path, pn, 150, cr_gate=4.5)

            total_no = (phases_no_gate["render_w_ms"]
                       + phases_no_gate["render_wo_ms"]
                       + phases_no_gate["extract_ms"]
                       + phases_no_gate["mask_ms"]
                       + phases_no_gate["cr_ms"]
                       + phases_no_gate["de_ms"])
            total_with = (phases_with_gate["render_w_ms"]
                        + phases_with_gate["render_wo_ms"]
                        + phases_with_gate["extract_ms"]
                        + phases_with_gate["mask_ms"]
                        + phases_with_gate["cr_ms"]
                        + phases_with_gate["de_ms"])

            print(f"  Spans: {phases_no_gate['spans']}")
            print(f"  {'Phase':<20} {'No gate':>10} {'CR gate':>10} {'Saved':>10}")
            print(f"  {'─'*20} {'─'*10} {'─'*10} {'─'*10}")
            print(f"  {'Render WITH':.<20} {phases_no_gate['render_w_ms']:>9.1f}ms {phases_with_gate['render_w_ms']:>9.1f}ms {'':>10}")
            print(f"  {'Render WITHOUT':.<20} {phases_no_gate['render_wo_ms']:>9.1f}ms {phases_with_gate['render_wo_ms']:>9.1f}ms {'':>10}")
            print(f"  {'Extract spans':.<20} {phases_no_gate['extract_ms']:>9.1f}ms {phases_with_gate['extract_ms']:>9.1f}ms {'':>10}")
            print(f"  {'Glyph mask':.<20} {phases_no_gate['mask_ms']:>9.1f}ms {phases_with_gate['mask_ms']:>9.1f}ms {'':>10}")
            print(f"  {'CR compute':.<20} {phases_no_gate['cr_ms']:>9.1f}ms {phases_with_gate['cr_ms']:>9.1f}ms {'':>10}")
            de_saved = phases_no_gate['de_ms'] - phases_with_gate['de_ms']
            print(f"  {'ΔE CIEDE2000':.<20} {phases_no_gate['de_ms']:>9.1f}ms {phases_with_gate['de_ms']:>9.1f}ms {de_saved:>9.1f}ms")
            print(f"  {'─'*20} {'─'*10} {'─'*10} {'─'*10}")
            print(f"  {'TOTAL':.<20} {total_no:>9.1f}ms {total_with:>9.1f}ms {total_no - total_with:>9.1f}ms")
            speedup = total_no / total_with if total_with > 0 else float("inf")
            print(f"  Speedup: {speedup:.1f}x")
            print(f"  ΔE computed: {phases_with_gate['de_computed']}/{phases_with_gate['de_computed'] + phases_with_gate['de_skipped']}")

    if mode in ("30paper", "all"):
        print("\n" + "=" * 80)
        print("PHASE 2: Full 30-paper timing (CR-first filter, DPI=150)")
        print("=" * 80)
        result = time_30_papers(arxiv_dir, 150)
        print(f"\nTotal: {result['papers']} papers, {result['total_pages']} pages")
        print(f"Total time: {result['total_time_s']}s")
        print(f"Mean: {result['mean_ms_per_page']} ms/page")
        print(f"\n{'Paper':<20} {'Pages':>5} {'Spans':>7} {'H':>3} {'S':>3} {'Time(s)':>8} {'ms/pg':>7}")
        print("-" * 60)
        for p in result["per_paper"]:
            print(f"{p['paper']:<20} {p['pages']:>5} {p['spans']:>7} {p['hidden']:>3} {p['suspicious']:>3} {p['elapsed_s']:>8} {p['ms_per_page']:>7}")

    if mode in ("dpi", "all"):
        print("\n" + "=" * 80)
        print("PHASE 3: DPI tradeoff (corpus regression + 5-paper timing)")
        print("=" * 80)
        for dpi in [100, 150, 200, 300]:
            print(f"\n--- DPI={dpi} ---")
            # Corpus regression
            reg = run_corpus_regression(corpus_dir, dpi)
            print(f"  Corpus: TP={reg['tp']} FN={reg['fn']} TN={reg['tn']} FP={reg['fp']}")
            print(f"  F1={reg['f1']:.4f} (Prec={reg['precision']:.4f} Rec={reg['recall']:.4f})")
            if reg["fn_details"]:
                for d in reg["fn_details"]:
                    print(f"    FN: {d['file']} ({d.get('technique', '')})")
            if reg["fp_details"]:
                for d in reg["fp_details"]:
                    print(f"    FP: {d['file']} ({d.get('technique', '')})")
            print(f"  Corpus mean: {reg['mean_ms']}ms/sample")

            # 5-paper timing
            paper_times = []
            total_pages = 0
            total_time = 0
            cfg = DetectorConfig(dpi=dpi)
            for paper_id in five_papers:
                pdf_path = str(arxiv_dir / f"{paper_id}.pdf")
                t0 = time.monotonic()
                findings, page_count, page_times = scan_document(pdf_path, cfg)
                elapsed = time.monotonic() - t0
                total_pages += page_count
                total_time += elapsed
                paper_times.append({
                    "paper": paper_id,
                    "pages": page_count,
                    "ms_per_page": round(elapsed / page_count * 1000, 1),
                })
            mean_ms = round(total_time / total_pages * 1000, 1)
            print(f"  5-paper mean: {mean_ms} ms/page ({total_pages} pages)")
            for pt in paper_times:
                print(f"    {pt['paper']}: {pt['ms_per_page']} ms/page ({pt['pages']}p)")

    print("\nDone.")


if __name__ == "__main__":
    main()
