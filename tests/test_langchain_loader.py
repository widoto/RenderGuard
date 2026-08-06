"""Tests for the LangChain SecureDocumentLoader integration."""

import json
from pathlib import Path

import pytest

from visual_sanitizer.core import PolicyConfig
from visual_sanitizer.integrators.langchain_loader import (
    HiddenTextBlockedError,
    LoaderConfig,
    SANITIZED_MARKER,
    SecureDocumentLoader,
)

CORPUS = Path(__file__).resolve().parent.parent / "corpus"
ARXIV = Path(__file__).resolve().parent.parent / "data" / "arxiv_papers"
PAYLOAD = "IGNORE_PREVIOUS_INSTRUCTIONS_MARKER_7F3A"

# Load manifest for payload-based tests
_manifest = json.loads((CORPUS / "manifest.json").read_text())
_HIDDEN_SAMPLES = [
    s for s in _manifest["samples"] if s["expected_hidden"]
]

# Core detector limitations: techniques it cannot detect.
# tiny_font — high-contrast text at unreadable size (CR=21, ΔE=100)
# offpage   — text positioned outside visible page area
_DETECTOR_BLIND_SPOTS = {"tiny_font_0_5pt.pdf", "offpage.pdf"}


def _load_sanitized(pdf_name: str) -> list:
    """Load a corpus PDF with block_on_hidden=False so we get Documents."""
    cfg = LoaderConfig(policy=PolicyConfig(block_on_hidden=False))
    loader = SecureDocumentLoader(CORPUS / pdf_name, config=cfg)
    return loader.load()


# ── Test 1: white-on-white hidden text is sanitized ──────────────────────


def test_hidden_white_on_white_sanitized():
    docs = _load_sanitized("white_on_white.pdf")
    assert len(docs) >= 1
    content = docs[0].page_content

    assert PAYLOAD not in content, "Hidden payload must not appear in page_content"
    assert SANITIZED_MARKER in content, "Sanitization marker must replace hidden text"
    assert docs[0].metadata["hidden_count"] >= 1


# ── Test 2: render-mode-3 invisible text is sanitized ────────────────────


def test_hidden_render_mode_3_sanitized():
    docs = _load_sanitized("render_mode_3.pdf")
    assert len(docs) >= 1
    content = docs[0].page_content

    assert PAYLOAD not in content
    assert docs[0].metadata["hidden_count"] >= 1


# ── Test 3: black-on-black hidden text is sanitized ──────────────────────


def test_hidden_black_on_black_sanitized():
    docs = _load_sanitized("black_on_black.pdf")
    assert len(docs) >= 1
    content = docs[0].page_content

    assert PAYLOAD not in content
    flagged = json.loads(docs[0].metadata["flagged_findings"])
    verdicts = [f["verdict"] for f in flagged]
    assert "hidden" in verdicts


# ── Test 4: normal gray caption text is preserved ────────────────────────


def test_normal_gray_caption_preserved():
    docs = _load_sanitized("gray_caption.pdf")
    assert len(docs) >= 1
    content = docs[0].page_content

    assert PAYLOAD in content, "Visible payload must be preserved in page_content"
    assert docs[0].metadata["hidden_count"] == 0
    assert docs[0].metadata["scan_decision"] == "pass"


# ── Test 5: BLOCK raises HiddenTextBlockedError ──────────────────────────


def test_block_raises_exception():
    cfg = LoaderConfig(
        policy=PolicyConfig(block_on_hidden=True),
        raise_on_block=True,
    )
    loader = SecureDocumentLoader(CORPUS / "white_on_white.pdf", config=cfg)

    with pytest.raises(HiddenTextBlockedError) as exc_info:
        loader.load()

    assert exc_info.value.scan_result is not None
    assert exc_info.value.scan_result.hidden_count >= 1


# ── Test 6: payload-based verification across all HIDDEN samples ─────────


@pytest.mark.parametrize(
    "sample",
    _HIDDEN_SAMPLES,
    ids=[s["file"] for s in _HIDDEN_SAMPLES],
)
def test_payload_absent_from_all_hidden_samples(sample):
    """The payload marker string must never appear in sanitized output.

    This is the ground-truth test: detection metadata (hidden_count) can
    claim a span was found, but the real safety guarantee is that the
    payload text itself is absent from page_content.

    Samples in _DETECTOR_BLIND_SPOTS use hiding techniques the
    contrast-based core detector cannot catch (tiny font, off-page).
    These are marked xfail to document the limitation without hiding it.
    """
    if sample["file"] in _DETECTOR_BLIND_SPOTS:
        pytest.xfail(
            f"core detector blind spot: {sample['technique']} "
            f"(not contrast/render-mode based)"
        )

    pdf_path = CORPUS / sample["file"]
    if not pdf_path.exists():
        pytest.skip(f"{sample['file']} not in corpus")

    docs = _load_sanitized(sample["file"])
    all_content = "\n".join(d.page_content for d in docs)

    # Primary assertion: payload is gone from text
    assert sample["payload"] not in all_content, (
        f"{sample['file']}: payload leaked through sanitization"
    )

    # Secondary: metadata agrees with text-level check
    total_hidden = sum(d.metadata["hidden_count"] for d in docs)
    assert total_hidden >= 1, (
        f"{sample['file']}: expected hidden_count >= 1, got {total_hidden}"
    )


# ── Test 7: bbox matching produces zero unmatched findings ───────────────


@pytest.mark.parametrize(
    "sample",
    _HIDDEN_SAMPLES[:3],
    ids=[s["file"] for s in _HIDDEN_SAMPLES[:3]],
)
def test_bbox_matching_no_unmatched(sample):
    """Every HIDDEN finding's bbox must match a text span exactly.

    An unmatched_hidden_count > 0 means the fail-safe kicked in, which
    is safe but indicates a bbox coordinate mismatch that should be
    investigated.
    """
    docs = _load_sanitized(sample["file"])
    for doc in docs:
        assert doc.metadata["unmatched_hidden_count"] == 0, (
            f"{sample['file']} page {doc.metadata['page']}: "
            f"bbox mismatch detected (unmatched={doc.metadata['unmatched_hidden_count']})"
        )


# ── Test 8: real arXiv paper smoke test ──────────────────────────────────


def test_arxiv_paper_smoke():
    """Load a real arXiv paper (no hidden text) and verify:
    - No exceptions raised
    - Body text is preserved (span count vs content length sanity)
    - Zero bbox mismatches
    - Decision is PASS
    """
    arxiv_pdf = ARXIV / "1706.03762.pdf"  # Attention Is All You Need
    if not arxiv_pdf.exists():
        pytest.skip("arXiv paper not available")

    cfg = LoaderConfig(policy=PolicyConfig(block_on_hidden=False))
    loader = SecureDocumentLoader(arxiv_pdf, config=cfg)
    docs = loader.load()

    assert len(docs) >= 1, "Must produce at least 1 page"

    # Content sanity: a real paper should have substantial text
    total_chars = sum(len(d.page_content) for d in docs)
    assert total_chars > 1000, f"Suspiciously little text: {total_chars} chars"

    # No hidden text in a real paper
    for doc in docs:
        assert doc.metadata["hidden_count"] == 0
        assert doc.metadata["unmatched_hidden_count"] == 0

    # Overall decision should be PASS
    assert docs[0].metadata["scan_decision"] == "pass"
