"""CLI entrypoint: `python -m backend.mcp [--http [host:port]]`.

Defaults to stdio (for Claude Desktop / Claude Code MCP integrations).
Pass `--http` (alone) to bind streamable-http to 127.0.0.1:8765, or
`--http <host>:<port>` for a custom bind.
"""
from __future__ import annotations

import argparse
import sys

from .server import mcp


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fingent-mcp", description=__doc__)
    parser.add_argument(
        "--http",
        nargs="?",
        const="127.0.0.1:8765",
        default=None,
        metavar="HOST:PORT",
        help="Serve over streamable-http instead of stdio (default bind 127.0.0.1:8765).",
    )
    args = parser.parse_args(argv)

    if args.http is None:
        mcp.run(transport="stdio")
        return 0

    host, _, port = args.http.partition(":")
    mcp.run(
        transport="streamable-http",
        host=host or "127.0.0.1",
        port=int(port) if port else 8765,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
