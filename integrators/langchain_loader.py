"""LangChain document loader with hidden-text detection and sanitization.

Wraps the visual_sanitizer core pipeline as a LangChain ``BaseLoader`` so
that PDFs are scanned for hidden text *before* they enter a RAG pipeline.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

try:
    from langchain_core.documents import Document
    from langchain_core.document_loaders import BaseLoader
except ImportError as exc:
    raise ImportError(
        "langchain-core is required for SecureDocumentLoader. "
        "Install it with:  pip install langchain-core"
    ) from exc

import pymupdf

from core import (
    DetectorConfig,
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

# ── Constants ────────────────────────────────────────────────────────────

SANITIZED_MARKER = "[SANITIZED_HIDDEN_TEXT]"

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────────


@dataclass
class LoaderConfig:
    """Configuration for :class:`SecureDocumentLoader`."""

    detector: DetectorConfig = field(default_factory=DetectorConfig)
    policy: PolicyConfig = field(default_factory=PolicyConfig)
    raise_on_block: bool = True
    sanitize_marker: str = SANITIZED_MARKER


# ── Exceptions ───────────────────────────────────────────────────────────


class HiddenTextBlockedError(Exception):
    """Raised when the document is BLOCK-ed and ``raise_on_block`` is True."""

    def __init__(self, scan_result: ScanResult) -> None:
        self.scan_result = scan_result
        hidden = scan_result.hidden_count
        super().__init__(
            f"Document blocked: {hidden} hidden text span(s) detected"
        )


# ── Internal helpers ─────────────────────────────────────────────────────


def _build_hidden_bboxes(
    findings: list[SpanFinding], page_num: int
) -> set[tuple]:
    """Return the set of bboxes classified as HIDDEN on *page_num*."""
    return {
        f.bbox
        for f in findings
        if f.page == page_num and f.verdict == Verdict.HIDDEN
    }


def _sanitize_page_text(
    page: pymupdf.Page,
    page_num: int,
    findings: list[SpanFinding],
    marker: str,
) -> tuple[str, int]:
    """Reconstruct page text, replacing HIDDEN spans with *marker*.

    Uses ``page.get_text("dict")`` so every span's bbox can be matched
    against findings by exact tuple equality (both originate from the
    same pymupdf ``page.get_text("dict")`` call on the unmodified PDF,
    producing identical PDF-point coordinates).

    **Fail-safe**: if any HIDDEN finding's bbox cannot be matched to a
    text span, the unmatched finding is logged as a warning and the
    marker is still injected at the end of the page text (conservative
    masking — fail-closed, never fail-open).
    """
    hidden_bboxes = _build_hidden_bboxes(findings, page_num)
    if not hidden_bboxes:
        return page.get_text(), 0

    matched_bboxes: set[tuple] = set()
    text_dict = page.get_text("dict")
    parts: list[str] = []

    for block in text_dict.get("blocks", []):
        if block.get("type") != 0:  # text blocks only
            continue
        block_lines: list[str] = []
        for line in block.get("lines", []):
            line_parts: list[str] = []
            for span in line.get("spans", []):
                bbox = tuple(span["bbox"])
                if bbox in hidden_bboxes:
                    matched_bboxes.add(bbox)
                    line_parts.append(marker)
                else:
                    line_parts.append(span.get("text", ""))
            block_lines.append("".join(line_parts))
        parts.append("\n".join(block_lines))

    # Fail-safe: detect unmatched HIDDEN findings
    unmatched = hidden_bboxes - matched_bboxes
    if unmatched:
        logger.warning(
            "page %d: %d HIDDEN finding(s) had no matching text span "
            "(bbox mismatch). Applying conservative masking.",
            page_num,
            len(unmatched),
        )
        # Inject marker for each unmatched finding so payload cannot
        # leak through silently (fail-closed).
        for bbox in unmatched:
            parts.append(marker)

    return "\n\n".join(parts), len(unmatched)


def _scan_result_to_metadata(scan_result: ScanResult) -> dict:
    """Serialize *scan_result* to a JSON-safe metadata dict.

    Only non-NORMAL findings are included to keep metadata compact.
    """
    flagged = [
        {
            "page": f.page,
            "bbox": list(f.bbox),
            "text": f.text,
            "verdict": f.verdict.value,
            "score": round(f.score, 4),
            "reason": f.reason,
            "cr": round(f.cr, 3) if f.cr is not None else None,
            "delta_e": round(f.delta_e, 3) if f.delta_e is not None else None,
        }
        for f in scan_result.findings
        if f.verdict != Verdict.NORMAL
    ]
    return {
        "scan_decision": scan_result.decision.value,
        "hidden_count": scan_result.hidden_count,
        "suspicious_count": scan_result.suspicious_count,
        "flagged_findings": json.dumps(flagged),
    }


# ── Loader ───────────────────────────────────────────────────────────────


class SecureDocumentLoader(BaseLoader):
    """LangChain loader that scans PDFs for hidden text before ingestion.

    Usage::

        loader = SecureDocumentLoader("report.pdf")
        docs = loader.load()          # raises on BLOCK by default

        # or: get sanitized output even when hidden text is found
        cfg = LoaderConfig(
            policy=PolicyConfig(block_on_hidden=False),
        )
        loader = SecureDocumentLoader("report.pdf", config=cfg)
        docs = loader.load()
    """

    def __init__(
        self,
        file_path: str | Path,
        config: LoaderConfig | None = None,
    ) -> None:
        self.file_path = Path(file_path)
        self.config = config or LoaderConfig()

    # -- public interface --------------------------------------------------

    def lazy_load(self) -> Iterator[Document]:
        """Scan, optionally block, then yield sanitized Documents."""
        scan_result = self._scan()

        if (
            scan_result.decision == PolicyDecision.BLOCK
            and self.config.raise_on_block
        ):
            raise HiddenTextBlockedError(scan_result)

        yield from self._build_documents(scan_result)

    # -- internals ---------------------------------------------------------

    def _scan(self) -> ScanResult:
        """Run the full core detection pipeline on the PDF."""
        findings, page_count, page_times = scan_document(
            self.file_path, config=self.config.detector
        )
        patterns = load_patterns()
        score_findings(findings, config=self.config.detector, patterns=patterns)
        return evaluate(
            findings, page_count, page_times, config=self.config.policy
        )

    def _build_documents(
        self, scan_result: ScanResult
    ) -> Iterator[Document]:
        """Open the PDF and yield one Document per page."""
        metadata_base = _scan_result_to_metadata(scan_result)
        doc = pymupdf.open(str(self.file_path))

        try:
            for page_num in range(len(doc)):
                page = doc[page_num]
                content, unmatched_count = _sanitize_page_text(
                    page,
                    page_num,
                    scan_result.findings,
                    self.config.sanitize_marker,
                )
                page_meta = {
                    **metadata_base,
                    "source": str(self.file_path),
                    "page": page_num,
                    "unmatched_hidden_count": unmatched_count,
                }
                yield Document(page_content=content, metadata=page_meta)
        finally:
            doc.close()
