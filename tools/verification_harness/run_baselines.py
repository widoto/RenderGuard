#!/usr/bin/env python3
"""Run baseline detection tools against the verification corpus.

Each tool is installed in an isolated venv and invoked via subprocess.
Outputs:
  results/baseline_matrix.csv   – per-tool per-sample detection results
  results/FINDINGS.md           – data-only analysis tables
  results/environment.json      – tool versions, settings, platform info
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
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CORPUS_DIR = REPO_ROOT / "corpus"
MANIFEST_PATH = CORPUS_DIR / "manifest.json"
RESULTS_DIR = REPO_ROOT / "results"
VENVS_DIR = REPO_ROOT / "tools" / "verification_harness" / "venvs"


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class RunResult:
    tool: str
    sample_id: str
    technique: str
    expected_hidden: bool
    detected: bool
    detector_name: str
    latency_ms: float
    tool_version: str


# ---------------------------------------------------------------------------
# Venv helpers
# ---------------------------------------------------------------------------

def _vbin(venv: Path, name: str) -> str:
    return str(venv / "bin" / name)


def _create_venv(name: str, python: str = sys.executable) -> Path:
    venv_dir = VENVS_DIR / name
    if (venv_dir / "bin" / "python").exists():
        print(f"  Reusing existing venv: {name}")
        return venv_dir
    print(f"  Creating venv: {name} ...")
    VENVS_DIR.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [python, "-m", "venv", str(venv_dir)],
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
# Tool registry  (name, setup_fn, run_fn, default timeout, recorded settings)
# ---------------------------------------------------------------------------

TOOL_SETTINGS: Dict[str, dict] = {
    "pdf-injection-scanner": {
        "white_threshold": 0.9,
        "same_bg_threshold": 0.05,
        "tiny_text_threshold_pt": 2.0,
        "note": "defaults from scanner.py",
    },
    "doc-sherlock": {
        "min_contrast_ratio": 4.5,
        "min_font_size_pt": 4.0,
        "bg_assumption": "hardcoded (255,255,255)",
        "srgb_linearization": True,
        "note": "AGPL-3.0 — subprocess CLI only",
    },
    "pdf-prompt-injection-toolkit": {
        "micro_font_threshold_pt": 3.0,
        "invisible_text_check": True,
        "risk_score_formula": "CRITICAL=30, HIGH=20, MEDIUM=10, LOW=5, INFO=1",
        "note": "defaults from pdf_injection_detector.py",
    },
    "phantomlint": {
        "dpi": 300,
        "threshold": 0.75,
        "diff": "word_exact",
        "analyze": "nlp",
        "split": "noop",
        "note": "OCR-diff approach; defaults from cli.py",
    },
}


# ---------------------------------------------------------------------------
# Setup functions  (return venv Path or None on failure)
# ---------------------------------------------------------------------------

def setup_pdf_injection_scanner() -> Optional[Path]:
    print("\n[1/4] Setting up pdf-injection-scanner ...")
    venv = _create_venv("pdf-injection-scanner")
    if not _pip(venv, "install", "pdf-injection-scanner"):
        return None
    return venv


def setup_doc_sherlock() -> Optional[Path]:
    print("\n[2/4] Setting up doc-sherlock ...")
    venv = _create_venv("doc-sherlock")
    # fastapi + uvicorn required because cli.py imports them at module level
    if not _pip(venv, "install",
                "git+https://github.com/robomotic/doc-sherlock.git",
                "fastapi", "uvicorn"):
        return None
    return venv


def setup_pdf_prompt_injection_toolkit() -> Optional[Path]:
    print("\n[3/4] Setting up PDF-Prompt-Injection-Toolkit ...")
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
        if not _pip(venv, "install", "-r", str(req)):
            return None
    return venv


def _find_python_lt313() -> Optional[str]:
    """Find a Python 3.9-3.12 interpreter for PhantomLint."""
    for minor in (12, 11, 10, 9):
        for cmd in (f"python3.{minor}", ):
            path = shutil.which(cmd)
            if path:
                return path
    return None


def setup_phantomlint() -> Optional[Path]:
    print("\n[4/4] Setting up PhantomLint ...")
    python = _find_python_lt313()
    if not python:
        print("    No Python 3.9-3.12 found. Skipping PhantomLint.")
        return None
    if not shutil.which("tesseract"):
        print("    tesseract not found. PhantomLint requires it. Skipping.")
        return None
    venv = _create_venv("phantomlint", python=python)
    if not _pip(venv, "install",
                "git+https://github.com/tobycmurray/phantom-lint.git",
                timeout=600):
        return None
    # spacy model
    try:
        subprocess.run(
            [_vbin(venv, "python"), "-m", "spacy", "download", "en_core_web_sm"],
            capture_output=True, timeout=120,
        )
    except Exception:
        pass
    return venv


# ---------------------------------------------------------------------------
# Runner functions  → (detected, detector_names, latency_ms)
# ---------------------------------------------------------------------------

def run_pdf_injection_scanner(
    venv: Path, pdf: Path, timeout: float = 60,
) -> Tuple[bool, str, float]:
    cmd = [_vbin(venv, "pdf-scan"), "--json", str(pdf)]
    t0 = time.monotonic()
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        ms = (time.monotonic() - t0) * 1000
        out = r.stdout.strip()
        if not out:
            return False, "", ms
        # Find JSON array in output (tool may print warnings before it)
        idx = out.find("[")
        if idx < 0:
            return False, "", ms
        findings = json.loads(out[idx:])
        if not findings:
            return False, "", ms
        names = sorted({f.get("type", "unknown") for f in findings})
        return True, "|".join(names), ms
    except subprocess.TimeoutExpired:
        return False, "timeout", (time.monotonic() - t0) * 1000
    except Exception as e:
        return False, f"error:{e!r}"[:80], (time.monotonic() - t0) * 1000


def run_doc_sherlock(
    venv: Path, pdf: Path, timeout: float = 60,
) -> Tuple[bool, str, float]:
    # Use wrapper script because doc-sherlock CLI has a bug
    # (cli.py line 124: 'results' variable referenced before assignment).
    # The wrapper calls PDFAnalyzer.analyze_file() directly via subprocess,
    # maintaining AGPL isolation (no import in our codebase).
    runner = venv / "ds_runner.py"
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        out_path = tmp.name
    cmd = [_vbin(venv, "python"), str(runner), str(pdf), out_path]
    t0 = time.monotonic()
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        ms = (time.monotonic() - t0) * 1000
        if not Path(out_path).exists() or Path(out_path).stat().st_size == 0:
            return False, f"error:{r.stderr[:80]}" if r.stderr else "", ms
        with open(out_path) as f:
            report = json.load(f)
        findings = report.get("findings", [])
        if not findings:
            return False, "", ms
        names = sorted({f.get("type", "unknown") for f in findings})
        return True, "|".join(names), ms
    except subprocess.TimeoutExpired:
        return False, "timeout", (time.monotonic() - t0) * 1000
    except Exception as e:
        return False, f"error:{e!r}"[:80], (time.monotonic() - t0) * 1000
    finally:
        try:
            os.unlink(out_path)
        except OSError:
            pass


def run_pdf_prompt_injection_toolkit(
    venv: Path, pdf: Path, timeout: float = 60,
) -> Tuple[bool, str, float]:
    repo = VENVS_DIR / "pdf-prompt-injection-toolkit-repo"
    script = repo / "pdf_injection_detector.py"
    if not script.exists():
        return False, "script_missing", 0
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
                    if data.get("risk_score", 0) > 0 and findings:
                        names = sorted({
                            f.get("technique", "unknown") for f in findings
                        })
                        return True, "|".join(names), ms
                    return False, "", ms
            return False, "", ms
        except subprocess.TimeoutExpired:
            return False, "timeout", (time.monotonic() - t0) * 1000
        except Exception as e:
            return False, f"error:{e!r}"[:80], (time.monotonic() - t0) * 1000


def run_phantomlint(
    venv: Path, pdf: Path, timeout: float = 300,
) -> Tuple[bool, str, float]:
    with tempfile.TemporaryDirectory() as tmpdir:
        cmd = [_vbin(venv, "phantomlint"), str(pdf), "--output", tmpdir]
        t0 = time.monotonic()
        try:
            subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            ms = (time.monotonic() - t0) * 1000
            hidden = Path(tmpdir) / "hidden_suspicious_phrases.txt"
            if hidden.exists() and hidden.read_text().strip():
                return True, "ocr_diff", ms
            return False, "", ms
        except subprocess.TimeoutExpired:
            return False, "timeout", (time.monotonic() - t0) * 1000
        except Exception as e:
            return False, f"error:{e!r}"[:80], (time.monotonic() - t0) * 1000


# ---------------------------------------------------------------------------
# Tool descriptors
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "name": "pdf-injection-scanner",
        "short": "pis",
        "setup": setup_pdf_injection_scanner,
        "run": run_pdf_injection_scanner,
        "timeout": 60,
        "pkg": "pdf-injection-scanner",
    },
    {
        "name": "doc-sherlock",
        "short": "ds",
        "setup": setup_doc_sherlock,
        "run": run_doc_sherlock,
        "timeout": 60,
        "pkg": "doc-sherlock",
    },
    {
        "name": "pdf-prompt-injection-toolkit",
        "short": "toolkit",
        "setup": setup_pdf_prompt_injection_toolkit,
        "run": run_pdf_prompt_injection_toolkit,
        "timeout": 60,
        "pkg": "N/A",
    },
    {
        "name": "phantomlint",
        "short": "phantomlint",
        "setup": setup_phantomlint,
        "run": run_phantomlint,
        "timeout": 300,
        "pkg": "phantomlint",
    },
]


# ---------------------------------------------------------------------------
# CSV writer
# ---------------------------------------------------------------------------

CSV_FIELDS = [
    "tool", "sample_id", "technique", "expected_hidden",
    "detected", "detector_name", "latency_ms", "tool_version",
]


def write_csv(results: List[RunResult], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        w.writeheader()
        for r in results:
            w.writerow(asdict(r))
    print(f"\nCSV written: {path}")


# ---------------------------------------------------------------------------
# FINDINGS.md writer
# ---------------------------------------------------------------------------

def _verdict(expected_hidden: bool, detected: bool) -> str:
    if expected_hidden:
        return "TP" if detected else "FN"
    else:
        return "FP" if detected else "TN"


def write_findings(
    results: List[RunResult],
    samples: List[dict],
    tool_names: List[str],
    tool_versions: Dict[str, str],
    path: Path,
) -> None:
    """Generate results/FINDINGS.md with data tables only."""
    # Index results by (tool, sample_id)
    idx: Dict[Tuple[str, str], RunResult] = {}
    for r in results:
        idx[(r.tool, r.sample_id)] = r

    sample_ids = [s["file"].replace(".pdf", "") for s in samples]
    active_tools = [t for t in tool_names if any(
        (t, sid) in idx for sid in sample_ids
    )]
    short = {t["name"]: t["short"] for t in TOOLS}

    lines: List[str] = []
    L = lines.append

    L("# Baseline Findings")
    L("")
    L(f"> Tools tested: {len(active_tools)}  ")
    L(f"> Samples: {len(samples)}  ")
    for t in active_tools:
        L(f"> {t}: v{tool_versions.get(t, '?')}  ")
    L("")

    # ---- § 1  Detection matrix ----
    L("## 1. Detection matrix")
    L("")
    hdr = "| # | Sample | Expected | " + " | ".join(
        short.get(t, t) for t in active_tools
    ) + " | Detectors |"
    sep = "|" + "|".join(["---"] * (4 + len(active_tools))) + "|"
    L(hdr)
    L(sep)
    for i, s in enumerate(samples, 1):
        sid = s["file"].replace(".pdf", "")
        exp = "hidden" if s["expected_hidden"] else "visible"
        cells = []
        det_names = []
        for t in active_tools:
            r = idx.get((t, sid))
            if r is None:
                cells.append("N/A")
            else:
                v = _verdict(s["expected_hidden"], r.detected)
                cells.append(f"**{v}**")
                if r.detected and r.detector_name:
                    det_names.append(f"{short.get(t,t)}:{r.detector_name}")
        det_str = "; ".join(det_names) if det_names else ""
        L(f"| {i} | {sid} | {exp} | " + " | ".join(cells)
          + f" | {det_str} |")
    L("")

    # ---- § 2  Matched pairs ----
    L("## 2. Matched pair analysis")
    L("")

    pair_groups = {}
    for s in samples:
        pg = s.get("pair_group")
        if pg:
            pair_groups.setdefault(pg, []).append(s)

    for pg in sorted(pair_groups):
        members = pair_groups[pg]
        L(f"### {pg}")
        L("")
        hdr2 = "| Sample | fg | bg | CR_true | CR_white | Expected | " + \
               " | ".join(short.get(t, t) for t in active_tools) + " |"
        sep2 = "|" + "|".join(["---"] * (6 + len(active_tools))) + "|"
        L(hdr2)
        L(sep2)
        for s in members:
            sid = s["file"].replace(".pdf", "")
            fg = s["parameters"].get("fg", "?")
            bg = s["parameters"].get("bg", s.get("bg_color", "?"))
            cr_t = s.get("contrast_ratio_true", 0)
            cr_w = s.get("contrast_ratio_white_assumed", 0)
            exp = "hidden" if s["expected_hidden"] else "visible"
            cells = []
            for t in active_tools:
                r = idx.get((t, sid))
                if r is None:
                    cells.append("N/A")
                else:
                    cells.append(_verdict(s["expected_hidden"], r.detected))
            L(f"| {sid} | {fg} | {bg} | {cr_t:.2f} | {cr_w:.2f} | {exp} | "
              + " | ".join(cells) + " |")
        L("")

    # ---- § 3  Benign false-positive rates ----
    L("## 3. Benign sample false-positive rates")
    L("")
    benign = [s for s in samples if not s["expected_hidden"]]
    hdr3 = "| Sample | " + " | ".join(
        short.get(t, t) for t in active_tools
    ) + " |"
    sep3 = "|" + "|".join(["---"] * (1 + len(active_tools))) + "|"
    L(hdr3)
    L(sep3)
    for s in benign:
        sid = s["file"].replace(".pdf", "")
        cells = []
        for t in active_tools:
            r = idx.get((t, sid))
            if r is None:
                cells.append("N/A")
            else:
                cells.append("FP" if r.detected else "TN")
        L(f"| {sid} | " + " | ".join(cells) + " |")
    L("")
    # FP rate per tool
    L("| Tool | FP count | Total benign | FP rate |")
    L("|------|----------|-------------|---------|")
    for t in active_tools:
        fp = sum(1 for s in benign
                 if idx.get((t, s["file"].replace(".pdf", "")))
                 and idx[(t, s["file"].replace(".pdf", ""))].detected)
        L(f"| {short.get(t,t)} | {fp} | {len(benign)} | "
          f"{fp/len(benign)*100:.1f}% |")
    L("")

    # ---- § 4  Average latency ----
    L("## 4. Per-tool average latency")
    L("")
    L("| Tool | Samples run | Mean (ms) | Median (ms) | Max (ms) |")
    L("|------|------------|-----------|-------------|----------|")
    for t in active_tools:
        lats = [idx[(t, sid)].latency_ms
                for sid in sample_ids if (t, sid) in idx]
        if lats:
            lats_sorted = sorted(lats)
            mean = sum(lats) / len(lats)
            median = lats_sorted[len(lats_sorted) // 2]
            L(f"| {short.get(t,t)} | {len(lats)} | {mean:.0f} | "
              f"{median:.0f} | {max(lats):.0f} |")
    L("")

    # ---- § 5  Undetected by all ----
    L("## 5. Samples no tool detected")
    L("")
    hidden_samples = [s for s in samples if s["expected_hidden"]]
    missed_all = []
    for s in hidden_samples:
        sid = s["file"].replace(".pdf", "")
        if not any(
            idx.get((t, sid)) and idx[(t, sid)].detected
            for t in active_tools
        ):
            missed_all.append(sid)
    if missed_all:
        for sid in missed_all:
            L(f"- {sid}")
    else:
        L("(none)")
    L("")

    # ---- § 6  Detected by all ----
    L("## 6. Samples all tools detected")
    L("")
    caught_all = []
    for s in hidden_samples:
        sid = s["file"].replace(".pdf", "")
        if all(
            idx.get((t, sid)) and idx[(t, sid)].detected
            for t in active_tools
        ):
            caught_all.append(sid)
    if caught_all:
        for sid in caught_all:
            L(f"- {sid}")
    else:
        L("(none)")
    L("")

    # ---- § 7  Summary scorecard ----
    L("## 7. Summary scorecard")
    L("")
    L("| Tool | TP | FN | TN | FP | Precision | Recall | F1 |")
    L("|------|----|----|----|----|-----------|---------|----|")
    for t in active_tools:
        tp = fn = tn = fp = 0
        for s in samples:
            sid = s["file"].replace(".pdf", "")
            r = idx.get((t, sid))
            if r is None:
                continue
            v = _verdict(s["expected_hidden"], r.detected)
            if v == "TP":
                tp += 1
            elif v == "FN":
                fn += 1
            elif v == "TN":
                tn += 1
            elif v == "FP":
                fp += 1
        prec = tp / (tp + fp) if (tp + fp) else 0
        rec = tp / (tp + fn) if (tp + fn) else 0
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0
        L(f"| {short.get(t,t)} | {tp} | {fn} | {tn} | {fp} | "
          f"{prec:.3f} | {rec:.3f} | {f1:.3f} |")
    L("")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines))
    print(f"FINDINGS written: {path}")


# ---------------------------------------------------------------------------
# environment.json writer
# ---------------------------------------------------------------------------

def write_environment(
    tool_states: Dict[str, dict],
    path: Path,
) -> None:
    env = {
        "platform": {
            "os": platform.system(),
            "os_version": platform.version(),
            "arch": platform.machine(),
            "python": sys.version,
            "python_executable": sys.executable,
        },
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "tools": {},
    }
    for name, state in tool_states.items():
        entry = {
            "installed": state["venv"] is not None,
            "version": state.get("version", "unknown"),
            "venv": str(state["venv"]) if state["venv"] else None,
            "settings": TOOL_SETTINGS.get(name, {}),
        }
        env["tools"][name] = entry

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(env, f, indent=2, ensure_ascii=False)
    print(f"Environment written: {path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    if not MANIFEST_PATH.exists():
        print(f"Manifest not found: {MANIFEST_PATH}")
        return 1

    with open(MANIFEST_PATH) as f:
        manifest = json.load(f)
    samples = manifest["samples"]
    print(f"Loaded {len(samples)} samples from manifest.")

    # ---- Setup phase ----
    print("\n" + "=" * 60)
    print("SETUP: Installing tools in isolated venvs")
    print("=" * 60)

    tool_states: Dict[str, dict] = {}
    for tool_cfg in TOOLS:
        name = tool_cfg["name"]
        venv = tool_cfg["setup"]()
        version = "unknown"
        if venv and tool_cfg["pkg"] != "N/A":
            version = _pkg_version(venv, tool_cfg["pkg"])
        elif venv and name == "pdf-prompt-injection-toolkit":
            # No pip package; record git commit
            repo = VENVS_DIR / "pdf-prompt-injection-toolkit-repo"
            try:
                r = subprocess.run(
                    ["git", "rev-parse", "--short", "HEAD"],
                    capture_output=True, text=True, cwd=repo, timeout=5,
                )
                version = f"git:{r.stdout.strip()}"
            except Exception:
                version = "git:unknown"
        tool_states[name] = {"venv": venv, "version": version}
        status = f"v{version}" if venv else "FAILED"
        print(f"  → {name}: {status}")

    active = [(t, tool_states[t["name"]])
              for t in TOOLS if tool_states[t["name"]]["venv"]]
    if not active:
        print("\nNo tools installed successfully. Aborting.")
        return 1
    print(f"\n{len(active)} tool(s) ready.")

    # ---- Run phase ----
    print("\n" + "=" * 60)
    print("RUN: Scanning corpus with each tool")
    print("=" * 60)

    results: List[RunResult] = []
    for tool_cfg, state in active:
        name = tool_cfg["name"]
        venv = state["venv"]
        version = state["version"]
        timeout = tool_cfg["timeout"]
        run_fn = tool_cfg["run"]
        print(f"\n--- {name} (timeout={timeout}s) ---")

        for i, sample in enumerate(samples, 1):
            sid = sample["file"].replace(".pdf", "")
            pdf = CORPUS_DIR / sample["file"]
            if not pdf.exists():
                print(f"  [{i:2d}/{len(samples)}] {sid}: PDF missing")
                results.append(RunResult(
                    name, sid, sample["technique"],
                    sample["expected_hidden"], False,
                    "file_missing", 0, version,
                ))
                continue

            detected, det_name, latency = run_fn(venv, pdf, timeout)
            verdict = _verdict(sample["expected_hidden"], detected)
            mark = "+" if detected else "-"
            print(f"  [{i:2d}/{len(samples)}] {sid}: "
                  f"{verdict} ({mark}) {latency:.0f}ms"
                  + (f"  [{det_name}]" if det_name else ""))
            results.append(RunResult(
                name, sid, sample["technique"],
                sample["expected_hidden"], detected,
                det_name, round(latency, 1), version,
            ))

    # ---- Report phase ----
    print("\n" + "=" * 60)
    print("REPORT: Generating outputs")
    print("=" * 60)

    csv_path = RESULTS_DIR / "baseline_matrix.csv"
    write_csv(results, csv_path)

    tool_names = [t["name"] for t in TOOLS]
    tool_versions = {n: tool_states[n]["version"] for n in tool_names}
    write_findings(
        results, samples, tool_names, tool_versions,
        RESULTS_DIR / "FINDINGS.md",
    )

    write_environment(tool_states, RESULTS_DIR / "environment.json")

    # ---- Summary ----
    print("\n" + "=" * 60)
    for tool_cfg, state in active:
        name = tool_cfg["name"]
        tool_results = [r for r in results if r.tool == name]
        tp = sum(1 for r in tool_results
                 if r.expected_hidden and r.detected)
        fn = sum(1 for r in tool_results
                 if r.expected_hidden and not r.detected)
        fp = sum(1 for r in tool_results
                 if not r.expected_hidden and r.detected)
        tn = sum(1 for r in tool_results
                 if not r.expected_hidden and not r.detected)
        print(f"{name}: TP={tp} FN={fn} TN={tn} FP={fp}")
    print("=" * 60)

    return 0


if __name__ == "__main__":
    sys.exit(main())
