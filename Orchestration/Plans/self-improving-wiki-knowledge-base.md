# Feature: Self-Improving Wiki Knowledge Base for Reporting Pipelines

The following plan should be complete, but it is important that you validate documentation, codebase patterns, and task sanity before you start implementing. Pay special attention to naming of existing utils, types, and models — import from the right files (`backend.orchestration.wiki.*`, `backend.orchestration.tools.wiki_reader`, `backend.orchestration.runners.base.AgentResult`, etc.).

> **Re-discover the wiki at runtime.** This plan deliberately does **not** enumerate which markdown pages exist. Pages are discovered each call via `wiki.loader.load_pages_for_tags` (frontmatter-tag set-intersection over the latest revision of every row in `wiki_pages`). The corpus is allowed to grow between runs — new policies, new post-mortems, new runbooks added since the last execution must be picked up automatically. Anything that hardcodes page paths or assumes a fixed file list is a regression. Search behaviour (`wiki_search` tool, §STEP-BY-STEP) must walk the live `wiki_revisions` table, never an in-memory snapshot.

## Feature Description

The `backend/orchestration/wiki/` module already ships the schema, loader, writer, and an Anthropic-side `wiki_reader` tool wired into `gl_account_classifier_agent`. This feature closes the loop: it makes the wiki **self-improving**, **fully cited**, and **reachable from every reasoning agent and every reporting pipeline**.

Concretely, this feature delivers:

1. **Cache + prompt-hash correctness.** Today `anthropic_runner.py:129` and `pydantic_ai_runner` compute `prompt_hash` *without* `wiki_context`, and `executor.py:201` computes `cache_key` *without* `wiki_context`. The infrastructure is in place (both functions accept it as an optional arg) but agents don't thread it. A wiki edit therefore does not invalidate cached agent calls — a real correctness bug. We fix the runner contract so the citation is part of the hash by construction.
2. **Three more agents wired** — `counterparty_classifier`, `anomaly_flag_agent`, `document_extractor` — using the same pattern as `gl_account_classifier_agent.py:139-167`.
3. **A `wiki_search` tool** — BM25 over `wiki_revisions.body_md` with `(page_id, revision_id)` citations, so agents and pipelines can do free-text grep across the corpus, not just frontmatter-tag lookup. Backed by SQLite FTS5 (no new infra; same `orchestration.db`).
4. **A `wiki_post_mortem` agent + tool** — at the end of `period_close`, `vat_return`, `year_end_close`, this writes back into the wiki. Two flavours:
   - *Auto-append observations* (run-id, anomalies, low-confidence rates, what went into review) → new immutable page under `post_mortems/{period}/{pipeline}_{run_id}.md` with `applies_to: [{pipeline}, post_mortem]` so the next run reads it.
   - *Drafts of rule changes* — never auto-committed. Drafts a markdown diff against an existing `policies/*.md` page, queues it for CFO ratification via the existing `review_queue`. The CFO commits via the new `PUT /wiki/pages/{page_id}` endpoint (which is the only path that actually mutates a `policies/*` page).
5. **Reporting-pipeline wiki integration** — add a `read-wiki` node early in `period_close.yaml`, `vat_return.yaml`, `year_end_close.yaml` so `flag-anomalies` (and downstream agents) inherit the latest policy + post-mortem context.
6. **A `log.md` append-on-edit + `index.md` rebuild** — Karpathy LLM-wiki convention (`Orchestration/research/llm-wiki.md`). These two files are auto-maintained by the writer so the corpus is navigable to humans and future agents.
7. **`PUT /wiki/pages/{page_id}` endpoint** — currently `backend/api/wiki.py` only ships GETs. We add the write endpoint so the CFO/auditor can edit policies (and ratify post-mortem drafts) from the frontend.

## User Story

As Marie (founder-CFO of a 100-person FR/DE scale-up),
I want every reporting agent (anomaly flagging, GL classification, counterparty match, document extraction) to read the current Living Rule Wiki **and** to write its own learnings back into it after a run,
So that the second time I run a period close, the system already remembers the corner cases the auditor flagged the first time — without me having to re-explain anything.

## Problem Statement

The current wiki integration is one-way and partial:

- **Read side**: only `gl_account_classifier_agent` reads the wiki. `counterparty_classifier`, `anomaly_flag_agent`, `document_extractor` do not. The reporting pipelines (`period_close.yaml`, `vat_return.yaml`, `year_end_close.yaml`) have no wiki node, so their `flag-anomalies` agent is uninformed.
- **Cache + hash bug**: agents stamp `wiki_references` onto `AgentResult` *after* the runner has already computed `prompt_hash`. The hash is therefore stale relative to the wiki citation, and `executor._run_node` never threads the agent's wiki context into `cache.cache_key`. A wiki edit does **not** invalidate cached agent decisions today.
- **Write side**: nothing writes back. The "self-improving" loop the user described — anomalies / low-confidence outcomes / human resolutions feeding the next run — does not exist. Each pipeline run is amnesia.
- **Search**: the only retrieval today is `applies_to` set-intersection. There is no free-text path for cases where the right tag isn't known up-front.
- **Discoverability**: there is no `index.md` / `log.md`, so neither humans nor agents can quickly survey what the corpus contains.
- **Edit surface**: `backend/api/wiki.py` ships only GETs; the frontend can read pages but not write them.

## Solution Statement

A focused six-part patch (no new database, no new framework):

1. **Thread `wiki_context` end-to-end.** Add `wiki_context` kwarg to the `AgentRunner.run` Protocol (`backend/orchestration/runners/base.py:47-62`). Both `AnthropicRunner` and `PydanticAiRunner` pass it to `prompt_hash` (already supports it: `prompt_hash.py:25`) and stamp it onto the returned `AgentResult.wiki_references` (already a field: `runners/base.py:44`). Executor passes it into `cache.cache_key` (already supports it: `cache.py:71`). Agents stop using `dataclasses.replace` for the citation; the runner owns it.
2. **Wire the three remaining agents** to call `wiki_reader_tool.fetch` with role-appropriate tags before constructing the system prompt — mirroring `gl_account_classifier_agent.py:139-167`.
3. **Add `tools.wiki_search:run`** backed by SQLite FTS5 over a contentless virtual table on `wiki_revisions.body_md`. Returns `(page_id, revision_id, snippet, score)` for the latest revision per matching page. Pipeline-callable and importable from agents.
4. **Add `agents.wiki_post_mortem:run` + `tools.wiki_writer:run`** as terminal nodes on the three reporting pipelines. The agent reads the run's anomalies + decision traces from `audit.db`, drafts a markdown post-mortem, and the tool calls `wiki.upsert_page` to file it under `post_mortems/{period}/{pipeline}_{run_id}.md`. Rule-change drafts route to `review_queue` instead.
5. **Patch `period_close.yaml` / `vat_return.yaml` / `year_end_close.yaml`** with a `read-wiki` node early (depends on nothing; informs `flag-anomalies`) and a `write-post-mortem` node terminal.
6. **Add `PUT /wiki/pages/{page_id}` + `POST /wiki/pages` + auto-maintain `index.md` and `log.md`** so the human side of the loop closes — CFO can ratify drafts, log captures every edit.

The critical invariant: every wiki interaction is *re-discovered* per call. The loader joins `wiki_pages × wiki_revisions` on `MAX(revision_number)` so newly added markdown files (added via `PUT /wiki/pages` or via the `wiki_post_mortem` agent) are visible on the very next call. There is no in-memory cache of "the wiki structure".

## Feature Metadata

**Feature Type**: Enhancement
**Estimated Complexity**: Medium (≈10 file edits, 4 new files, 6 new tests, no new dependencies, no new DB schema beyond a single FTS5 virtual table migration)
**Primary Systems Affected**: `backend/orchestration/{runners,executor.py,tools,agents,pipelines,wiki}`, `backend/api/wiki.py`, three reporting YAML pipelines.
**Dependencies**: None new. SQLite FTS5 ships with the system `sqlite3` (≥3.40, already required by `CLAUDE.md`).

