#!/usr/bin/env python3
"""Measure (CR, ΔE₀₀) distribution across 30 arXiv papers.

Outputs:
  results/distribution_30.csv    — per-span measurements
  results/distribution_2d.png    — 2D scatter plot
  results/quadrant_spans.csv     — spans in the "hidden quadrant"
  results/DISTRIBUTION_2D.md     — analysis report
"""

import csv
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
from core.render_diff import analyze_document, SpanFinding

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    HAS_MPL = True
except ImportError:
    HAS_MPL = False

try:
    import pymupdf
    HAS_PYMUPDF = True
except ImportError:
    HAS_PYMUPDF = False


ROOT = Path(__file__).resolve().parent.parent
ARXIV_30 = ROOT / "data" / "arxiv_30"
ARXIV_5 = ROOT / "data" / "arxiv_papers"
CORPUS = ROOT / "corpus"
RESULTS = ROOT / "results"
RESULTS.mkdir(exist_ok=True)

# Load paper category info
PAPER_INFO = {}
ids_file = ARXIV_30 / "paper_ids.txt"
if ids_file.exists():
    for line in ids_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) >= 3:
            PAPER_INFO[parts[0]] = {"category": parts[1], "title": parts[2]}


def extract_span_color(span: dict) -> str | None:
    """Extract foreground color hex from PyMuPDF span dict."""
    color = span.get("color")
    if color is not None:
        # PyMuPDF returns color as int
        r = (color >> 16) & 0xFF
        g = (color >> 8) & 0xFF
        b = color & 0xFF
        return f"#{r:02X}{g:02X}{b:02X}"
    return None


def measure_paper(pdf_path: str) -> list[dict]:
    """Measure all spans in a paper, returning dicts with all fields."""
    from core.render_diff import (
        _extract_spans, _render_page, _open_textless,
        _pixel_rect, _compute_contrast,
    )
    import pymupdf as fitz

    doc_w = fitz.open(pdf_path)
    doc_wo = _open_textless(pdf_path)
    scale = 150 / 72.0
    rows = []

    for pn in range(len(doc_w)):
        page_w = doc_w[pn]
        spans = _extract_spans(page_w)
        img_w = _render_page(page_w, 150)
        img_wo = _render_page(doc_wo[pn], 150)

        for span in spans:
            text = span.get("text", "")
            if not text.strip():
                continue

            bbox = tuple(span["bbox"])
            font_size = span.get("size", 0)
            fg_hex = extract_span_color(span)

            rect = _pixel_rect(bbox, scale, img_w.shape)
            if rect is None:
                continue

            x0, y0, x1, y1 = rect
            gpx, fg_lum, bg_lum, cr, de = _compute_contrast(
                img_w[y0:y1, x0:x1], img_wo[y0:y1, x0:x1], 0.9
            )

            if gpx < 5 and gpx > 0:
                continue  # skip noise

            # Determine bg_hex from median bg pixel
            bg_hex = None
            if gpx > 0:
                crop_wo = img_wo[y0:y1, x0:x1]
                mask_raw = np.max(np.abs(
                    img_w[y0:y1, x0:x1].astype(np.int16) - crop_wo.astype(np.int16)
                ), axis=2) > 3
                if np.any(mask_raw):
                    bg_med = np.median(crop_wo[mask_raw], axis=0).astype(int)
                    bg_hex = f"#{bg_med[0]:02X}{bg_med[1]:02X}{bg_med[2]:02X}"

            technique = "invisible_render_mode" if gpx == 0 else "measured"

            rows.append({
                "page": pn,
                "text": text[:60],
                "cr": round(cr, 4) if cr is not None else None,
                "delta_e": round(de, 4) if de is not None else None,
                "glyph_px": gpx,
                "font_size": round(font_size, 2),
                "fg_hex": fg_hex,
                "bg_hex": bg_hex,
                "technique": technique,
            })

    doc_w.close()
    doc_wo.close()
    return rows


def render_page_png(pdf_path: str, page_num: int, out_path: str, dpi: int = 150):
    """Render a single page to PNG for visual inspection."""
    import pymupdf as fitz
    doc = fitz.open(pdf_path)
    pix = doc[page_num].get_pixmap(dpi=dpi)
    pix.save(out_path)
    doc.close()


