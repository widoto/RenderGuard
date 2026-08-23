#!/usr/bin/env python3
"""Measure false-positive rates of competitor tools on 30 arXiv papers.

Same 30 papers, same conditions used for RenderGuard's FP measurement
(20 / 259,043 spans = 0.0077%).  These are all clean papers with no
hidden text — every detection is a false positive.

Tools: pdf-injection-scanner, doc-sherlock, PDF-Prompt-Injection-Toolkit.
RenderGuard is re-verified in the same run.

Output: results/COMPETITIVE_FP.md

Usage::

    python tools/run_competitive_fp.py
"""

from __future__ import annotations

import csv
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
ARXIV_DIR = ROOT / "data" / "arxiv_30"
RESULTS_DIR = ROOT / "results"
VENVS_DIR = ROOT / "tools" / "verification_harness" / "venvs"

sys.path.insert(0, str(ROOT))

# ---------------------------------------------------------------------------
# Paper list
# ---------------------------------------------------------------------------

def load_paper_ids() -> list[dict]:
    ids_file = ARXIV_DIR / "paper_ids.txt"
    papers = []
    for line in ids_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) >= 3:
            papers.append({
                "id": parts[0],
                "category": parts[1],
                "title": parts[2],
                "path": ARXIV_DIR / f"{parts[0]}.pdf",
            })
    return papers


# ---------------------------------------------------------------------------
# Venv helpers (from run_baselines.py)
# ---------------------------------------------------------------------------

def _vbin(venv: Path, name: str) -> str:
    return str(venv / "bin" / name)


def _create_venv(name: str) -> Path:
    venv_dir = VENVS_DIR / name
    if (venv_dir / "bin" / "python").exists():
        print(f"  Reusing existing venv: {name}")
        return venv_dir
    print(f"  Creating venv: {name} ...")
    VENVS_DIR.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [sys.executable, "-m", "venv", str(venv_dir)],
        check=True, capture_output=True,
    )
    subprocess.run(
        [_vbin(venv_dir, "pip"), "install", "--upgrade", "pip"],
        capture_output=True, timeout=120,
    )
    return venv_dir


def _pip(venv: Path, *args: str, timeout: int = 600) -> bool:
    try:
        subprocess.run(
            [_vbin(venv, "pip")] + list(args),
            check=True, capture_output=True, text=True, timeout=timeout,
        )
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        print(f"    pip failed: {str(e)[:200]}")
        return False


def _pkg_version(venv: Path, pkg: str) -> str:
    try:
        r = subprocess.run(
            [_vbin(venv, "pip"), "show", pkg],
            capture_output=True, text=True, timeout=15,
        )
        for line in r.stdout.splitlines():
            if line.startswith("Version:"):
                return line.split(":", 1)[1].strip()
    except Exception:
        pass
    return "unknown"


# ---------------------------------------------------------------------------
# Tool setup
# ---------------------------------------------------------------------------

def setup_pdf_injection_scanner() -> Path | None:
    print("\n[1/3] Setting up pdf-injection-scanner ...")
    venv = _create_venv("pdf-injection-scanner")
    if not (Path(_vbin(venv, "pdf-scan")).exists()):
        if not _pip(venv, "install", "pdf-injection-scanner"):
            return None
    return venv