---

## CONTEXT REFERENCES

### Relevant Codebase Files — IMPORTANT: YOU MUST READ THESE BEFORE IMPLEMENTING

**Wiki module (already shipped — do not duplicate)**

- `backend/orchestration/wiki/schema.py:27-50` — `WikiFrontmatter` dataclass + `applies_to`/`agent_input_for`/`jurisdictions` fields. Use as-is.
- `backend/orchestration/wiki/schema.py:105-143` — `parse_frontmatter` for any path that round-trips markdown text → frontmatter.
- `backend/orchestration/wiki/loader.py:35-103` — `load_pages_for_tags(orchestration_db, tags, jurisdiction)` — the canonical "give me all latest-revision pages whose `applies_to` overlaps these tags" call. **Use this everywhere.**
- `backend/orchestration/wiki/loader.py:106-187` — `resolve_references(orchestration_db, refs)` — converts `(page_id, revision_id)` pairs to display dicts. Used by the executor for the `agent.decision` event.
- `backend/orchestration/wiki/writer.py:31-109` — `upsert_page(orchestration_db, lock, *, path, title, frontmatter, body_md, author)` — single chokepoint for ALL wiki writes. Returns `(page_id, revision_id)`. **Every write goes through this; never INSERT into `wiki_pages` directly.**
- `backend/orchestration/wiki/__init__.py:11-21` — public surface; add `WikiSearchHit` here when the search tool lands.
- `backend/orchestration/tools/wiki_reader.py:26-87` — the reference fetch+stash pattern. The new agents must call `await wiki_reader_tool.fetch(ctx, tags=[...], jurisdiction=...)`.
- `backend/orchestration/agents/gl_account_classifier_agent.py:139-204` — the reference wiring. Key lines:
  - `:142` — pull jurisdiction from `ctx.metadata`.
  - `:143-147` — call `wiki_reader_tool.fetch` with role-tags.
  - `:149-151` — extract `(page_id, revision_id)` pairs.
  - `:153-170` — splice page bodies under `## Policy reference (Living Rule Wiki)` heading.
  - `:203-204` — currently uses `dataclasses.replace` to stamp `wiki_references`. **DELETE this; the runner will own it once §STEP-BY-STEP §1 lands.**

**Cache + hash plumbing (already shipped — wire it up)**

- `backend/orchestration/prompt_hash.py:20-50` — `prompt_hash(model, system, tools, messages, wiki_context=None)` — already accepts `wiki_context: Iterable[tuple[int, int]] | None`. Sorts ascending. Don't touch.
- `backend/orchestration/cache.py:68-92` — `cache_key(node_id, canonical_input, wiki_context=None)` — already accepts `wiki_context`. Don't touch.
- `backend/orchestration/audit.py:23-106` — `propose_checkpoint_commit(... wiki_references=None)` already stores the first `(page_id, revision_id)` pair on `agent_decisions`. `wiki_references=None` falls back to `result.wiki_references`. Don't touch.

**Runner contract (must change)**

- `backend/orchestration/runners/base.py:47-62` — `AgentRunner` Protocol. **Add `wiki_context` kwarg.**
- `backend/orchestration/runners/base.py:25-44` — `AgentResult` dataclass; `wiki_references` is field on line 44. The runner will populate this directly after the change.
- `backend/orchestration/runners/anthropic_runner.py:113-199` — `AnthropicRunner.run`. Line 129 currently `prompt_hash(model, system, tools, messages)` — add `wiki_context=wiki_context`. Lines 168-181 (timeout path) and 187-198 (success path) both need to populate `wiki_references=list(wiki_context or [])` on the returned `AgentResult`.
- `backend/orchestration/runners/pydantic_ai_runner.py` — apply identical changes (read this file before editing).

**Executor (must change)**

- `backend/orchestration/executor.py:198-212` — `_run_node` cache miss path. Line 201 currently `cache_mod.cache_key(node.id, canonical_input)`. We need to thread the agent's `wiki_context` from `ctx.metadata["wiki_references"]` into this call. Two-pass interaction with the cache:
  - **Pre-dispatch read**: agents may call `wiki_reader_tool.fetch` *during* dispatch, so we cannot know `wiki_context` for cache lookup at line 201. Decision: **agents are not cacheable** (`cacheable=false` is already the convention; verify via `dag/yaml_loader.py`). The cache-key wiki threading therefore matters only for tool nodes that explicitly reference wiki output via deps — handled implicitly because the tool's deps include `read-wiki` whose output is part of `canonical_input`. **No code change needed at executor.py:201 if agents stay non-cacheable.** Add a brief comment confirming this.
- `backend/orchestration/executor.py:255-313` — `_dispatch_agent`. The wiki citation flow is already correct here: `propose_checkpoint_commit` reads `result.wiki_references`; `_resolved_wiki_citations` (`:520-560`) joins to `wiki_revisions` for the SSE event. **Nothing to change here once the runner stamps `wiki_references` correctly.**
- `backend/orchestration/executor.py:487-560` — `_wiki_citations_for_result` + `_resolved_wiki_citations` already exist. Reference, don't touch.

**Agents to wire (currently NOT wired)**

- `backend/orchestration/agents/counterparty_classifier.py:172-222` — read the prompt-construction path. Insert `wiki_reader_tool.fetch(ctx, tags=["counterparties", "classification"], jurisdiction=...)` before `runner.run` (≈ between current lines 189 and 191). Splice into `_SYSTEM_PROMPT` (line 26) only when matches exist.
- `backend/orchestration/agents/anomaly_flag_agent.py:58-129` — reads three upstream tool outputs to build the period summary. Insert `wiki_reader_tool.fetch(ctx, tags=["anomaly_detection", ctx.pipeline_name], jurisdiction=...)` before line 121. Splice into the system prompt under the same `## Policy reference (Living Rule Wiki)` heading.
- `backend/orchestration/agents/document_extractor.py` — read this file end-to-end before editing. Vision agent. Tags should be `["document_extraction", "ocr"]` plus optionally `[document_kind]` from upstream context.

**Reporting pipelines to patch**

- `backend/orchestration/pipelines/period_close.yaml` — add `read-wiki` (no deps) and have `flag-anomalies` depend on `[..., read-wiki]`. Add terminal `write-post-mortem` depending on `[summarize-period, gate-confidence]`.
- `backend/orchestration/pipelines/vat_return.yaml` — same shape.
- `backend/orchestration/pipelines/year_end_close.yaml` — same shape; the post-mortem also should observe `build-accrual-entry` outcome.

**Audit + DB**

- `backend/orchestration/store/migrations/audit/0004_wiki_citations.py` — already adds `agent_decisions.wiki_page_id, wiki_revision_id`. Reference, don't duplicate.
- `backend/orchestration/store/migrations/orchestration/0002_wiki_pages.py` + `0003_wiki_revisions.py` — schema for the corpus. The new FTS5 migration must be `0004_wiki_fts.py` (next free number; verify by `ls backend/orchestration/store/migrations/orchestration/`).

**API**

- `backend/api/wiki.py:1-217` — currently GET-only. Add `PUT /wiki/pages/{page_id}` and `POST /wiki/pages` mirroring the GET-revision response shape. Body validation: title (str), body_md (str), frontmatter (dict matching `WikiFrontmatter.from_dict`). Author from auth context (or fallback `"cfo"` until auth lands).

**Tests — patterns to mirror**

