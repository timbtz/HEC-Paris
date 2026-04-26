"""Seed one realistic post-mortem revision + one rule-change ratification request.

The wiki "self-improvement" story in the pitch needs at least one tape:
- a non-seed wiki revision authored by `agent:wiki_post_mortem` showing the
  agent observed something and filed it,
- a `review_queue` row of `kind='wiki_rule_change'` showing the CFO has a
  one-click ratify pending.

Demo-only — runs idempotently (skips if the same path already exists).

Usage:
    uv run python -m backend.scripts.seed_demo_post_mortem
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from ..orchestration.store.bootstrap import open_dbs
from ..orchestration.wiki import upsert_page
from ..orchestration.wiki.schema import WikiFrontmatter

logger = logging.getLogger(__name__)


_POST_MORTEM_PATH = "post_mortems/2026-Q1/period_close_demo.md"
_POST_MORTEM_TITLE = "period_close — 2026-Q1 — €312 dinner slipped €250 attendees-list rule"
_POST_MORTEM_BODY = """\
# period_close — 2026-Q1 — €312 dinner slipped €250 attendees-list rule

**Run:** period_close run #212 (2026-04-26)
**Period:** 2026-Q1
**Author:** `agent:wiki_post_mortem` (auto-filed observation, no human ratification needed for this page)

## What was unclean

One restaurant invoice (`invoice_2026_03_19_le_jules_verne.pdf`, €312.40) cleared
the auto-post path despite being above the €250 attendees-list threshold defined
in `policies/fr-bewirtung.md`. The classifier's confidence was 0.92 — well
above the 0.85 floor in `policies/expense-thresholds.md` — so the gate did not
route to review.

## Assumptions made

The line was booked to `625710 Réceptions` with TVA at 10%. The
`document_extractor` extracted only the total, not an attendees list, because
the receipt PDF doesn't have one printed (the host wrote names by hand).

## Where review was needed

The €250 threshold rule in `policies/fr-bewirtung.md` only triggers a review
flag when the *extractor* sets `attendees_missing=true`. The extractor instead
returned `attendees=null` because the field was absent rather than explicitly
empty. Null silently passed the rule.

## Rule changes proposed

Tighten `policies/fr-bewirtung.md`: when amount ≥ €250 AND attendees field is
either `null` or `[]`, route to review. Currently only `[]` triggers. Filed as
a separate ratification request (kind=wiki_rule_change) so the CFO can sign
off on the rule edit before agents start using it.

## Anomalies flagged

| Type | Count | Severity |
|---|---|---|
| restaurant_attendees_missing | 1 | medium |
| confidence_above_threshold_but_unclean | 1 | medium |

## Confidence

Overall: 0.78 (down from 0.91 last quarter; the missing-attendees pattern is
the only reason — every other signal was clean).
"""

_POST_MORTEM_FRONTMATTER = WikiFrontmatter(
    applies_to=["period_close", "post_mortem", "2026-Q1"],
    jurisdictions=["FR"],
    revision=1,
)


_RULE_CHANGE_REASON = (
    "wiki rule change proposed by post_mortem agent (run 212); "
    "target=policies/fr-bewirtung.md; "
    "title='FR — Business meals: tighten null-attendees handling above €250'; "
    "draft_len=614"
)


async def main(data_dir: Path) -> dict[str, int]:
    store = await open_dbs(data_dir, run_migrations=False)
    written = {"wiki_revision": 0, "review_queue": 0, "skipped_existing": 0}
    try:
        cur = await store.orchestration.execute(
            "SELECT id FROM wiki_pages WHERE path = ?", (_POST_MORTEM_PATH,),
        )
        existing = await cur.fetchone()
        await cur.close()
        if existing is not None:
            written["skipped_existing"] = 1
            logger.info("seed.post_mortem.exists path=%s page_id=%d",
                        _POST_MORTEM_PATH, int(existing[0]))
        else:
            page_id, revision_id = await upsert_page(
                store.orchestration,
                store.orchestration_lock,
                path=_POST_MORTEM_PATH,
                title=_POST_MORTEM_TITLE,
                frontmatter=_POST_MORTEM_FRONTMATTER,
                body_md=_POST_MORTEM_BODY,
                author="agent:wiki_post_mortem",
            )
            written["wiki_revision"] = 1
            logger.info("seed.post_mortem.written page_id=%d revision_id=%d",
                        page_id, revision_id)

        cur = await store.accounting.execute(
            "SELECT id, resolved_at FROM review_queue "
            "WHERE kind = 'wiki_rule_change' AND reason = ?",
            (_RULE_CHANGE_REASON,),
        )
        existing_rq = await cur.fetchone()
        await cur.close()
        from ..orchestration.store.writes import write_tx
        if existing_rq is None:
            async with write_tx(store.accounting, store.accounting_lock) as conn:
                cur = await conn.execute(
                    "INSERT INTO review_queue (entry_id, kind, confidence, reason) "
                    "VALUES (?, ?, ?, ?)",
                    (None, "wiki_rule_change", None, _RULE_CHANGE_REASON),
                )
                await cur.close()
            written["review_queue"] = 1
            logger.info("seed.review_queue.written")
        elif existing_rq[1] is not None:
            # The demo seed already exists but was ratified in a prior run —
            # reset it to pending so the demo always has a fresh ratification
            # ready to show.
            async with write_tx(store.accounting, store.accounting_lock) as conn:
                await conn.execute(
                    "UPDATE review_queue "
                    "SET resolved_at = NULL, resolved_by = NULL "
                    "WHERE id = ?",
                    (int(existing_rq[0]),),
                )
            written["review_queue"] = 1
            logger.info("seed.review_queue.reset_to_pending id=%d", int(existing_rq[0]))
    finally:
        await store.close()
    return written


if __name__ == "__main__":
    import os
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    data_dir = Path(os.environ.get("AGNES_DATA_DIR", "./data")).resolve()
    summary = asyncio.run(main(data_dir))
    logger.info("seed_demo_post_mortem.complete %s", summary)