def setup_doc_sherlock() -> Path | None:
    print("\n[2/3] Setting up doc-sherlock ...")
    venv = _create_venv("doc-sherlock")
    # Check if already installed
    try:
        r = subprocess.run(
            [_vbin(venv, "python"), "-c", "import doc_sherlock"],
            capture_output=True, timeout=10,
        )
        if r.returncode != 0:
            if not _pip(venv, "install",
                        "git+https://github.com/robomotic/doc-sherlock.git",
                        "fastapi", "uvicorn"):
                return None
    except Exception:
        if not _pip(venv, "install",
                    "git+https://github.com/robomotic/doc-sherlock.git",
                    "fastapi", "uvicorn"):
            return None

    # Create wrapper script (AGPL isolation — no import in our codebase)
    runner = venv / "ds_runner.py"
    runner.write_text('''\
"""doc-sherlock wrapper: subprocess isolation for AGPL compliance."""
import json, sys
from doc_sherlock import PDFAnalyzer

pdf_path = sys.argv[1]
out_path = sys.argv[2]

try:
    analyzer = PDFAnalyzer(pdf_path)
    result = analyzer.analyze_file()
    findings_out = []
    for f in result.findings:
        findings_out.append({
            "type": f.finding_type.value if hasattr(f.finding_type, 'value') else str(f.finding_type),
            "severity": f.severity.value if hasattr(f.severity, 'value') else str(f.severity),
            "description": str(f.description) if hasattr(f, 'description') else "",
            "detector": str(getattr(f, 'detector', '')),
            "page": getattr(f, 'page', None),
        })
    data = {"findings": findings_out}
except Exception as e:
    data = {"error": str(e), "findings": []}

with open(out_path, "w") as f:
    json.dump(data, f)
''')
    return venv


def setup_pdf_prompt_injection_toolkit() -> Path | None:
    print("\n[3/3] Setting up PDF-Prompt-Injection-Toolkit ...")
    venv = _create_venv("pdf-prompt-injection-toolkit")
    repo_dir = VENVS_DIR / "pdf-prompt-injection-toolkit-repo"
    if not repo_dir.exists():
        try:
            subprocess.run(
                ["git", "clone", "--depth", "1",
                 "https://github.com/zhihuiyuze/PDF-Prompt-Injection-Toolkit.git",
                 str(repo_dir)],
                check=True, capture_output=True, timeout=60,
            )
        except Exception as e:
            print(f"    git clone failed: {e}")
            return None
    req = repo_dir / "requirements.txt"
    if req.exists():
        _pip(venv, "install", "-r", str(req))
    return venv


# ---------------------------------------------------------------------------
# Tool runners — return (fp_count, findings_detail, latency_ms)
# ---------------------------------------------------------------------------

@dataclass
class ToolResult:
    paper_id: str
    fp_count: int
    detector_names: list[str]
    latency_ms: float
    error: str


def run_pdf_injection_scanner(
    venv: Path, pdf: Path, timeout: float = 60,
) -> ToolResult:
    pid = pdf.stem
    cmd = [_vbin(venv, "pdf-scan"), "--json", str(pdf)]
    t0 = time.monotonic()
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        ms = (time.monotonic() - t0) * 1000
        out = r.stdout.strip()
        if not out:
            return ToolResult(pid, 0, [], ms, "")
        idx = out.find("[")
        if idx < 0:
            return ToolResult(pid, 0, [], ms, "")
        findings = json.loads(out[idx:])
        names = [f.get("type", "unknown") for f in findings]
        return ToolResult(pid, len(findings), names, ms, "")
    except subprocess.TimeoutExpired:
        return ToolResult(pid, 0, [], (time.monotonic() - t0) * 1000, "timeout")
    except Exception as e:
        return ToolResult(pid, 0, [], (time.monotonic() - t0) * 1000, str(e)[:80])


def run_doc_sherlock(
    venv: Path, pdf: Path, timeout: float = 300,
) -> ToolResult:
    pid = pdf.stem
    runner = venv / "ds_runner.py"
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        out_path = tmp.name
    cmd = [_vbin(venv, "python"), str(runner), str(pdf), out_path]
    t0 = time.monotonic()
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        ms = (time.monotonic() - t0) * 1000
        if not Path(out_path).exists() or Path(out_path).stat().st_size == 0:
            err = r.stderr[:120] if r.stderr else "no output"
            return ToolResult(pid, 0, [], ms, err)
        with open(out_path) as f:
            report = json.load(f)
        if report.get("error"):
            return ToolResult(pid, 0, [], ms, report["error"][:120])
        findings = report.get("findings", [])
        names = [f.get("type", f.get("detector", "unknown")) for f in findings]
        return ToolResult(pid, len(findings), names, ms, "")
    except subprocess.TimeoutExpired:
        return ToolResult(pid, 0, [], (time.monotonic() - t0) * 1000,
                          f"timeout ({timeout}s)")
    except Exception as e:
        return ToolResult(pid, 0, [], (time.monotonic() - t0) * 1000, str(e)[:80])
    finally:
        try:
            os.unlink(out_path)
        except OSError:
            pass