- `backend/tests/test_gl_classifier_wiki_injection.py:25-86` — the canonical wiki-injection test: creates a page, runs the agent against `fake_anthropic`, asserts (a) wiki body appears in `system` prompt, (b) `result.wiki_references == [(page_id, revision_id)]`, (c) the no-match case behaves exactly like the pre-wiki agent.
- `backend/tests/test_wiki_loader.py` — read pattern for fixture setup.
- `backend/tests/test_wiki_writer_revisions.py` — pattern for revision-bump assertions.
- `backend/tests/test_cache_invalidation_wiki.py` — pattern for cache-invalidation-on-wiki-edit assertions. **Critical reference**: replicate this approach for the three newly wired agents.
- `backend/tests/test_prompt_hash_wiki_threading.py` — pattern for asserting `prompt_hash` differs between two wiki revisions.
- `backend/tests/test_dag_events_wiki_citations.py` — pattern for asserting the `agent.decision` SSE event ships `wiki_citations`.
- `backend/tests/test_wiki_api.py` — pattern for the GET endpoints. Mirror for the new PUT/POST.

**Frontend (out of scope for this plan, but stay non-breaking)**

- `frontend-lovable/src/components/fingent/NodeTraceDrawer.tsx` — consumes `wiki_citations` from the SSE event. Don't change the event shape; only add fields if absolutely necessary.
- `frontend-lovable/src/pages/WikiPage.tsx` — uses `GET /wiki/pages` + `GET /wiki/pages/{id}`. The new PUT/POST will be wired in a follow-up; this plan only ships the backend endpoints.

### New Files to Create

- `backend/orchestration/store/migrations/orchestration/0004_wiki_fts.py` — FTS5 virtual table over `wiki_revisions.body_md` for the search tool.
- `backend/orchestration/tools/wiki_search.py` — BM25 search tool: `async def fetch(ctx, *, query, limit=10) -> {hits: [...]}` plus `async def run(ctx) -> dict` for the YAML pipeline path (query in `trigger_payload["wiki_query"]` or `metadata["wiki_query"]`).
- `backend/orchestration/tools/wiki_writer.py` — pipeline-tool entry point: reads draft markdown + frontmatter from `ctx.get("draft-post-mortem")`, calls `wiki.writer.upsert_page`, returns `{page_id, revision_id, path}`.
- `backend/orchestration/agents/wiki_post_mortem_agent.py` — drafts post-mortem markdown from the run's anomalies + decision traces; returns an `AgentResult` whose `output` is `{path, title, frontmatter, body_md, requires_human_ratification: bool}`. Tags: `applies_to: [{pipeline_name}, post_mortem, {period_id}]`.
- `backend/orchestration/wiki/maintenance.py` — small module with `async def append_log(orchestration_db, lock, *, entry: str)` and `async def rebuild_index(orchestration_db, lock)`. Called from `writer.upsert_page` (a one-line side-effect after the revision INSERT) so `log.md` and `index.md` stay current automatically.
- `backend/tests/test_runner_wiki_context_threading.py` — proves the runner now threads `wiki_context` into `prompt_hash` and stamps `wiki_references`.
- `backend/tests/test_counterparty_classifier_wiki_injection.py` — mirror of `test_gl_classifier_wiki_injection.py`.
- `backend/tests/test_anomaly_flag_wiki_injection.py` — same pattern.
- `backend/tests/test_document_extractor_wiki_injection.py` — same pattern, possibly with vision-agent fixture.
- `backend/tests/test_wiki_search.py` — FTS5 query roundtrip + ranking + revision-pinning.
- `backend/tests/test_wiki_post_mortem.py` — runs `period_close` end-to-end against a seeded fixture, asserts a new page lands under `post_mortems/`, asserts the next run's `read-wiki` picks it up.
- `backend/tests/test_wiki_api_write.py` — PUT/POST happy-path + frontmatter validation + revision-bump assertion.

### Relevant Documentation — YOU SHOULD READ THESE BEFORE IMPLEMENTING

- `Orchestration/research/llm-wiki.md` — **read end-to-end first**. The `index.md` / `log.md` / "ingest / query / lint" three-operation model is the design north-star. Specifically:
  - §"Indexing and logging" — defines what `index.md` and `log.md` should look like (entry prefix `## [YYYY-MM-DD] ingest | <title>` is parseable with `grep`). Use this exact prefix in `wiki/maintenance.py:append_log`.
  - §"Ingest / Query / Lint" — the three operations the wiki must support. Our `wiki_post_mortem` agent is an *ingest*; `wiki_search` is *query*; the *lint* job is deferred to a follow-up.
  - §"The wiki is a persistent, compounding artifact." — the rule that justifies the post-mortem write-back loop.
- `Orchestration/PRDs/PRD-AutonomousCFO.md` — the overarching feature PRD.
  - §6 "DEFERRED RESEARCH" lines 330-334 — the open questions this plan resolves: wiki injection point per agent, prompt-hash threading, cache-invalidation correctness.
  - §7.3 "The Living Rule Wiki" lines 394-429 — the canonical contract.
  - §15 D "Open research items" lines 877-890 — items #1, #3, #9 are addressed here.
- `Orchestration/PRDs/RealMetaPRD.md` §7.5 (audit.db schema), §7.8 (`prompt_hash` formula), §6.4 line 525 (`cache_key` formula). Don't mutate any of these contracts; only thread `wiki_context` through them.
- `CLAUDE.md` — re-read the **Hard rules** section. Especially: (a) money is integer cents, (b) all DB writes through `store.writes.write_tx`, (c) `gl_poster.post` is the chokepoint for `journal_entries`, (d) `--workers 1` invariant. The wiki writer already obeys (b); the post-mortem path must not write to `journal_entries`.
- SQLite FTS5 docs — https://www.sqlite.org/fts5.html
  - §"Contentless FTS5 Tables" — we want `content=''` to avoid duplicating the body, with explicit `INSERT INTO wiki_fts(rowid, body_md) VALUES (NEW.id, NEW.body_md)` triggers on `wiki_revisions`.
  - §"The bm25() Function" — ranking. Lower score = better match. Use `ORDER BY bm25(wiki_fts) LIMIT ?`.

### Patterns to Follow

**Naming conventions (from codebase scan)**

- Tool module names: `tools/<thing>.py` with `async def fetch(ctx, *, ...) -> dict` for in-process callers and `async def run(ctx) -> dict` for YAML-pipeline entry. See `tools/wiki_reader.py:26` and `:74`.
- Agent module names: `agents/<thing>_agent.py` with `async def run(ctx: FingentContext) -> AgentResult`. See `agents/gl_account_classifier_agent.py:95` and `agents/anomaly_flag_agent.py:58`.
- Migration filenames: `00NN_<lower_snake_description>.py` matching the convention in `store/migrations/orchestration/`.
- Test filenames: `test_<unit_under_test>.py` colocated under `backend/tests/`.

**Wiki injection pattern (verbatim from `gl_account_classifier_agent.py:139-170`)**

```python
jurisdiction = ctx.metadata.get("jurisdiction") if isinstance(ctx.metadata, dict) else None
wiki_payload = await wiki_reader_tool.fetch(
    ctx,
    tags=["<role-tag-1>", "<role-tag-2>"],
    jurisdiction=jurisdiction,
)
wiki_pages = wiki_payload.get("pages") or []
wiki_references: list[tuple[int, int]] = [
    (int(p["page_id"]), int(p["revision_id"])) for p in wiki_pages
]

base_system = "<existing system prompt>"
if wiki_pages:
    policy_blocks = "\n\n".join(
        f"### {p['title']} ({p['path']}, rev {p['revision_number']})\n\n{p['body_md']}"
        for p in wiki_pages
    )
    system = f"{base_system}\n\n## Policy reference (Living Rule Wiki)\n\n{policy_blocks}"
else:
    system = base_system
```

**Runner-call pattern (the NEW shape after §STEP-BY-STEP §1 lands)**

```python
result = await runner.run(
    ctx=ctx,
    system=system,
    tools=[tool],
    messages=messages,
    model=model,
    max_tokens=...,
    temperature=0.0,
    wiki_context=wiki_references,   # NEW — threads into prompt_hash + result.wiki_references
)
# DO NOT use dataclasses.replace to stamp wiki_references — the runner does it now.
```

