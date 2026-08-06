"""Policy layer: per-span findings → document-level decision.

Separates threshold judgement (detector) from action decision (policy).
The detector classifies each span; the policy decides what to do about
the collection of classified spans.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import PolicyDecision, ScanResult, SpanFinding, Verdict


@dataclass
class PolicyConfig:
    """Policy behaviour switches."""

    block_on_hidden: bool = True
    warn_on_suspicious: bool = True


def evaluate(
    findings: list[SpanFinding],
    page_count: int,
    page_times: list[float] | None = None,
    config: PolicyConfig | None = None,
) -> ScanResult:
    """Evaluate classified findings into a document-level decision."""
    cfg = config or PolicyConfig()
    times = page_times or []

    hidden = [f for f in findings if f.verdict == Verdict.HIDDEN]
    suspicious = [f for f in findings if f.verdict == Verdict.SUSPICIOUS]

    if cfg.block_on_hidden and hidden:
        decision = PolicyDecision.BLOCK
    elif cfg.warn_on_suspicious and suspicious:
        decision = PolicyDecision.WARN
    else:
        decision = PolicyDecision.PASS

    return ScanResult(
        findings=findings,
        decision=decision,
        page_count=page_count,
        page_times=times,
        hidden_count=len(hidden),
        suspicious_count=len(suspicious),
        summary=_build_summary(hidden, suspicious, decision, len(findings)),
    )


def _build_summary(
    hidden: list[SpanFinding],
    suspicious: list[SpanFinding],
    decision: PolicyDecision,
    total: int,
) -> str:
    """Build a human-readable one-line + detail summary."""
    lines = [f"{decision.value.upper()} | {total} spans, "
             f"{len(hidden)} hidden, {len(suspicious)} suspicious"]

    for f in hidden[:5]:
        lines.append(
            f"  [HIDDEN] p{f.page} {f.technique} "
            f"{f.text[:40]!r} ({f.reason})"
        )
    if len(hidden) > 5:
        lines.append(f"  ... +{len(hidden) - 5} more hidden")

    for f in suspicious[:3]:
        lines.append(
            f"  [SUSPICIOUS] p{f.page} CR={f.cr:.2f} "
            f"ΔE={f.delta_e:.2f} {f.text[:40]!r}"
        )
    if len(suspicious) > 3:
        lines.append(f"  ... +{len(suspicious) - 3} more suspicious")

    return "\n".join(lines)
