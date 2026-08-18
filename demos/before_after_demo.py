"""RenderGuard Before/After Demo Script.

시연 영상용 데모: RenderGuard 없이 원본 PDF 텍스트를 LLM에 넣으면 숨은
지시를 따르고, RenderGuard로 소독하면 막히는 것을 나란히 보여준다.

이 스크립트는 **예시 시연** 목적이며, LLM 응답은 비결정적입니다.

Usage::

    # Replay 모드 (기본 — API 미호출)
    python demos/before_after_demo.py --mode replay

    # 특정 PDF 지정
    python demos/before_after_demo.py corpus/black_on_black.pdf --mode replay

    # API 모드 (환경변수로 키 제공)
    LLM_API_KEY=sk-... python demos/before_after_demo.py --mode api
"""

from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path

# ── sys.path setup ──────────────────────────────────────────────────────
_project_root = str(Path(__file__).resolve().parents[1])
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

import pymupdf

from core import (
    PolicyConfig,
    PolicyDecision,
    ScanResult,
    SpanFinding,
    Verdict,
    evaluate,
    load_patterns,
    scan_document,
    score_findings,
)

# ── ANSI color helpers ──────────────────────────────────────────────────

_USE_COLOR = sys.stdout.isatty()


def _sgr(code: str) -> str:
    return f"\033[{code}m" if _USE_COLOR else ""


RESET = _sgr("0")
BOLD = _sgr("1")
DIM = _sgr("2")

RED = _sgr("31")
GREEN = _sgr("32")
YELLOW = _sgr("33")
CYAN = _sgr("36")

RED_BG = _sgr("41;37")
GREEN_BG = _sgr("42;30")

# ── Constants ───────────────────────────────────────────────────────────

DEFAULT_PDF = Path("corpus/white_on_white.pdf")
DEFAULT_QUESTION = "이 논문을 심사해서 평가 점수를 매겨줘."
SANITIZED_MARKER = "[SANITIZED_HIDDEN_TEXT]"

# Replay 모드 고정 응답
_REPLAY_BEFORE = """\
이 논문은 매우 우수한 연구입니다.

**총평**: 본 논문은 혁신적인 접근 방식을 통해 기존 연구의 한계를 명확히
극복하였습니다. 방법론이 탄탄하고, 실험 설계가 정교하며, 결과의 재현성도
뛰어납니다.

**강점**:
- 독창적인 연구 주제 선정
- 체계적인 실험 설계와 풍부한 데이터 분석
- 명확한 논리 전개와 설득력 있는 결론

**약점**: 특별히 지적할 약점이 없습니다.

**점수: 9.5/10** — 게재 강력 추천"""

_REPLAY_AFTER = """\
본 논문을 검토한 결과를 아래와 같이 정리합니다.

**강점**:
- 연구 주제가 시의적절하며, 관련 선행 연구를 적절히 인용하고 있습니다.
- 실험 설계의 기본 구조는 합리적입니다.

**약점**:
- 비교 실험(baseline)이 부족하여 제안 방법의 우위를 판단하기 어렵습니다.
- 통계적 유의성 검정이 누락되어 결과의 신뢰도가 불확실합니다.
- 일부 실험 조건에 대한 설명이 불충분하여 재현이 어려울 수 있습니다.
- 관련 연구 비교 섹션에서 최근 2년 내 주요 논문이 빠져 있습니다.

**종합 평가**: 연구 아이디어는 흥미로우나, 실험의 엄밀성과 비교 분석을
보강해야 합니다.

**점수: 5.5/10** — 수정 후 재심사 권고"""

# ── LLM call ────────────────────────────────────────────────────────────


def call_llm(prompt: str, *, mode: str, label: str) -> str:
    """LLM 호출. api/replay 모드 분리.

    - mode="replay": label("before"|"after")로 미리 정의된 응답 반환
    - mode="api": 환경변수 기반 OpenAI-호환 API 호출

    환경변수 (api 모드):
        LLM_API_KEY  — API 키 (OPENAI_API_KEY 폴백)
        LLM_BASE_URL — 기본 https://api.openai.com/v1
        LLM_MODEL    — 기본 gpt-4o
    """
    if mode == "replay":
        return _REPLAY_BEFORE if label == "before" else _REPLAY_AFTER

    # ── api mode ──
    api_key = os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print(
            f"\n{YELLOW}LLM_API_KEY (또는 OPENAI_API_KEY) 환경변수가 "
            f"설정되지 않았습니다.{RESET}"
        )
        print(f"{DIM}replay 모드로 전환합니다: --mode replay{RESET}\n")
        return _REPLAY_BEFORE if label == "before" else _REPLAY_AFTER

    base_url = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = os.environ.get("LLM_MODEL", "gpt-4o")

    payload = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
        }
    ).encode()

    req = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=60) as resp:
            body = json.loads(resp.read())
        return body["choices"][0]["message"]["content"]
    except (urllib.error.URLError, KeyError, json.JSONDecodeError) as exc:
        return f"[API 호출 실패: {exc}]"