**Single-chokepoint write pattern (from `wiki/writer.py:53`)**

```python
async with write_tx(orchestration_db, lock) as conn:
    await conn.execute(...)
```

Every new wiki-mutating path obeys this. The `wiki.writer.upsert_page` already does — call it; do not write SQL directly.

**Test pattern (from `test_gl_classifier_wiki_injection.py:25-59`)**

```python
async def test_<agent>_wiki_injection(store, fake_anthropic, fake_anthropic_message):
    page_id, revision_id = await upsert_page(
        store.orchestration, store.orchestration_lock,
        path="policies/<applicable>.md",
        title="<title>",
        frontmatter=WikiFrontmatter(applies_to=["<role-tag>"], revision=1),
        body_md="SENTINEL_BODY_TOKEN — <distinctive content>.",
        author="test",
    )
    calls, fake = fake_anthropic
    fake.messages._response = fake_anthropic_message(
        tool_input={...}, tool_name="submit_<thing>",
    )
    ctx = _ctx(store, node_outputs={...})
    result = await run(ctx)

    system_text = calls[0].get("system") or ""
    assert "## Policy reference (Living Rule Wiki)" in system_text
    assert "SENTINEL_BODY_TOKEN" in system_text
    assert list(result.wiki_references) == [(page_id, revision_id)]
```

---

## IMPLEMENTATION PLAN

### Phase 1: Foundation — Runner contract + cache correctness

Get the wiki citation into `prompt_hash` and `AgentResult.wiki_references` *by construction*, so every subsequent agent automatically inherits correct cache-invalidation semantics.

**Tasks:**

- Extend `AgentRunner.run` Protocol with `wiki_context: Iterable[tuple[int, int]] | None = None`.
- Update `AnthropicRunner.run` and `PydanticAiRunner.run` to: (a) pass `wiki_context` to `prompt_hash`, (b) populate `wiki_references` on the returned `AgentResult`.
- Update `gl_account_classifier_agent` to pass `wiki_context=wiki_references` and remove the post-hoc `dataclasses.replace`.
- Update `executor._run_node` cache-key path to confirm agents stay non-cacheable (and document why — agents fetch wiki in-flight, so wiki state must be reflected via the dep graph, not the cache key).

### Phase 2: Wire remaining reasoning agents

Bring `counterparty_classifier`, `anomaly_flag_agent`, `document_extractor` to parity with `gl_account_classifier_agent`.

**Tasks:**

- Mirror the wiki-injection block (see §Patterns) into each agent's `run`. Tags chosen per agent role.
- Each call passes `wiki_context=wiki_references` to its runner.

### Phase 3: Search + write-back tools and agent

Build the two new ingest/query primitives so the wiki becomes navigable and self-improving.

**Tasks:**

- New migration `0004_wiki_fts.py` — FTS5 contentless virtual table on `wiki_revisions.body_md` plus `AFTER INSERT` and `AFTER UPDATE` triggers. Backfill from existing rows.
- New `tools/wiki_search.py` — `fetch(ctx, *, query, limit=10)` returning `{hits: [{page_id, revision_id, revision_number, path, title, snippet, score}]}` for the latest revision per matching page. `run(ctx)` reads `query` from `trigger_payload["wiki_query"]` / `metadata["wiki_query"]`. Stashes hits' `(page_id, revision_id)` on `ctx.metadata["wiki_references"]` so a downstream agent can credit the citation.
- New `agents/wiki_post_mortem_agent.py` — reads `audit.db`'s `agent_decisions` for the current `run_id`, plus the `flag-anomalies` output via `ctx.get("flag-anomalies")`. Drafts:
  - `path = f"post_mortems/{period_or_today}/{ctx.pipeline_name}_{ctx.run_id}.md"`
  - `title = f"{ctx.pipeline_name} run {ctx.run_id} — {period_label}"`
  - `frontmatter = WikiFrontmatter(applies_to=[ctx.pipeline_name, "post_mortem", period_id], jurisdictions=[...], revision=1)`
  - `body_md` includes: per-anomaly detail with confidence, prompt_hash citations to the Sonnet/Cerebras decisions, links to the human-review queue rows when applicable, `**Open question**:` sections for things the agent could not resolve.
  - `output = {path, title, frontmatter (dict), body_md, requires_human_ratification: bool}`. The bool flips true when the agent wants to *change* an existing `policies/*` page rather than file a new observation; in that case `wiki_writer` skips the write and enqueues a `review_queue` row instead.
- New `tools/wiki_writer.py` — pipeline-tool entry point. Reads `ctx.get("draft-post-mortem")`. If `requires_human_ratification`, enqueue review and return `{enqueued: true}`. Otherwise call `wiki.writer.upsert_page`; return `{page_id, revision_id, path}`.
- New `wiki/maintenance.py` — `append_log` and `rebuild_index`. Both go through `write_tx` (`store/writes.py`). Both treat `index.md` / `log.md` themselves as wiki pages (path `index.md`, `log.md`) so `wiki_pages` remains the single source of truth and the GET endpoints already render them.
- Patch `wiki/writer.py:upsert_page` to call `await append_log(...)` after each successful upsert (one-line side-effect; soft-fails so a maintenance bug never crashes the actual edit). Keep `index.md` rebuild on a debounce — only call `rebuild_index` when the upserted page is new (not just a revision bump) to keep the hot path light.

### Phase 4: Reporting-pipeline integration

Make the three closing pipelines context-aware and self-improving.

**Tasks:**

- Add `read-wiki` node (tool: `tools.wiki_reader:run`) at the top of `period_close.yaml`, `vat_return.yaml`, `year_end_close.yaml`. Pass `trigger_payload.wiki_tags = [<pipeline_name>, "anomaly_detection"]`.
- Make `flag-anomalies` depend on `[..., read-wiki]`; the agent's existing `wiki_reader_tool.fetch` call from §Phase 2 will inherit the same context.
- Add terminal `draft-post-mortem` (agent: `agents.wiki_post_mortem:run`) depending on `[summarize-period, gate-confidence]`.
- Add terminal `write-post-mortem` (tool: `tools.wiki_writer:run`) depending on `[draft-post-mortem]`.
- For `year_end_close.yaml`, also make `draft-post-mortem` depend on `build-accrual-entry` so accrual decisions are observed.

### Phase 5: Edit surface — API + frontmatter validation

Close the loop by making the wiki editable from the existing GET-only API.

**Tasks:**

- Add `POST /wiki/pages` (create new page; body `{path, title, body_md, frontmatter}`).
- Add `PUT /wiki/pages/{page_id}` (write a new revision; body `{title, body_md, frontmatter}`).
- Both endpoints validate `frontmatter` via `WikiFrontmatter.from_dict` (raises `ValueError` → return 400). Both call `wiki.writer.upsert_page` so `index.md` / `log.md` updates flow automatically.
- Author field: take from `request.headers.get("x-fingent-author")` for now; falls back to `"cfo"`. Auth integration is out of scope.

### Phase 6: Tests + validation

**Tasks:**

- Add the six new test files listed in §New Files to Create.
- Run the full suite (background, per `CLAUDE.md`) and triage regressions.

---

## STEP-BY-STEP TASKS

IMPORTANT: Execute every task in order, top to bottom. Each task is atomic and independently testable.

### Task 1 — UPDATE `backend/orchestration/runners/base.py`

- **IMPLEMENT**: Add `wiki_context: Iterable[tuple[int, int]] | None = None` kwarg to the `AgentRunner.run` Protocol (after `seed`).
- **PATTERN**: mirror the existing optional-kwarg style on the Protocol (`runners/base.py:50-62`).
- **IMPORTS**: add `from typing import Iterable` if not already present.
- **GOTCHA**: `AgentResult` already has `wiki_references` field (`runners/base.py:44`). Do not re-add it.
- **VALIDATE**: `python -c "from backend.orchestration.runners.base import AgentRunner; help(AgentRunner.run)"` shows `wiki_context` in signature.