def run_pdf_prompt_injection_toolkit(
    venv: Path, pdf: Path, timeout: float = 60,
) -> ToolResult:
    pid = pdf.stem
    repo = VENVS_DIR / "pdf-prompt-injection-toolkit-repo"
    script = repo / "pdf_injection_detector.py"
    if not script.exists():
        return ToolResult(pid, 0, [], 0, "script_missing")
    with tempfile.TemporaryDirectory() as tmpdir:
        cmd = [_vbin(venv, "python"), str(script), str(pdf)]
        t0 = time.monotonic()
        try:
            r = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=timeout, cwd=tmpdir,
            )
            ms = (time.monotonic() - t0) * 1000
            reports_dir = Path(tmpdir) / "scan_reports"
            if reports_dir.exists():
                for rpt in reports_dir.glob("*.json"):
                    with open(rpt) as f:
                        data = json.load(f)
                    findings = data.get("findings", [])
                    risk = data.get("risk_score", 0)
                    if risk > 0 and findings:
                        names = [
                            f.get("technique", f.get("type", "unknown"))
                            for f in findings
                        ]
                        return ToolResult(pid, len(findings), names, ms, "")
                    return ToolResult(pid, 0, [], ms, "")
            return ToolResult(pid, 0, [], ms, "")
        except subprocess.TimeoutExpired:
            return ToolResult(pid, 0, [], (time.monotonic() - t0) * 1000,
                              f"timeout ({timeout}s)")
        except Exception as e:
            return ToolResult(pid, 0, [], (time.monotonic() - t0) * 1000,
                              str(e)[:80])


# ---------------------------------------------------------------------------
# RenderGuard measurement (direct, no subprocess)
# ---------------------------------------------------------------------------

def run_renderguard(papers: list[dict]) -> tuple[list[ToolResult], int]:
    """Run RenderGuard on all papers. Returns (results, total_span_count)."""
    from core import scan_document, score_findings, evaluate, Verdict

    results = []
    total_spans = 0

    for paper in papers:
        pdf = paper["path"]
        pid = paper["id"]
        if not pdf.exists():
            results.append(ToolResult(pid, 0, [], 0, "missing"))
            continue

        t0 = time.monotonic()
        try:
            findings, page_count, page_times = scan_document(str(pdf))
            score_findings(findings)
            result = evaluate(findings, page_count, page_times)
            ms = (time.monotonic() - t0) * 1000

            fp_findings = [
                f for f in result.findings
                if f.verdict in (Verdict.HIDDEN, Verdict.SUSPICIOUS)
            ]
            names = [f.technique for f in fp_findings]
            total_spans += len(result.findings)

            results.append(ToolResult(pid, len(fp_findings), names, ms, ""))
        except Exception as e:
            ms = (time.monotonic() - t0) * 1000
            results.append(ToolResult(pid, 0, [], ms, str(e)[:80]))

    return results, total_spans


# ---------------------------------------------------------------------------
# Report writer
# ---------------------------------------------------------------------------