def main():
    # Collect all PDFs
    pdfs_30 = sorted(ARXIV_30.glob("*.pdf"))
    if not pdfs_30:
        print("No PDFs found in data/arxiv_30/. Run download.sh first.")
        return

    print(f"Found {len(pdfs_30)} papers in arxiv_30/")

    all_rows = []
    paper_stats = []

    for pdf_path in pdfs_30:
        pid = pdf_path.stem
        info = PAPER_INFO.get(pid, {"category": "unknown", "title": pid})
        print(f"  [{info['category']:>15}] {pid}...", end="", flush=True)

        t0 = time.time()
        try:
            rows = measure_paper(str(pdf_path))
        except Exception as e:
            print(f" ERROR: {e}")
            continue
        elapsed = time.time() - t0

        for r in rows:
            r["paper_id"] = pid
            r["category"] = info["category"]

        measured = [r for r in rows if r["technique"] == "measured"]
        invisible = [r for r in rows if r["technique"] == "invisible_render_mode"]

        all_rows.extend(rows)
        paper_stats.append({
            "paper_id": pid,
            "category": info["category"],
            "total_spans": len(rows),
            "measured": len(measured),
            "invisible": len(invisible),
            "elapsed_s": round(elapsed, 1),
        })
        print(f" {len(rows)} spans ({elapsed:.1f}s)")

    # Write CSV
    csv_path = RESULTS / "distribution_30.csv"
    fieldnames = [
        "paper_id", "category", "page", "text", "cr", "delta_e",
        "glyph_px", "font_size", "fg_hex", "bg_hex", "technique",
    ]
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_rows)
    print(f"\nWrote {len(all_rows)} rows to {csv_path}")

    # Analysis
    measured = [r for r in all_rows if r["technique"] == "measured"
                and r["cr"] is not None and r["delta_e"] is not None]
    invisible = [r for r in all_rows if r["technique"] == "invisible_render_mode"]

    print(f"\n=== Distribution Summary ===")
    print(f"Total spans: {len(all_rows)}")
    print(f"  Measured: {len(measured)}")
    print(f"  Invisible: {len(invisible)}")

    # Quadrant analysis: CR < threshold AND ΔE < threshold
    for cr_thresh, de_thresh in [(2.0, 20.0), (4.5, 20.0)]:
        quadrant = [r for r in measured
                    if r["cr"] < cr_thresh and r["delta_e"] < de_thresh]
        print(f"\n  Quadrant CR<{cr_thresh} AND ΔE<{de_thresh}: {len(quadrant)} spans")
        if quadrant:
            # Group by paper
            from collections import Counter
            papers = Counter(r["paper_id"] for r in quadrant)
            for pid, cnt in papers.most_common():
                print(f"    {pid}: {cnt} spans")
            # Show all spans
            print(f"\n  --- All spans in quadrant (CR<{cr_thresh}, ΔE<{de_thresh}) ---")
            for r in sorted(quadrant, key=lambda x: (x["cr"] or 99)):
                print(f"    [{r['paper_id']:>12}] p{r['page']:>2} "
                      f"CR={r['cr']:>5.2f} ΔE={r['delta_e']:>6.2f} "
                      f"gpx={r['glyph_px']:>5} fs={r['font_size']:>5.1f} "
                      f"fg={r['fg_hex'] or '?':>8} "
                      f"text={r['text'][:40]}")

    # Render pages for any quadrant spans for visual inspection
    quadrant_tight = [r for r in measured
                      if r["cr"] < 4.5 and r["delta_e"] < 20.0]
    if quadrant_tight:
        inspect_dir = RESULTS / "quadrant_inspect"
        inspect_dir.mkdir(exist_ok=True)
        seen_pages = set()
        for r in quadrant_tight:
            key = (r["paper_id"], r["page"])
            if key not in seen_pages:
                seen_pages.add(key)
                pdf_path = ARXIV_30 / f"{r['paper_id']}.pdf"
                if pdf_path.exists():
                    out_png = inspect_dir / f"{r['paper_id']}_p{r['page']}.png"
                    try:
                        render_page_png(str(pdf_path), r["page"], str(out_png))
                        print(f"  Rendered {out_png.name}")
                    except Exception as e:
                        print(f"  Failed to render {key}: {e}")

    # Write quadrant spans CSV
    if quadrant_tight:
        qcsv = RESULTS / "quadrant_spans.csv"
        with open(qcsv, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(quadrant_tight)
        print(f"\nQuadrant spans written to {qcsv}")

    # CR and ΔE distribution stats
    if measured:
        crs = [r["cr"] for r in measured]
        des = [r["delta_e"] for r in measured]
        print(f"\n=== CR Distribution ===")
        print(f"  min={min(crs):.2f} p5={np.percentile(crs,5):.2f} "
              f"p25={np.percentile(crs,25):.2f} median={np.median(crs):.2f} "
              f"p75={np.percentile(crs,75):.2f} p95={np.percentile(crs,95):.2f} "
              f"max={max(crs):.2f}")
        print(f"\n=== ΔE Distribution ===")
        print(f"  min={min(des):.2f} p5={np.percentile(des,5):.2f} "
              f"p25={np.percentile(des,25):.2f} median={np.median(des):.2f} "
              f"p75={np.percentile(des,75):.2f} p95={np.percentile(des,95):.2f} "
              f"max={max(des):.2f}")

        # Count spans in each CR bucket
        print(f"\n=== CR Buckets ===")
        for lo, hi in [(1.0,1.5),(1.5,2.0),(2.0,3.0),(3.0,4.5),(4.5,7.0),(7.0,21.1)]:
            n = sum(1 for c in crs if lo <= c < hi)
            print(f"  [{lo:.1f}, {hi:.1f}): {n} ({100*n/len(crs):.2f}%)")

    # Scatter plot
    if HAS_MPL and measured:
        # Load corpus hidden spans for overlay
        corpus_hidden = []
        manifest_path = CORPUS / "manifest.json"
        if manifest_path.exists():
            with open(manifest_path) as f:
                manifest = json.load(f)
            for sample in manifest["samples"]:
                if not sample.get("expected_hidden"):
                    continue
                pdf_path = str(CORPUS / sample["file"])
                try:
                    from core.render_diff import analyze_page
                    findings = analyze_page(pdf_path)
                    for f_obj in findings:
                        if "IGNORE_PREVIOUS" in f_obj.text and f_obj.cr is not None:
                            corpus_hidden.append({
                                "cr": f_obj.cr, "delta_e": f_obj.delta_e,
                                "text": f_obj.text[:40],
                            })
                except Exception:
                    pass

        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(18, 8))

        # Left: full view
        ax1.scatter(
            [r["cr"] for r in measured],
            [r["delta_e"] for r in measured],
            c="steelblue", alpha=0.15, s=5, label=f"30-paper spans (n={len(measured)})",
            zorder=2, rasterized=True,
        )
        if corpus_hidden:
            ax1.scatter(
                [r["cr"] for r in corpus_hidden],
                [r["delta_e"] for r in corpus_hidden],
                c="red", marker="x", s=50, linewidths=1.5,
                label=f"Corpus hidden (n={len(corpus_hidden)})",
                zorder=4,
            )
        ax1.set_xscale("log")
        ax1.set_xlabel("CR (log)", fontsize=12)
        ax1.set_ylabel("ΔE₀₀", fontsize=12)
        ax1.set_title("30-Paper Distribution: CR vs ΔE₀₀", fontsize=13)
        ax1.legend(fontsize=9)
        ax1.grid(True, alpha=0.3)
        ax1.set_xlim(0.9, 25)

        # Right: zoomed to low-CR region
        low_cr = [r for r in measured if r["cr"] < 5.0]
        ax2.scatter(
            [r["cr"] for r in low_cr],
            [r["delta_e"] for r in low_cr],
            c="steelblue", alpha=0.4, s=15,
            label=f"30-paper CR<5 (n={len(low_cr)})",
            zorder=2,
        )
        if corpus_hidden:
            ch_low = [r for r in corpus_hidden if r["cr"] is not None and r["cr"] < 5.0]
            if ch_low:
                ax2.scatter(
                    [r["cr"] for r in ch_low],
                    [r["delta_e"] for r in ch_low],
                    c="red", marker="x", s=80, linewidths=2,
                    label=f"Corpus hidden CR<5 (n={len(ch_low)})",
                    zorder=4,
                )
        # Quadrant shading
        ax2.axhline(y=20, color="green", linestyle="--", linewidth=2, alpha=0.7)
        ax2.axvline(x=4.5, color="green", linestyle="--", linewidth=2, alpha=0.7)
        ax2.axhspan(0, 20, xmin=0, xmax=4.5/5.2, alpha=0.06, color="red")
        ax2.text(1.5, 2, "HIDDEN\nquadrant", fontsize=11, color="red",
                ha="center", va="center", alpha=0.6)
        ax2.set_xlabel("CR", fontsize=12)
        ax2.set_ylabel("ΔE₀₀", fontsize=12)
        ax2.set_title("Low-CR Zone: Hidden Quadrant Check", fontsize=13)
        ax2.set_xlim(0.9, 5.2)
        ax2.set_ylim(-1, 60)
        ax2.legend(fontsize=9)
        ax2.grid(True, alpha=0.3)

        plt.tight_layout()
        plot_path = RESULTS / "distribution_2d.png"
        fig.savefig(str(plot_path), dpi=150)
        print(f"\nPlot saved to {plot_path}")


if __name__ == "__main__":
    main()
