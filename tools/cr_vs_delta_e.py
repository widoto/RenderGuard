#!/usr/bin/env python3
"""Generate (CR, ΔE₀₀) scatter plot for 28-sample corpus + 5 arXiv papers.

Outputs:
  results/cr_vs_delta_e.csv  — raw data
  results/cr_vs_delta_e.png  — scatter plot
"""

import csv
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
from core.render_diff import analyze_document, analyze_page

# matplotlib may not be installed; generate CSV regardless
try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    HAS_MPL = True
except ImportError:
    HAS_MPL = False


ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "corpus"
ARXIV = ROOT / "data" / "arxiv_papers"
RESULTS = ROOT / "results"
RESULTS.mkdir(exist_ok=True)


def classify_span(cr, delta_e, glyph_px, technique, expected_hidden=None):
    """Classify a span for plotting purposes."""
    if technique == "invisible":
        return "invisible"
    if cr is not None and cr < 2.0:
        return "low_cr"
    return "normal"


def main():
    manifest_path = CORPUS / "manifest.json"
    with open(manifest_path) as f:
        manifest = json.load(f)

    rows = []

    # --- 28-sample corpus ---
    print("Processing 28-sample corpus...")
    for sample in manifest["samples"]:
        sid = sample["file"].replace(".pdf", "")
        pdf_path = str(CORPUS / sample["file"])
        expected = sample.get("expected_hidden", False)
        t0 = time.time()
        findings = analyze_page(pdf_path)
        elapsed = time.time() - t0

        for f in findings:
            if f.glyph_px < 5 and f.technique == "measured":
                continue  # skip noise
            rows.append({
                "source": "corpus",
                "sample": sid,
                "text": f.text[:50],
                "cr": f.cr,
                "delta_e": f.delta_e,
                "glyph_px": f.glyph_px,
                "technique": f.technique,
                "expected_hidden": expected,
                "has_payload": "IGNORE_PREVIOUS" in f.text,
            })
        print(f"  {sid}: {len(findings)} spans ({elapsed:.1f}s)")

    # --- 5 arXiv papers ---
    arxiv_pdfs = sorted(ARXIV.glob("*.pdf"))
    for pdf_path in arxiv_pdfs:
        sid = pdf_path.stem
        print(f"Processing arXiv {sid}...")
        t0 = time.time()
        findings = analyze_document(str(pdf_path))
        elapsed = time.time() - t0

        for f in findings:
            if f.glyph_px < 5 and f.technique == "measured":
                continue
            rows.append({
                "source": "arxiv",
                "sample": sid,
                "text": f.text[:50],
                "cr": f.cr,
                "delta_e": f.delta_e,
                "glyph_px": f.glyph_px,
                "technique": f.technique,
                "expected_hidden": False,
                "has_payload": False,
            })
        print(f"  {sid}: {len(findings)} spans ({elapsed:.1f}s)")

    # --- Write CSV ---
    csv_path = RESULTS / "cr_vs_delta_e.csv"
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "source", "sample", "text", "cr", "delta_e",
            "glyph_px", "technique", "expected_hidden", "has_payload",
        ])
        writer.writeheader()
        writer.writerows(rows)
    print(f"\nWrote {len(rows)} rows to {csv_path}")

    # --- Summary statistics ---
    measured = [r for r in rows if r["technique"] == "measured" and r["cr"] is not None and r["delta_e"] is not None]
    invisible = [r for r in rows if r["technique"] == "invisible_render_mode"]

    # Identify groups
    hidden_measured = [r for r in measured if r["has_payload"] or r["expected_hidden"]]
    low_cr_benign = [r for r in measured if r["cr"] < 2.0 and not r["has_payload"] and not r["expected_hidden"]]
    normal = [r for r in measured if r["cr"] >= 2.0 and not r["has_payload"] and not r["expected_hidden"]]

    print(f"\n=== Summary ===")
    print(f"Total rows: {len(rows)}")
    print(f"  Invisible (glyph=0): {len(invisible)}")
    print(f"  Measured: {len(measured)}")
    print(f"    Hidden (payload/expected): {len(hidden_measured)}")
    print(f"    Low-CR benign (CR<2): {len(low_cr_benign)}")
    print(f"    Normal (CR>=2): {len(normal)}")

    if hidden_measured:
        crs = [r["cr"] for r in hidden_measured]
        des = [r["delta_e"] for r in hidden_measured]
        print(f"\n  Hidden measured spans:")
        print(f"    CR range:  [{min(crs):.2f}, {max(crs):.2f}]")
        print(f"    ΔE range:  [{min(des):.2f}, {max(des):.2f}]")

    if low_cr_benign:
        crs = [r["cr"] for r in low_cr_benign]
        des = [r["delta_e"] for r in low_cr_benign]
        print(f"\n  Low-CR benign spans (hyperlinks etc):")
        print(f"    CR range:  [{min(crs):.2f}, {max(crs):.2f}]")
        print(f"    ΔE range:  [{min(des):.2f}, {max(des):.2f}]")
        print(f"    Count: {len(low_cr_benign)}")

    if normal:
        crs = [r["cr"] for r in normal]
        des = [r["delta_e"] for r in normal]
        print(f"\n  Normal visible spans:")
        print(f"    CR range:  [{min(crs):.2f}, {max(crs):.2f}]")
        print(f"    ΔE range:  [{min(des):.2f}, {max(des):.2f}]")

    # --- Scatter plot (zoomed to CR < 2.5 zone where separation matters) ---
    if not HAS_MPL:
        print("\nmatplotlib not available — skipping plot")
        return

    # Refined groups for the CR < 2.0 zone
    hidden_cr_low = [r for r in measured
                     if r["cr"] < 2.0 and (r["expected_hidden"] or r["has_payload"])]
    benign_cr_low = [r for r in measured
                     if r["cr"] < 2.0 and not r["expected_hidden"] and not r["has_payload"]]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(18, 8))

    # --- Left panel: full view ---
    if normal:
        ax1.scatter(
            [r["cr"] for r in normal],
            [r["delta_e"] for r in normal],
            c="steelblue", alpha=0.2, s=8, label=f"Normal (n={len(normal)})",
            zorder=2,
        )
    if low_cr_benign:
        ax1.scatter(
            [r["cr"] for r in low_cr_benign],
            [r["delta_e"] for r in low_cr_benign],
            c="orange", alpha=0.5, s=25,
            label=f"Hyperlinks CR<2 (n={len(low_cr_benign)})",
            zorder=3,
        )
    if hidden_measured:
        ax1.scatter(
            [r["cr"] for r in hidden_measured],
            [r["delta_e"] for r in hidden_measured],
            c="red", marker="x", s=50, linewidths=1.5,
            label=f"Hidden (corpus) (n={len(hidden_measured)})",
            zorder=4,
        )

    ax1.set_xlabel("WCAG Contrast Ratio (CR)", fontsize=12)
    ax1.set_ylabel("CIEDE2000 Color Distance (ΔE₀₀)", fontsize=12)
    ax1.set_title("Full View: CR vs ΔE₀₀", fontsize=13)
    ax1.legend(fontsize=9, loc="lower right")
    ax1.grid(True, alpha=0.3)
    ax1.set_xscale("log")
    ax1.set_xlim(0.9, 25)

    # --- Right panel: zoomed CR < 2.0 zone ---
    if hidden_cr_low:
        ax2.scatter(
            [r["cr"] for r in hidden_cr_low],
            [r["delta_e"] for r in hidden_cr_low],
            c="red", marker="x", s=80, linewidths=2,
            label=f"Hidden text (n={len(hidden_cr_low)})",
            zorder=4,
        )
    if benign_cr_low:
        ax2.scatter(
            [r["cr"] for r in benign_cr_low],
            [r["delta_e"] for r in benign_cr_low],
            c="orange", alpha=0.6, s=40,
            label=f"Hyperlinks (n={len(benign_cr_low)})",
            zorder=3,
        )

    # Decision boundary
    if hidden_cr_low and benign_cr_low:
        max_h = max(r["delta_e"] for r in hidden_cr_low)
        min_b = min(r["delta_e"] for r in benign_cr_low)
        if max_h < min_b:
            threshold = (max_h + min_b) / 2
            ax2.axhline(y=threshold, color="green", linestyle="--", linewidth=2,
                        alpha=0.8, label=f"ΔE₀₀ = {threshold:.1f} (separates)")
            ax2.axhspan(0, threshold, alpha=0.08, color="red")
            ax2.axhspan(threshold, 40, alpha=0.08, color="green")

    ax2.set_xlabel("WCAG Contrast Ratio (CR)", fontsize=12)
    ax2.set_ylabel("CIEDE2000 Color Distance (ΔE₀₀)", fontsize=12)
    ax2.set_title("CR < 2.0 Zone: Hidden vs Hyperlinks", fontsize=13)
    ax2.set_xlim(0.95, 2.1)
    ax2.set_ylim(-1, 40)
    ax2.legend(fontsize=10)
    ax2.grid(True, alpha=0.3)

    # Annotate key points
    ax2.annotate("tiny_font (0.5pt)", xy=(1.84, 14.38), fontsize=8,
                 xytext=(1.55, 17), arrowprops=dict(arrowstyle="->", lw=0.8))
    ax2.annotate("#00FF00 hyperlinks\n(CR=1.37, ΔE=33.3)", xy=(1.37, 33.26),
                 fontsize=8, xytext=(1.6, 35),
                 arrowprops=dict(arrowstyle="->", lw=0.8))

    plt.tight_layout()
    plot_path = RESULTS / "cr_vs_delta_e.png"
    fig.savefig(str(plot_path), dpi=150)
    print(f"\nPlot saved to {plot_path}")


if __name__ == "__main__":
    main()
