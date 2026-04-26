"""Living Rule Wiki — markdown corpus read by reasoning agents as prompt input.

Source: PRD-AutonomousCFO §7.3 + Karpathy LLM-Wiki pattern
(`Orchestration/research/llm-wiki.md`).

Public surface:
- `schema.WikiFrontmatter`, `schema.parse_frontmatter`
- `loader.load_pages_for_tags`, `loader.WikiPage`
- `writer.upsert_page`
"""
from .schema import WikiFrontmatter, parse_frontmatter
from .loader import WikiPage, load_pages_for_tags
from .writer import upsert_page

__all__ = [
    "WikiFrontmatter",
    "parse_frontmatter",
    "WikiPage",
    "load_pages_for_tags",
    "upsert_page",
]
