"""One-shot seeding script for the Living Rule Wiki.

Usage:
    uv run python -m backend.scripts.seed_wiki
    uv run python -m backend.scripts.seed_wiki --wiki-dir /custom/wiki

Scans `wiki/**/*.md` from the repo root (override with `--wiki-dir`),
parses each file's YAML frontmatter, and calls
`orchestration.wiki.writer.upsert_page` once per file.

Why explicit (not autoload on boot): each call writes a new revision row;
autoloading on every server start would balloon `wiki_revisions` with
no-op revisions. Run this once per fresh DB or after a CI git pull.

PRD-AutonomousCFO §12 Phase 4.A deliverable.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
from pathlib import Path

from ..orchestration.store.bootstrap import open_dbs
from ..orchestration.wiki.schema import parse_frontmatter
from ..orchestration.wiki.writer import upsert_page

logger = logging.getLogger(__name__)


def _default_wiki_dir() -> Path:
    """Repo-root `wiki/` directory.

    `backend/scripts/seed_wiki.py` → repo root is two parents up.
    """
    return Path(__file__).resolve().parent.parent.parent / "wiki"


def _default_data_dir() -> Path:
    """Match the canonical Agnes data dir.

    Honors `AGNES_DATA_DIR` per `.env.example` so the seeder writes into
    the same orchestration.db the API server opens.
    """
    return Path(os.environ.get("AGNES_DATA_DIR", "./data")).resolve()


def _title_from_body(body_md: str, fallback: str) -> str:
    """First Markdown heading wins; otherwise the path-derived fallback."""
    for line in body_md.splitlines():
        s = line.strip()
        if s.startswith("# "):
            return s[2:].strip() or fallback
    return fallback


async def seed(
    *,
    wiki_dir: Path,
    data_dir: Path,
    author: str = "seed_wiki.py",
) -> list[tuple[str, int, int]]:
    """Walk `wiki_dir` and upsert every `*.md` file. Returns a list of
    `(path_in_wiki, page_id, revision_id)` tuples for the caller to log.
    """
    if not wiki_dir.is_dir():
        raise FileNotFoundError(f"wiki dir not found: {wiki_dir}")

    handles = await open_dbs(data_dir)
    out: list[tuple[str, int, int]] = []
    try:
        files = sorted(p for p in wiki_dir.rglob("*.md") if p.is_file())
        for md_path in files:
            rel = md_path.relative_to(wiki_dir).as_posix()
            text = md_path.read_text(encoding="utf-8")
            frontmatter, body_md = parse_frontmatter(text)
            title = _title_from_body(body_md, fallback=rel)
            page_id, revision_id = await upsert_page(
                handles.orchestration,
                handles.orchestration_lock,
                path=rel,
                title=title,
                frontmatter=frontmatter,
                body_md=body_md,
                author=author,
            )
            out.append((rel, page_id, revision_id))
            logger.info(
                "wiki.seeded path=%s page_id=%d rev_id=%d", rel, page_id, revision_id
            )
    finally:
        await handles.close()
    return out


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Seed the Living Rule Wiki.")
    p.add_argument(
        "--wiki-dir",
        type=Path,
        default=_default_wiki_dir(),
        help="Directory holding `*.md` wiki pages (default: repo-root wiki/).",
    )
    p.add_argument(
        "--data-dir",
        type=Path,
        default=_default_data_dir(),
        help="Agnes data dir (default: AGNES_DATA_DIR or ./data).",
    )
    p.add_argument(
        "--author",
        type=str,
        default="seed_wiki.py",
        help="Author label written to the wiki_revisions row.",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = _parse_args(argv)
    seeded = asyncio.run(seed(
        wiki_dir=args.wiki_dir,
        data_dir=args.data_dir,
        author=args.author,
    ))
    print(f"Seeded {len(seeded)} wiki page(s) into {args.data_dir}/orchestration.db")
    for path, page_id, rev_id in seeded:
        print(f"  {path}  page_id={page_id} rev_id={rev_id}")


if __name__ == "__main__":
    main()