### Task 2 — UPDATE `backend/orchestration/runners/anthropic_runner.py`

- **IMPLEMENT**:
  - Add `wiki_context: Iterable[tuple[int, int]] | None = None` kwarg (after `seed`).
  - Line 129: change to `ph = prompt_hash(model, system, tools, messages, wiki_context=wiki_context)`.
  - In both `AgentResult(...)` constructions (lines 168 timeout path and 187 success path), add `wiki_references=list(wiki_context or [])`.
- **PATTERN**: `prompt_hash.py:25` already accepts `wiki_context`; just thread it.
- **IMPORTS**: `from typing import Iterable` at top.
- **GOTCHA**: Sort-stability is handled inside `prompt_hash`; pass the iterable as-is.
- **VALIDATE**: `python -m pytest backend/tests/test_prompt_hash_wiki_threading.py -x` passes.

### Task 3 — UPDATE `backend/orchestration/runners/pydantic_ai_runner.py`

- **IMPLEMENT**: Same three changes as Task 2.
- **PATTERN**: identical surface; this file already imports `prompt_hash`.
- **GOTCHA**: This runner is wrapped around raw `AsyncOpenAI` (Cerebras compat). Confirm there's no separate `prompt_hash` call site beyond the one matching anthropic_runner — read the file before editing.
- **VALIDATE**: `python -m pytest backend/tests/test_prompt_hash_wiki_threading.py -x` still passes.

### Task 4 — CREATE `backend/tests/test_runner_wiki_context_threading.py`

- **IMPLEMENT**: Two-runner parametrized test.
  - Build a fake page via `upsert_page`, call `runner.run(..., wiki_context=[(page_id, revision_id)])` against `fake_anthropic`.
  - Assert `result.prompt_hash` differs from a control call with `wiki_context=None` or with a different revision pair.
  - Assert `result.wiki_references == [(page_id, revision_id)]`.
- **PATTERN**: `backend/tests/test_prompt_hash_wiki_threading.py` (existing) for fixture shape.
- **IMPORTS**: `from backend.orchestration.runners.anthropic_runner import AnthropicRunner`; `from backend.orchestration.runners.pydantic_ai_runner import PydanticAiRunner` (skip the cerebras case if no key in env via `pytest.importorskip` or `skipif`).
- **GOTCHA**: Don't depend on a real network key. The Anthropic case uses the existing `fake_anthropic` fixture; the Cerebras case can patch the OpenAI client similarly or skip.
- **VALIDATE**: `python -m pytest backend/tests/test_runner_wiki_context_threading.py -x`.

### Task 5 — UPDATE `backend/orchestration/agents/gl_account_classifier_agent.py`

- **IMPLEMENT**:
  - Pass `wiki_context=wiki_references` into `runner.run(...)` (around current line 190).
  - Delete lines 200-204 (the `dataclasses.replace` post-hoc stamp). The runner now does it.
- **PATTERN**: see §Patterns "Runner-call pattern".
- **IMPORTS**: remove unused `import dataclasses` if no other use remains.
- **GOTCHA**: `wiki_references` is a `list[tuple[int, int]]`. Pass as-is.
- **VALIDATE**: `python -m pytest backend/tests/test_gl_classifier_wiki_injection.py -x` still passes (test asserts `result.wiki_references == [(page_id, revision_id)]`; behavior unchanged for callers).

### Task 6 — UPDATE `backend/orchestration/agents/counterparty_classifier.py`

- **IMPLEMENT**: Mirror §Patterns "Wiki injection pattern" with tags `["counterparties", "classification"]`. Splice into a system prompt that combines `_SYSTEM_PROMPT` (line 26) with the policy block. Pass `wiki_context=wiki_references` into `runner.run`.
- **PATTERN**: `gl_account_classifier_agent.py:139-170`.
- **IMPORTS**: `from ..tools import wiki_reader as wiki_reader_tool`.
- **GOTCHA**: Currently `_SYSTEM_PROMPT` is a module-level constant. Build the system string locally per-call; do not mutate the constant.
- **VALIDATE**: `python -m pytest backend/tests/test_counterparty_classifier_wiki_injection.py -x` (created in Task 13).

### Task 7 — UPDATE `backend/orchestration/agents/anomaly_flag_agent.py`

- **IMPLEMENT**: Same wiki block, tags `["anomaly_detection", ctx.pipeline_name]` plus optional `period_id` from `ctx.metadata` if present. Splice under the existing system prompt (line 97). Pass `wiki_context=wiki_references`.
- **PATTERN**: same.
- **IMPORTS**: `from ..tools import wiki_reader as wiki_reader_tool`.
- **GOTCHA**: This agent runs in three different pipelines (`period_close`, `vat_return`, `year_end_close`). Tagging by `ctx.pipeline_name` lets a CFO write a `vat_return`-specific anomaly rule that *doesn't* leak into the unrelated `period_close` anomaly pass.
- **VALIDATE**: `python -m pytest backend/tests/test_anomaly_flag_wiki_injection.py -x` (created in Task 14).

### Task 8 — UPDATE `backend/orchestration/agents/document_extractor.py`

- **IMPLEMENT**: Same block, tags `["document_extraction", "ocr"]` plus `[document_kind]` from `ctx.trigger_payload.get("document_kind")` if present. Pass `wiki_context=wiki_references`.
- **PATTERN**: same. Vision agent — keep wiki splice in the textual system prompt, not the image part.
- **GOTCHA**: This agent uses Anthropic-only (vision); never gets routed through Cerebras. The wiki call still works the same way; just the runner-selection branch in the agent is shorter.
- **VALIDATE**: `python -m pytest backend/tests/test_document_extractor_wiki_injection.py -x` (created in Task 15).

### Task 9 — CREATE `backend/orchestration/store/migrations/orchestration/0004_wiki_fts.py`

- **IMPLEMENT**: FTS5 virtual table + triggers + backfill.
  - `CREATE VIRTUAL TABLE wiki_fts USING fts5(body_md, content='', tokenize='porter unicode61')`.
  - `AFTER INSERT ON wiki_revisions BEGIN INSERT INTO wiki_fts(rowid, body_md) VALUES (NEW.id, NEW.body_md); END;`
  - `AFTER UPDATE ON wiki_revisions BEGIN INSERT INTO wiki_fts(wiki_fts, rowid, body_md) VALUES('delete', OLD.id, OLD.body_md); INSERT INTO wiki_fts(rowid, body_md) VALUES (NEW.id, NEW.body_md); END;`
  - `AFTER DELETE ON wiki_revisions BEGIN INSERT INTO wiki_fts(wiki_fts, rowid, body_md) VALUES('delete', OLD.id, OLD.body_md); END;`
  - Backfill: `INSERT INTO wiki_fts(rowid, body_md) SELECT id, body_md FROM wiki_revisions`.
- **PATTERN**: existing migrations under `store/migrations/orchestration/0001..0003.py` for the `apply(conn)` callable shape.
- **GOTCHA**: Contentless tables (`content=''`) require manual sync; the triggers above keep them coherent. Confirm SQLite ≥3.40 supports FTS5 (check `sqlite3.sqlite_version_info` in a test helper).
- **VALIDATE**: `python -c "import sqlite3; conn = sqlite3.connect(':memory:'); conn.execute('CREATE VIRTUAL TABLE t USING fts5(x)'); print('fts5 ok')"`. Then `python -m pytest backend/tests/test_wiki_search.py -x` (created in Task 16).

### Task 10 — CREATE `backend/orchestration/tools/wiki_search.py`