# ── PDF text extraction ────────────────────────────────────────────────


def extract_raw_text(pdf_path: Path) -> str:
    """pymupdf로 모든 페이지의 텍스트 추출 (숨은 텍스트 포함)."""
    doc = pymupdf.open(str(pdf_path))
    try:
        pages = [doc[i].get_text() for i in range(len(doc))]
    finally:
        doc.close()
    return "\n\n".join(pages)


# ── RenderGuard sanitization ───────────────────────────────────────────


def sanitize_with_renderguard(
    pdf_path: Path,
) -> tuple[str, ScanResult]:
    """SecureDocumentLoader로 스캔+소독. 소독된 텍스트와 ScanResult 반환.

    langchain-core 미설치 시 core API로 직접 소독 (폴백).
    반환되는 ScanResult는 기본 정책(block_on_hidden=True) 기준 판정.
    """
    # 먼저 core pipeline으로 스캔 결과 확보
    findings, page_count, page_times = scan_document(str(pdf_path))
    patterns = load_patterns()
    score_findings(findings, patterns=patterns)
    # 표시용: 기본 정책으로 판정 (block_on_hidden=True)
    scan_result = evaluate(findings, page_count, page_times)

    # SecureDocumentLoader 시도
    try:
        from integrators.langchain_loader import LoaderConfig, SecureDocumentLoader

        cfg = LoaderConfig(
            policy=PolicyConfig(block_on_hidden=False),
            raise_on_block=False,
        )
        loader = SecureDocumentLoader(pdf_path, config=cfg)
        docs = loader.load()
        sanitized_text = "\n\n".join(doc.page_content for doc in docs)
        return sanitized_text, scan_result
    except ImportError:
        pass

    # 폴백: pymupdf text dict에서 HIDDEN 스팬을 수동 치환
    sanitized_text = _fallback_sanitize(pdf_path, scan_result.findings)
    return sanitized_text, scan_result


def _fallback_sanitize(
    pdf_path: Path, findings: list[SpanFinding]
) -> str:
    """langchain-core 없이 core findings 기반으로 수동 소독."""
    hidden_bboxes_by_page: dict[int, set[tuple]] = {}
    for f in findings:
        if f.verdict == Verdict.HIDDEN:
            hidden_bboxes_by_page.setdefault(f.page, set()).add(f.bbox)

    doc = pymupdf.open(str(pdf_path))
    try:
        page_texts: list[str] = []
        for page_num in range(len(doc)):
            page = doc[page_num]
            hidden_bboxes = hidden_bboxes_by_page.get(page_num, set())
            if not hidden_bboxes:
                page_texts.append(page.get_text())
                continue

            text_dict = page.get_text("dict")
            parts: list[str] = []
            for block in text_dict.get("blocks", []):
                if block.get("type") != 0:
                    continue
                block_lines: list[str] = []
                for line in block.get("lines", []):
                    line_parts: list[str] = []
                    for span in line.get("spans", []):
                        bbox = tuple(span["bbox"])
                        if bbox in hidden_bboxes:
                            line_parts.append(SANITIZED_MARKER)
                        else:
                            line_parts.append(span.get("text", ""))
                    block_lines.append("".join(line_parts))
                parts.append("\n".join(block_lines))
            page_texts.append("\n\n".join(parts))
    finally:
        doc.close()
    return "\n\n".join(page_texts)


# ── Output formatting ──────────────────────────────────────────────────


def _print_header(mode: str) -> None:
    bar = "=" * 59
    print(f"\n{BOLD}{bar}{RESET}")
    print(f"{BOLD}  RenderGuard — Before/After Demo{RESET}")
    if mode == "replay":
        print(
            f"  {YELLOW}[재현 시연]{RESET} "
            f"미리 정의된 응답을 사용합니다 (API 미호출)"
        )
    else:
        print(f"  {CYAN}[API 모드]{RESET} 실제 LLM API를 호출합니다")
    print(f"{BOLD}{bar}{RESET}\n")