def write_report(
    all_results: dict[str, list[ToolResult]],
    rg_total_spans: int,
    papers: list[dict],
    tool_versions: dict[str, str],
) -> None:
    """Write results/COMPETITIVE_FP.md."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / "COMPETITIVE_FP.md"

    lines: list[str] = []
    L = lines.append

    L("# Competitive False-Positive Measurement")
    L("")
    L(f"> **Dataset**: 30 arXiv papers (same set used for RenderGuard FP measurement)  ")
    L(f"> **Total spans (RenderGuard)**: {rg_total_spans:,}  ")
    L(f"> **Denominator for all FP rates**: {rg_total_spans:,} (unified)  ")
    L(f"> **All papers are clean** — zero hidden text — every detection is a false positive  ")
    L("")

    # ── Main comparison table ──
    L("## Summary")
    L("")
    L("| Tool | Version | FP count | FP rate | Mean speed (ms/doc) | Notes |")
    L("|------|---------|----------|---------|---------------------|-------|")

    for tool_name in ["RenderGuard", "pdf-injection-scanner",
                      "doc-sherlock", "pdf-prompt-injection-toolkit"]:
        results = all_results.get(tool_name, [])
        ver = tool_versions.get(tool_name, "—")
        total_fp = sum(r.fp_count for r in results)
        processed = [r for r in results if not r.error]
        errors = [r for r in results if r.error]
        if processed:
            mean_ms = sum(r.latency_ms for r in processed) / len(processed)
            speed_str = f"{mean_ms:.0f}"
        else:
            speed_str = "—"

        fp_rate = f"{total_fp / rg_total_spans * 100:.4f}%" if rg_total_spans else "—"

        notes_parts = []
        if errors:
            notes_parts.append(f"{len(errors)} error(s)")
        timeout_docs = [r for r in results if "timeout" in r.error]
        if timeout_docs:
            notes_parts.append(f"{len(timeout_docs)} timeout(s)")
        notes = "; ".join(notes_parts) if notes_parts else "—"

        L(f"| {tool_name} | {ver} | {total_fp} | {fp_rate} | {speed_str} | {notes} |")

    L("")

    # ── Per-document detail ──
    for tool_name in all_results:
        results = all_results[tool_name]
        fp_results = [r for r in results if r.fp_count > 0]
        error_results = [r for r in results if r.error]

        if not fp_results and not error_results:
            continue

        L(f"## {tool_name} — Detail")
        L("")

        if fp_results:
            L("### False positives")
            L("")
            L("| Paper | FP count | Detector(s) | Latency (ms) |")
            L("|-------|----------|-------------|-------------|")
            for r in fp_results:
                det_str = ", ".join(sorted(set(r.detector_names))) if r.detector_names else "—"
                L(f"| {r.paper_id} | {r.fp_count} | {det_str} | {r.latency_ms:.0f} |")
            L("")

            # FP type classification
            all_det_names = []
            for r in fp_results:
                all_det_names.extend(r.detector_names)
            if all_det_names:
                from collections import Counter
                det_counts = Counter(all_det_names)
                L("**FP breakdown by detector/type:**")
                L("")
                for det, cnt in det_counts.most_common():
                    L(f"- `{det}`: {cnt} finding(s)")
                L("")

        if error_results:
            L("### Errors")
            L("")
            L("| Paper | Error | Latency (ms) |")
            L("|-------|-------|-------------|")
            for r in error_results:
                L(f"| {r.paper_id} | {r.error[:80]} | {r.latency_ms:.0f} |")
            L("")

    # ── White-only checker argument ──
    L("## White-only checker bypass proof")
    L("")
    L("Tools using absolute color thresholds (e.g., `channel > 0.9` = white)")
    L("miss dark-on-dark hiding:")
    L("")
    L("- A near-black payload (#0A0A0A on #000000) has channel value "
      "0.039 — far below 0.9")
    L("- White-only checkers classify it as normal dark text")
    L("- RenderGuard measures actual background-relative CR = 1.06, "
      "detecting it as HIDDEN")
    L("")

    path.write_text("\n".join(lines))
    print(f"\nReport written: {path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    papers = load_paper_ids()
    missing = [p for p in papers if not p["path"].exists()]
    if missing:
        print(f"Missing {len(missing)} PDFs. Run data/arxiv_30/download.sh first.")
        for p in missing:
            print(f"  {p['id']}")
        return 1

    print(f"Found {len(papers)} arXiv papers.\n")

    # ── Setup phase ──
    print("=" * 60)
    print("SETUP: Installing tools in isolated venvs")
    print("=" * 60)

    tools_config = [
        ("pdf-injection-scanner", setup_pdf_injection_scanner,
         run_pdf_injection_scanner, "pdf-injection-scanner", 60),
        ("doc-sherlock", setup_doc_sherlock,
         run_doc_sherlock, "doc-sherlock", 300),
        ("pdf-prompt-injection-toolkit", setup_pdf_prompt_injection_toolkit,
         run_pdf_prompt_injection_toolkit, "N/A", 60),
    ]

    tool_venvs: dict[str, Path | None] = {}
    tool_versions: dict[str, str] = {}

    for name, setup_fn, _, pkg, _ in tools_config:
        venv = setup_fn()
        tool_venvs[name] = venv
        if venv:
            if pkg != "N/A":
                tool_versions[name] = _pkg_version(venv, pkg)
            else:
                repo = VENVS_DIR / "pdf-prompt-injection-toolkit-repo"
                try:
                    r = subprocess.run(
                        ["git", "rev-parse", "--short", "HEAD"],
                        capture_output=True, text=True, cwd=repo, timeout=5,
                    )
                    tool_versions[name] = f"git:{r.stdout.strip()}"
                except Exception:
                    tool_versions[name] = "git:unknown"
            print(f"  → {name}: v{tool_versions[name]}")
        else:
            print(f"  → {name}: FAILED")

    active_tools = [
        (name, run_fn, timeout)
        for name, _, run_fn, _, timeout in tools_config
        if tool_venvs.get(name)
    ]
    print(f"\n{len(active_tools)} tool(s) ready.\n")

    # ── RenderGuard measurement ──
    print("=" * 60)
    print("RenderGuard: Scanning 30 papers")
    print("=" * 60)

    rg_results, rg_total_spans = run_renderguard(papers)
    rg_fp = sum(r.fp_count for r in rg_results)
    rg_errors = [r for r in rg_results if r.error]
    rg_processed = [r for r in rg_results if not r.error]
    rg_mean_ms = (sum(r.latency_ms for r in rg_processed) / len(rg_processed)
                  if rg_processed else 0)

    print(f"\nRenderGuard: {rg_fp} FP / {rg_total_spans:,} spans "
          f"= {rg_fp / rg_total_spans * 100:.4f}%")
    print(f"  Mean speed: {rg_mean_ms:.0f} ms/doc")
    if rg_errors:
        print(f"  Errors: {len(rg_errors)}")

    tool_versions["RenderGuard"] = "local"
    all_results: dict[str, list[ToolResult]] = {"RenderGuard": rg_results}

    # ── Competitor tools ──
    for name, run_fn, timeout in active_tools:
        venv = tool_venvs[name]
        print(f"\n{'=' * 60}")
        print(f"{name}: Scanning 30 papers (timeout={timeout}s)")
        print("=" * 60)

        results: list[ToolResult] = []
        for i, paper in enumerate(papers, 1):
            pdf = paper["path"]
            r = run_fn(venv, pdf, timeout)
            results.append(r)

            mark = f"FP={r.fp_count}" if r.fp_count > 0 else "clean"
            err = f" [{r.error}]" if r.error else ""
            print(f"  [{i:2d}/30] {paper['id']}: "
                  f"{mark} {r.latency_ms:.0f}ms{err}")

        all_results[name] = results
        total_fp = sum(r.fp_count for r in results)
        processed = [r for r in results if not r.error]
        mean_ms = (sum(r.latency_ms for r in processed) / len(processed)
                   if processed else 0)
        print(f"\n  Total FP: {total_fp}, Mean: {mean_ms:.0f} ms/doc")

    # ── Report ──
    print(f"\n{'=' * 60}")
    print("Writing report")
    print("=" * 60)
    write_report(all_results, rg_total_spans, papers, tool_versions)

    return 0


if __name__ == "__main__":
    sys.exit(main())
