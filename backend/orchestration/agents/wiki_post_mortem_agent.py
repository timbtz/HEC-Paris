"""wiki_post_mortem — terminal agent on the reporting pipelines that drafts
a post-mortem markdown page from the run's anomalies + decision trace.

Source: PRD-AutonomousCFO §7.3 + Karpathy LLM-Wiki "ingest" operation
(`Orchestration/research/llm-wiki.md`). Runs after `flag-anomalies` /
`summarize-period` / `gate-confidence` (and `build-accrual-entry` for
year-end) so it has the full picture.

Design:
- The path and frontmatter are computed in code (deterministic — tests
  pin them and the writer tool can short-circuit on a malformed draft).
- The LLM only authors `title`, `body_md`, and decides whether a rule
  change must route through human ratification.
- The agent reads prior post-mortems for the same pipeline before
  drafting — that's the self-improvement loop. Run #1 has no prior
  context; run #2 sees run #1; by run #5 the wiki is dense.
- `requires_human_ratification=true` is reserved for proposed *changes*
  to existing `policies/*` pages. Filing a fresh observation under
  `post_mortems/...` stays auto.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from ..context import AgnesContext
from ..registries import default_runner, get_runner
from ..runners.base import AgentResult
from ..tools import wiki_reader as wiki_reader_tool
from ..wiki.schema import WikiFrontmatter


_SUBMIT_TOOL: dict[str, Any] = {
    "name": "submit_post_mortem",
    "description": (
        "Submit the post-mortem draft for this pipeline run. Use "
        "`requires_human_ratification=true` ONLY if you are proposing a "
        "change to an existing policies/* page — observations file "
        "automatically under post_mortems/."
    ),
    "input_schema": {
        "type": "object",
        "required": ["title", "body_md", "requires_human_ratification"],
        "properties": {
            "title": {
                "type": "string",
                "description": "Short title; will be the H1 of the wiki page.",
            },
            "body_md": {
                "type": "string",
                "description": (
                    "Markdown body. Cover (a) what was unclean, (b) "
                    "assumptions made, (c) where review was needed, "
                    "(d) any rule changes you would propose."
                ),
            },
            "requires_human_ratification": {
                "type": "boolean",
                "description": (
                    "True only when proposing a change to an existing "
                    "policies/* page; otherwise false."
                ),
            },
            "proposed_policy_path": {
                "type": ["string", "null"],
                "description": (
                    "When requires_human_ratification=true, the path of "
                    "the policies/* page you're proposing to change."
                ),
            },
        },
    },
}


_SYSTEM_PROMPT = (
    "You are the post-mortem author for an accounting agentic pipeline. "
    "You receive (a) the anomaly list flagged earlier in the run, (b) a "
    "compact period summary, (c) any prior post-mortems for this pipeline "
    "via the Living Rule Wiki. Draft a brief post-mortem covering: what "
    "was unclean, what assumptions you made, where human review was "
    "needed, any rule changes you would propose. Always call "
    "submit_post_mortem exactly once. If the run was clean, file a "
    "one-line 'clean run' observation."
)


def _today_period_label() -> str:
    """Fallback period folder when no period_id is available (YYYY-MM)."""
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _compute_path_and_frontmatter(
    ctx: AgnesContext,
) -> tuple[str, WikiFrontmatter, str | None]:
    """Deterministic path + frontmatter for this run's post-mortem page.

    Returns (path, frontmatter, period_label). The period_label is
    embedded in the title.
    """
    metadata = ctx.metadata if isinstance(ctx.metadata, dict) else {}
    payload = ctx.trigger_payload if isinstance(ctx.trigger_payload, dict) else {}
    period_id = (
        payload.get("period_id")
        or metadata.get("period_id")
        or _today_period_label()
    )
    period_id = str(period_id)

    applies_to = [ctx.pipeline_name, "post_mortem", period_id]
    jurisdictions = metadata.get("jurisdiction")
    fm = WikiFrontmatter(
        applies_to=applies_to,
        jurisdictions=[str(jurisdictions)] if jurisdictions else None,
        revision=1,
    )
    path = f"post_mortems/{period_id}/{ctx.pipeline_name}_{ctx.run_id}.md"
    return path, fm, period_id


def _synthesize_offline_body(
    ctx: AgnesContext, period_id: str, summary_json: str,
) -> str:
    """Deterministic fallback post-mortem body when no LLM was available.

    Renders a minimal markdown digest of the run signals so the wiki page
    still gets created in offline demos. Production runs (with API keys)
    always overwrite this with the LLM-authored body.
    """
    try:
        signals = json.loads(summary_json)
    except (TypeError, ValueError):
        signals = {}
    anomalies = signals.get("anomalies") or []
    overall = signals.get("overall_confidence")
    summary = signals.get("summary") or {}
    gate = signals.get("gate") or {}
    accrual = signals.get("accrual") or {}

    lines: list[str] = [
        f"_Auto-generated fallback post-mortem for **{ctx.pipeline_name}** "
        f"run {ctx.run_id} ({period_id}) — no LLM key available, body "
        f"synthesized from run signals._",
        "",
        "## Run signals",
        f"- Overall confidence: `{overall if overall is not None else 'n/a'}`",
        f"- Anomalies flagged: **{len(anomalies)}**",
    ]
    if gate:
        lines.append(f"- Gate decision: `{gate.get('decision', gate)}`")
    if isinstance(summary, dict) and summary:
        keys = ", ".join(sorted(summary.keys())[:6])
        lines.append(f"- Summary keys present: {keys}")
    if accrual:
        lines.append(f"- Accrual entry candidate: `{bool(accrual)}`")
    if anomalies:
        lines.append("")
        lines.append("## Anomalies")
        for a in anomalies[:10]:
            if isinstance(a, dict):
                kind = a.get("kind") or a.get("type") or "anomaly"
                msg = a.get("message") or a.get("reason") or ""
                lines.append(f"- **{kind}** — {msg}")
            else:
                lines.append(f"- {a}")
    else:
        lines.append("")
        lines.append("## Clean run")
        lines.append("No anomalies were flagged. Filing this as a clean-run observation.")
    return "\n".join(lines)


def _summarize_anomalies(ctx: AgnesContext) -> str:
    """Compact JSON snapshot of the upstream anomalies + period summary."""
    anomalies_node = ctx.get("flag-anomalies") or {}
    summary_node = ctx.get("summarize-period") or {}
    gate = ctx.get("gate-confidence") or {}
    accrual = ctx.get("build-accrual-entry") or {}

    payload: dict[str, Any] = {
        "anomalies": (
            anomalies_node.get("anomalies", []) if isinstance(anomalies_node, dict) else []
        ),
        "overall_confidence": (
            anomalies_node.get("overall_confidence")
            if isinstance(anomalies_node, dict)
            else None
        ),
        "summary": summary_node,
        "gate": gate,
    }
    if accrual:
        payload["accrual"] = accrual
    return json.dumps(payload, default=str, separators=(",", ":"))


async def run(ctx: AgnesContext) -> AgentResult:
    """Draft a post-mortem; return AgentResult whose `output` carries the page draft."""
    path, frontmatter, period_id = _compute_path_and_frontmatter(ctx)

    # Self-grounding loop — read prior post-mortems for this pipeline
    # before drafting. Run #1 finds nothing; run #2 sees run #1.
    prior = await wiki_reader_tool.fetch(
        ctx,
        tags=[ctx.pipeline_name, "post_mortem"],
        jurisdiction=(
            ctx.metadata.get("jurisdiction") if isinstance(ctx.metadata, dict) else None
        ),
    )
    prior_pages = prior.get("pages") or []
    wiki_references: list[tuple[int, int]] = [
        (int(p["page_id"]), int(p["revision_id"])) for p in prior_pages
    ]

    if prior_pages:
        prior_blocks = "\n\n".join(
            f"### {p['title']} ({p['path']}, rev {p['revision_number']})\n\n{p['body_md']}"
            for p in prior_pages
        )
        system = (
            f"{_SYSTEM_PROMPT}\n\n"
            "## Prior post-mortems for this pipeline (Living Rule Wiki)\n\n"
            f"{prior_blocks}"
        )
    else:
        system = _SYSTEM_PROMPT

    summary = _summarize_anomalies(ctx)
    user_content = (
        f"Pipeline: {ctx.pipeline_name}\n"
        f"Run id: {ctx.run_id}\n"
        f"Period: {period_id}\n"
        f"Target wiki path: {path}\n\n"
        "Run signals (JSON):\n"
        f"{summary}\n\n"
        "Call submit_post_mortem with the title, body_md, and "
        "requires_human_ratification fields."
    )

    runner = get_runner(default_runner())
    result = await runner.run(
        ctx=ctx,
        system=system,
        tools=[_SUBMIT_TOOL],
        messages=[{"role": "user", "content": user_content}],
        model="claude-sonnet-4-6",
        max_tokens=1500,
        temperature=0.0,
        wiki_context=wiki_references,
        deadline_s=15.0,
    )

    # Bundle the LLM's authored fields with the deterministic path /
    # frontmatter so wiki_writer can call upsert_page without re-deriving
    # them. We replace `result.output` via dict-merge here — the runner's
    # AgentResult is frozen, so use a shallow rebuild.
    parsed = result.output if isinstance(result.output, dict) else {}
    title = parsed.get("title") or f"{ctx.pipeline_name} run {ctx.run_id} — {period_id}"
    body_md = parsed.get("body_md") or ""
    requires_ratification = bool(parsed.get("requires_human_ratification", False))
    proposed_policy_path = parsed.get("proposed_policy_path")

    # Offline-demo fallback: if the LLM didn't (or couldn't) author a body
    # — common when ANTHROPIC_API_KEY is unset and the runner returns an
    # empty result — synthesize a deterministic post-mortem from the
    # signal payload so the wiki_writer doesn't skip the page entirely.
    # Production runs with a working key always overwrite this.
    if not body_md:
        body_md = _synthesize_offline_body(ctx, period_id, summary)

    draft = {
        "path": path,
        "title": title,
        "frontmatter": frontmatter.to_dict(),
        "body_md": body_md,
        "requires_human_ratification": requires_ratification,
        "proposed_policy_path": proposed_policy_path,
    }

    # Frozen-dataclass shallow rebuild — only `output` changes.
    from dataclasses import replace
    return replace(result, output=draft)