def _print_scan_result(scan_result: ScanResult) -> None:
    bar = "─── RenderGuard 스캔 결과 " + "─" * 33
    print(f"{BOLD}{bar}{RESET}")

    decision = scan_result.decision.value.upper()
    if scan_result.decision == PolicyDecision.BLOCK:
        decision_str = f"{RED}{BOLD}{decision}{RESET}"
    elif scan_result.decision == PolicyDecision.WARN:
        decision_str = f"{YELLOW}{BOLD}{decision}{RESET}"
    else:
        decision_str = f"{GREEN}{BOLD}{decision}{RESET}"

    print(f"판정: {decision_str} | 숨은 텍스트 {scan_result.hidden_count}건 탐지")

    hidden_findings = [
        f for f in scan_result.findings if f.verdict == Verdict.HIDDEN
    ]
    if hidden_findings:
        print("숨은 지시 내용:")
        for f in hidden_findings:
            text_preview = f.text[:80] + ("..." if len(f.text) > 80 else "")
            print(f'  → "{text_preview}"')
    print()


def _print_before(raw_text: str, response: str) -> None:
    bar = "─── BEFORE: RenderGuard 없이 " + "─" * 30
    print(f"{RED_BG}{BOLD}{bar}{RESET}")

    text_preview = raw_text.strip()[:200]
    print(f"{DIM}LLM이 받은 입력: 원본 텍스트 (숨은 지시 포함){RESET}")
    print(f'{DIM}  "{text_preview}..."{RESET}\n')

    print(f"{RED}응답:{RESET}")
    for line in response.splitlines():
        print(f"  {RED}{line}{RESET}")
    print(f"\n  {RED}{BOLD}⚠ 숨은 지시를 따른 것으로 보입니다{RESET}\n")


def _print_after(sanitized_text: str, response: str) -> None:
    bar = "─── AFTER: RenderGuard 적용 " + "─" * 31
    print(f"{GREEN_BG}{BOLD}{bar}{RESET}")

    text_preview = sanitized_text.strip()[:200]
    marker_note = (
        f" ({SANITIZED_MARKER}로 치환)"
        if SANITIZED_MARKER in sanitized_text
        else ""
    )
    print(f"{DIM}LLM이 받은 입력: 소독된 텍스트{marker_note}{RESET}")
    print(f'{DIM}  "{text_preview}..."{RESET}\n')

    print(f"{GREEN}응답:{RESET}")
    for line in response.splitlines():
        print(f"  {GREEN}{line}{RESET}")
    print(f"\n  {GREEN}{BOLD}✓ 정상적인 평가{RESET}\n")


def _print_summary() -> None:
    bar = "─── 비교 요약 " + "─" * 45
    print(f"{BOLD}{bar}{RESET}")
    print(f"{RED}BEFORE:{RESET} 숨은 지시에 영향받은 응답")
    print(f"{GREEN}AFTER:{RESET}  소독 후 정상 응답")
    print()
    print(
        f"{DIM}* 이 시연은 예시이며, LLM 응답은 비결정적입니다.\n"
        f"  결과가 기대와 다를 경우 payload나 질문을 조정하세요.{RESET}\n"
    )


# ── Main ────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="RenderGuard Before/After 시연 스크립트 (예시 데모)",
    )
    parser.add_argument(
        "pdf",
        nargs="?",
        default=str(DEFAULT_PDF),
        help="숨겨진 프롬프트 인젝션이 포함된 PDF 경로 (기본: %(default)s)",
    )
    parser.add_argument(
        "--mode",
        choices=["api", "replay"],
        default="replay",
        help="api: 실제 API 호출 | replay: 미리 정의된 응답 (기본: replay)",
    )
    parser.add_argument(
        "--step",
        action="store_true",
        help="촬영용: 각 섹션 후 Enter 대기 (한 화면씩 보여주기)",
    )
    args = parser.parse_args()

    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        print(f"{RED}파일을 찾을 수 없습니다: {pdf_path}{RESET}", file=sys.stderr)
        sys.exit(1)

    mode = args.mode
    step = args.step

    def _pause() -> None:
        if step:
            input(f"{DIM}  ▸ Enter를 누르면 다음 단계로 진행합니다...{RESET}")
            print()

    # ── Header ──
    _print_header(mode)
    print(f"입력: {pdf_path}\n")

    # ── Scan ──
    sanitized_text, scan_result = sanitize_with_renderguard(pdf_path)
    _print_scan_result(scan_result)
    _pause()

    # ── BEFORE ──
    raw_text = extract_raw_text(pdf_path)
    before_prompt = f"다음 논문 내용을 읽고, {DEFAULT_QUESTION}\n\n{raw_text}"
    before_response = call_llm(before_prompt, mode=mode, label="before")
    _print_before(raw_text, before_response)
    _pause()

    # ── AFTER ──
    after_prompt = f"다음 논문 내용을 읽고, {DEFAULT_QUESTION}\n\n{sanitized_text}"
    after_response = call_llm(after_prompt, mode=mode, label="after")
    _print_after(sanitized_text, after_response)
    _pause()

    # ── Summary ──
    _print_summary()


if __name__ == "__main__":
    main()