- **IMPLEMENT**:
  - `async def fetch(ctx, *, query: str, limit: int = 10) -> dict` — runs `SELECT r.id, r.page_id, r.body_md, snippet(wiki_fts, 0, '«', '»', '…', 16) AS snippet, bm25(wiki_fts) AS score FROM wiki_fts JOIN wiki_revisions r ON r.id = wiki_fts.rowid JOIN wiki_pages p ON p.id = r.page_id WHERE wiki_fts MATCH ? AND r.revision_number = (SELECT MAX(revision_number) FROM wiki_revisions WHERE page_id = r.page_id) ORDER BY bm25(wiki_fts) LIMIT ?`. Return `{hits: [{page_id, revision_id, revision_number, path, title, snippet, score}]}`.
  - Stash `(page_id, revision_id)` pairs onto `ctx.metadata["wiki_references"]` (extend, don't replace) so a downstream agent's wiki citation list includes search hits.
  - `async def run(ctx) -> dict` — reads `query` from `trigger_payload["wiki_query"]` or `metadata["wiki_query"]`.
- **PATTERN**: `tools/wiki_reader.py:26-87` (fetch + run twin).
- **IMPORTS**: same set as `wiki_reader.py`.
- **GOTCHA**: FTS5 query syntax — escape user input by passing as parameter, never by string concatenation. Reject queries containing only stopwords (FTS5 returns empty silently — that's fine).
- **VALIDATE**: `python -m pytest backend/tests/test_wiki_search.py -x`.

### Task 11 — REGISTER `tools.wiki_search:run`

- **IMPLEMENT**: Add `"tools.wiki_search:run": "backend.orchestration.tools.wiki_search:run"` to `_TOOL_REGISTRY` in `backend/orchestration/registries.py:15-18`.
- **PATTERN**: existing `wiki_reader` registration on line 17.
- **VALIDATE**: `python -c "from backend.orchestration.registries import get_tool; assert get_tool('tools.wiki_search:run')"`.

### Task 12 — CREATE `backend/orchestration/wiki/maintenance.py`

- **IMPLEMENT**:
  - `async def append_log(orchestration_db, lock, *, entry: str)` — reads current `log.md` via the loader (latest revision of `path='log.md'`), appends `entry` (which the caller pre-formats as `## [YYYY-MM-DD HH:MM] <kind> | <title>\n<one-line summary>\n\n`), calls `wiki.writer.upsert_page` to create the next revision. Soft-fails (try/except, log warning) so a maintenance bug never crashes the underlying `upsert_page` call.
  - `async def rebuild_index(orchestration_db, lock)` — reads every `wiki_pages` row + head revision metadata, generates a markdown table grouped by top-level directory, calls `upsert_page` on `path='index.md'`. Idempotent — only writes a new revision when the rendered body actually changed (compare against existing latest body).
- **PATTERN**: §Karpathy LLM-wiki, `Orchestration/research/llm-wiki.md` "Indexing and logging".
- **IMPORTS**: `from .writer import upsert_page`; `from .loader import load_pages_for_tags`.
- **GOTCHA**: `index.md` and `log.md` are themselves wiki pages — their revisions trigger `append_log` again. Guard with a recursion sentinel: `if path in {"index.md", "log.md"}: return` inside `append_log`.
- **VALIDATE**: write a small inline test inside the writer test or a new `test_wiki_maintenance.py`.

### Task 13 — UPDATE `backend/orchestration/wiki/writer.py`

- **IMPLEMENT**: After the `INSERT INTO wiki_revisions` (around current line 104), call `await append_log(orchestration_db, lock, entry=...)` and (when `prev is None`) `await rebuild_index(...)`. Both wrapped in `try/except Exception as exc: logger.warning(...)`.
- **PATTERN**: same write_tx scope; the maintenance calls happen *after* the commit (outside `write_tx`) to avoid recursive lock acquisition — `wiki/maintenance.py` opens its own `write_tx`.
- **IMPORTS**: `from . import maintenance`; `import logging`.
- **GOTCHA**: Recursion guard from Task 12 prevents `index.md`/`log.md` upserts from triggering more upserts.
- **VALIDATE**: `python -m pytest backend/tests/test_wiki_writer_revisions.py -x` (existing) still passes; a new revision of any non-meta page produces a new revision of `log.md`.

### Task 14 — CREATE `backend/orchestration/agents/wiki_post_mortem_agent.py`

- **IMPLEMENT**: `async def run(ctx) -> AgentResult`.
  - Reads anomalies via `ctx.get("flag-anomalies")` (the upstream agent's `output`).
  - Reads decision-trace summary via a small SELECT on `audit.db` for `run_id=ctx.run_id` (count, models, total cost).
  - Builds a tool `submit_post_mortem` with input schema `{path: str, title: str, frontmatter: object, body_md: str, requires_human_ratification: bool}`.
  - System prompt instructs: "Draft a brief post-mortem covering: (a) what was unclean, (b) what assumptions you made, (c) where human review was needed, (d) any rule changes you would propose. Set requires_human_ratification=true ONLY if you propose changing an existing policies/* page; otherwise file a new observation under post_mortems/."
  - Returns `AgentResult` whose `output` is the parsed tool input.
- **PATTERN**: `gl_account_classifier_agent.py` for the runner-call shape; `anomaly_flag_agent.py` for the prompt-build shape.
- **IMPORTS**: standard agent imports; `from ..tools import wiki_reader as wiki_reader_tool` (the post-mortem agent itself reads existing post-mortems for the same pipeline as context — i.e. it grounds in *prior* lessons before writing the new one).
- **GOTCHA**: Self-grounding loop — when no prior post-mortems exist, `wiki_reader_tool.fetch` returns `[]`, agent runs unprimed. After the first run, subsequent runs see the previous post-mortem. This is the self-improvement mechanic.
- **VALIDATE**: `python -m pytest backend/tests/test_wiki_post_mortem.py -x`.

### Task 15 — REGISTER `agents.wiki_post_mortem:run`

- **IMPLEMENT**: Add to `_AGENT_REGISTRY` in `registries.py:20-22`.
- **VALIDATE**: `python -c "from backend.orchestration.registries import get_agent; assert get_agent('agents.wiki_post_mortem:run')"`.

### Task 16 — CREATE `backend/orchestration/tools/wiki_writer.py`

- **IMPLEMENT**: `async def run(ctx) -> dict`.
  - Pulls draft from `ctx.get("draft-post-mortem")` (the agent's output).
  - If `requires_human_ratification`, INSERT a `review_queue` row with the proposed diff (path + body_md) and return `{enqueued: true, page_id: null, revision_id: null}`.
  - Otherwise call `await wiki.writer.upsert_page(...)` with the draft fields and return `{enqueued: false, page_id, revision_id, path}`.
- **PATTERN**: `tools/wiki_reader.py` for the `run(ctx)` shape; `wiki/writer.py` for the upsert call.
- **IMPORTS**: `from ..wiki import upsert_page, WikiFrontmatter`.
- **GOTCHA**: `frontmatter` arrives as a dict (JSON-serialized through the agent tool boundary). Pass through `WikiFrontmatter.from_dict(...)` to validate before calling `upsert_page`. Author = `"agent:wiki_post_mortem"`.
- **VALIDATE**: `python -m pytest backend/tests/test_wiki_post_mortem.py -x`.

### Task 17 — REGISTER `tools.wiki_writer:run`

- **IMPLEMENT**: Add to `_TOOL_REGISTRY` in `registries.py`.
- **VALIDATE**: `python -c "from backend.orchestration.registries import get_tool; assert get_tool('tools.wiki_writer:run')"`.

### Task 18 — UPDATE `backend/orchestration/pipelines/period_close.yaml`

- **IMPLEMENT**: Add nodes:
  ```yaml
    - id: read-wiki
      tool: tools.wiki_reader:run
    - id: draft-post-mortem
      agent: agents.wiki_post_mortem:run
      runner: anthropic
      depends_on: [summarize-period, gate-confidence, flag-anomalies]
    - id: write-post-mortem
      tool: tools.wiki_writer:run
      depends_on: [draft-post-mortem]
  ```
  And add `read-wiki` to `flag-anomalies.depends_on`.
- **PATTERN**: existing YAML node shape (period_close.yaml:5-31).
- **GOTCHA**: The `read-wiki` node needs `wiki_tags` in `trigger_payload`. The pipeline trigger today doesn't pass tags. Add `read-wiki` configured to read `metadata.wiki_tags = ["period_close", "anomaly_detection"]` — but `tools.wiki_reader:run` reads from `trigger_payload`/`metadata` only. Cleanest fix: have the period_close caller (`/reports/period_close` or wherever it's triggered) pass `wiki_tags` in the trigger payload. Document this in the YAML as a `# expects trigger_payload.wiki_tags` comment.
- **VALIDATE**: `python -c "from backend.orchestration.yaml_loader import load; from pathlib import Path; print(load(Path('backend/orchestration/pipelines/period_close.yaml')))"` does not raise.

### Task 19 — UPDATE `backend/orchestration/pipelines/vat_return.yaml`

- **IMPLEMENT**: Same shape as Task 18, with `wiki_tags = ["vat_return", "anomaly_detection"]`.
- **VALIDATE**: as Task 18.

### Task 20 — UPDATE `backend/orchestration/pipelines/year_end_close.yaml`

- **IMPLEMENT**: Same shape as Task 18; `draft-post-mortem.depends_on` additionally includes `build-accrual-entry`. Tags `["year_end_close", "anomaly_detection"]`.
- **VALIDATE**: as Task 18.

### Task 21 — UPDATE `backend/api/wiki.py`

- **IMPLEMENT**:
  - `POST /wiki/pages` — body `{path, title, body_md, frontmatter}`. Validates frontmatter via `WikiFrontmatter.from_dict`. Calls `upsert_page`. Returns `{page_id, revision_id, path, revision_number}`.
  - `PUT /wiki/pages/{page_id}` — body `{title, body_md, frontmatter}`. Looks up `path` from `wiki_pages WHERE id=?` first; 404 if missing. Calls `upsert_page` with that path. Same response shape.
  - Author: `request.headers.get("x-fingent-author") or "cfo"`.
- **PATTERN**: existing GET handlers in `backend/api/wiki.py:54-217`.
- **IMPORTS**: `from pydantic import BaseModel, Field`; `from backend.orchestration.wiki import upsert_page, WikiFrontmatter`.
- **GOTCHA**: The store handles + locks live on `request.app.state.store`. Look at `backend/api/wiki.py:63-64` for the access pattern.
- **VALIDATE**: `python -m pytest backend/tests/test_wiki_api_write.py -x`.

### Task 22 — CREATE the six test files

- **IMPLEMENT**: per §New Files to Create. Mirror `test_gl_classifier_wiki_injection.py` style for the three agent tests; mirror `test_wiki_writer_revisions.py` style for the maintenance / search / post-mortem tests; mirror `test_wiki_api.py` style for the write-API test.
- **PATTERN**: see §Patterns "Test pattern" + the explicit references in §Relevant Codebase Files.
- **GOTCHA**: `pytest.ini` enforces 15s per-test (`CLAUDE.md`). The post-mortem test runs a real pipeline; keep the trigger payload minimal and use `background=False` (`executor.execute_pipeline(..., background=False)`) so the test deterministically awaits.
- **VALIDATE**: `python -m pytest backend/tests/test_runner_wiki_context_threading.py backend/tests/test_counterparty_classifier_wiki_injection.py backend/tests/test_anomaly_flag_wiki_injection.py backend/tests/test_document_extractor_wiki_injection.py backend/tests/test_wiki_search.py backend/tests/test_wiki_post_mortem.py backend/tests/test_wiki_api_write.py -x`.

### Task 23 — Final regression

- **IMPLEMENT**: Run the full suite in background per `CLAUDE.md` rules; confirm zero new failures vs `master` baseline.
- **VALIDATE**: see §VALIDATION COMMANDS Level 2.

---

## TESTING STRATEGY

The codebase uses `pytest` with `asyncio_mode=auto` and a 15s per-test ceiling enforced by `pytest-timeout` (see `pytest.ini`, `CLAUDE.md`). Tests live under `backend/tests/`.

### Unit Tests

- **Runner threading** — wiki_context flows into `prompt_hash` and out as `result.wiki_references`. Two-runner (Anthropic + Cerebras) parametrization where possible.
- **Per-agent injection** — for each of `counterparty_classifier`, `anomaly_flag_agent`, `document_extractor`: assert wiki body lands in system prompt and `wiki_references` is populated. Mirror `test_gl_classifier_wiki_injection.py`.
- **Search tool** — FTS5 query roundtrip; revision-pinning (only latest revision per page returned); ranking sanity (more-relevant page scores lower).
- **Maintenance** — `append_log` adds a line per upsert; `rebuild_index` is idempotent on no-change.
- **API** — POST creates page, PUT bumps revision, frontmatter validation returns 400.

### Integration Tests

- **End-to-end period_close with wiki self-improvement** — seed an empty wiki, run `period_close` with anomalies in the seed, assert a `post_mortems/...` page now exists with the right tags. Run `period_close` *again* and assert the new `read-wiki` node returns the prior post-mortem. This is the self-improvement loop in one test.
- **Cache invalidation under wiki edit** — variant of `test_cache_invalidation_wiki.py` for each newly wired agent.

### Edge Cases

- Empty wiki on first run → all agents behave identically to pre-wiki.
- Wiki edit between two runs of the same pipeline → second run sees the new revision in its `wiki_citations` SSE event.
- Post-mortem agent proposes `requires_human_ratification=true` → `wiki_writer` enqueues review, no page is upserted.
- Pipeline run with zero anomalies → post-mortem still files a one-line "clean run" observation (so the log exists for future reference). Or: skip the `write-post-mortem` node via a `when:` condition. Decision: skip — a `conditions.reporting:has_post_mortem_content` predicate. Add to `conditions/reporting.py`.
- FTS5 query with SQL-injection payload → parameterized query rejects.
- Wiki page with malformed YAML frontmatter → `parse_frontmatter` raises `ValueError`; the API returns 400 cleanly.

---

## VALIDATION COMMANDS

Execute every command to ensure zero regressions and 100% feature correctness.

### Level 1: Syntax & Style

```bash
python -m py_compile backend/orchestration/runners/anthropic_runner.py \
  backend/orchestration/runners/pydantic_ai_runner.py \
  backend/orchestration/runners/base.py \
  backend/orchestration/agents/gl_account_classifier_agent.py \
  backend/orchestration/agents/counterparty_classifier.py \
  backend/orchestration/agents/anomaly_flag_agent.py \
  backend/orchestration/agents/document_extractor.py \
  backend/orchestration/agents/wiki_post_mortem_agent.py \
  backend/orchestration/tools/wiki_search.py \
  backend/orchestration/tools/wiki_writer.py \
  backend/orchestration/wiki/maintenance.py \
  backend/orchestration/wiki/writer.py \
  backend/api/wiki.py
```

### Level 2: Unit + Integration Tests

Per `CLAUDE.md` "How to run tests": full-suite invocations MUST run in background.

```bash
# Selection — fast subset for the iteration loop:
python -m pytest backend/tests/ -k "wiki" -x

# Full suite — run in background (Bash run_in_background=true), poll output:
python -m pytest backend/tests/ -x
```

### Level 3: YAML pipeline validation

```bash
python -c "
from pathlib import Path
from backend.orchestration.yaml_loader import load
for p in ['period_close', 'vat_return', 'year_end_close']:
    pipeline = load(Path(f'backend/orchestration/pipelines/{p}.yaml'))
    assert any(n.id == 'read-wiki' for n in pipeline.nodes), f'{p} missing read-wiki'
    assert any(n.id == 'draft-post-mortem' for n in pipeline.nodes), f'{p} missing draft-post-mortem'
    assert any(n.id == 'write-post-mortem' for n in pipeline.nodes), f'{p} missing write-post-mortem'
print('all three reporting pipelines have wiki nodes')
"
```

### Level 4: Manual Validation

```bash
# 1. Start the backend
uvicorn backend.api.main:app --reload --workers 1

# 2. Create a policy via the new API
curl -X POST http://127.0.0.1:8000/wiki/pages \
  -H 'content-type: application/json' \
  -H 'x-fingent-author: marie' \
  -d '{"path":"policies/expense-thresholds.md","title":"Expense thresholds","body_md":"## Manager approval > 500 EUR","frontmatter":{"applies_to":["anomaly_detection","period_close"],"jurisdictions":["FR"],"revision":1}}'

# 3. Trigger a period close (replace tenant defaults as needed)
curl -X POST http://127.0.0.1:8000/pipelines/run \
  -H 'content-type: application/json' \
  -d '{"pipeline":"period_close","trigger_payload":{"period_id":"2026-Q1","wiki_tags":["period_close","anomaly_detection"]}}'

# 4. Inspect the resulting post_mortem page
curl http://127.0.0.1:8000/wiki/pages | jq '.items[] | select(.path | startswith("post_mortems/"))'

# 5. Re-run the same pipeline; observe the SSE stream's `agent.decision` events
#    carry citations to BOTH the policy page and the prior post-mortem.
curl -N http://127.0.0.1:8000/runs/<new_run_id>/stream
```

### Level 5: Audit-trail correctness

```bash
sqlite3 data/audit.db "SELECT node_id, wiki_page_id, wiki_revision_id, prompt_hash FROM agent_decisions WHERE run_id_logical = '<run_id>' ORDER BY id"
# Expectation: every reasoning agent (counterparty_*, gl_*, anomaly_*, document_*) row has non-NULL wiki_page_id when matching pages exist.
```

---

## ACCEPTANCE CRITERIA

- [ ] `prompt_hash` and `result.wiki_references` are populated by the runner, not by post-hoc agent code.
- [ ] All four reasoning agents (`gl_account_classifier`, `counterparty_classifier`, `anomaly_flag`, `document_extractor`) read the wiki and stamp citations.
- [ ] `tools.wiki_search:run` returns BM25-ranked hits over the latest revision per page.
- [ ] After a `period_close` run, a new page exists under `post_mortems/{period}/period_close_{run_id}.md` whose frontmatter `applies_to` includes `period_close` and `post_mortem`.
- [ ] A second `period_close` run on the same period reads the prior post-mortem in its `read-wiki` step (verified by the `agent.decision` SSE event citing it).
- [ ] `PUT /wiki/pages/{id}` and `POST /wiki/pages` work end-to-end with frontmatter validation.
- [ ] `log.md` and `index.md` exist as wiki pages and update automatically on every wiki edit.
- [ ] All existing tests still green (50+ tests; no regressions).
- [ ] At least 7 new tests added (one per major capability) and all green.
- [ ] No new dependencies in `pyproject.toml`.
- [ ] CLAUDE.md `--workers 1`, integer-cents, `gl_poster.post`-chokepoint, `write_tx`-only invariants all preserved.

---

## COMPLETION CHECKLIST

- [ ] All 23 step-by-step tasks completed in order.
- [ ] Each task validation command returns success.
- [ ] Full pytest suite passes (background invocation per `CLAUDE.md`).
- [ ] Manual validation flow (Level 4) demonstrates self-improvement: second run of the same pipeline cites the first run's post-mortem.
- [ ] `Orchestration/PRDs/PRD-AutonomousCFO.md` §15 D items #1, #3, #9 marked resolved.
- [ ] Update `CLAUDE.md` "Repository layout" to mention `tools/wiki_search.py`, `tools/wiki_writer.py`, `agents/wiki_post_mortem_agent.py`, `wiki/maintenance.py`, `migrations/orchestration/0004_wiki_fts.py`.
- [ ] Update `README.md` "What works today" to describe the self-improvement loop.

---

## NOTES

**Design decision — agents stay non-cacheable.** Cache-key correctness for an agent that does in-flight wiki fetch is hard: the wiki state isn't known until *after* the agent runs, so the cache lookup at `executor.py:201` cannot incorporate it. The clean way out: agents are non-cacheable (already the convention; verify `dag.PipelineNode.cacheable` defaults). Wiki state therefore enters cache only via the `read-wiki` *tool* node's output, which is part of every downstream agent's deps and naturally flows into the dep-tool's cache key. This preserves correctness without coupling the runner to the cache.

**Design decision — auto-append observations vs. CFO-gated rule edits.** Filing a new `post_mortems/...` page is auto. Modifying an existing `policies/...` page is *not* — the agent must set `requires_human_ratification=true`, which routes through `review_queue` so a human commits via `PUT /wiki/pages/{id}`. This bounds the unsafe failure mode (the wiki rewriting its own constitution) without giving up on self-improvement.

**Design decision — FTS5 vs `qmd`.** Both `Orchestration/research/llm-wiki.md` and PRD §13 mention `qmd`. We choose FTS5 because (a) zero new infra, (b) sufficient at MVP corpus scale (~hundreds of pages per tenant), (c) reversible — replacing FTS5 with `qmd` later is a one-tool swap. `qmd` becomes interesting once the wiki crosses 200+ pages and we want vector hybrid search (PRD §13 future consideration).

**Re-discovery invariant (the user's explicit ask).** Every wiki read path uses `load_pages_for_tags` or the FTS5 search, both of which scan `wiki_pages` × `wiki_revisions` live. There is no in-memory cache of "the wiki layout". This means a `wiki_post_mortem` agent that filed a page during `read-wiki` (theoretical — same-run) would be visible to a sibling node, and a CFO who hand-creates a markdown file via `POST /wiki/pages` is visible to the *very next* pipeline run. The corpus is allowed to grow between runs, and the system reflects it without any rebuild step. The `index.md` rebuild is a navigability nicety for humans, not a load-bearing data structure.

**Self-improvement reading order.** The post-mortem agent reads existing post-mortems for the same pipeline before drafting. So run #1 has no prior context; run #2 has run #1's lessons; by run #5 the wiki is dense with grounded observations. This compounds in the way `Orchestration/research/llm-wiki.md` §"Why this works" describes — the LLM does the bookkeeping that humans abandon.

**What this plan deliberately does NOT do.**

- Does not introduce a new database. All wiki state stays in `orchestration.db` (per RealMetaPRD §9 three-DB invariant).
- Does not change the `agent.decision` SSE event shape (frontend stays untouched).
- Does not add `qmd` or any embedding store (FTS5 is sufficient).
- Does not refactor `gl_poster.post` or any money-path code.
- Does not implement the `wiki-lint` nightly job (PRD §7.3 future consideration).
- Does not add the onboarding `onboarding_to_wiki` pipeline (PRD §7.1 — separate plan).
- Does not wire the frontend Wiki editor's commit button to the new PUT endpoint (frontend follow-up; the backend ships first).

**Confidence score for one-pass success: 8/10.** The pattern is established (`gl_account_classifier_agent` is the working reference); the contracts (`prompt_hash`, `cache_key`, `propose_checkpoint_commit`) all already accept `wiki_context`; the only genuinely new code is the post-mortem agent, the search tool, the maintenance helpers, and the write API. The two risk vectors: (1) the `wiki_post_mortem` agent's prompt may need iteration to produce useful drafts (manageable — the test seeds a controlled fixture), (2) FTS5 trigger semantics for contentless tables are easy to get subtly wrong (cited in the SQLite docs reference above; verify with the unit test before wiring downstream).
