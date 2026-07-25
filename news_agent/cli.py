"""Command-line entry point: `python -m news_agent`."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import agent, emailer
from .config import DEFAULT_KEYWORDS, Config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="news_agent",
        description=(
            "Search today's news for the given keywords, keep the hydrology-related "
            "articles, summarize them, and draft (or send) an email digest."
        ),
    )
    parser.add_argument(
        "-k", "--keyword", dest="keywords", action="append",
        help=f"Search keyword; repeatable. Default: {' and '.join(DEFAULT_KEYWORDS)}.",
    )
    parser.add_argument("-n", "--per-keyword", type=int, default=5,
                        help="Articles to select per keyword (default: 5).")
    parser.add_argument("--window", default="1d",
                        help="How far back to search, e.g. 1d, 2d, 7d (default: 1d).")
    parser.add_argument("--max-candidates", type=int, default=30,
                        help="Max search results to assess per keyword (default: 30).")
    parser.add_argument("--no-widen", action="store_true",
                        help="Don't widen the time window when a keyword yields fewer than "
                             "--per-keyword hydrology articles. Strictly honours --window.")
    parser.add_argument("--articles-file", default=None, metavar="PATH",
                        help="Read candidate articles from a JSON file instead of searching "
                             "the web. Use this to plug in another search backend, or where "
                             "outbound RSS is blocked.")
    parser.add_argument("--model", default=None, help="Claude model id (default: from CLAUDE_MODEL env).")
    parser.add_argument("--no-llm", action="store_true",
                        help="Skip Claude entirely; use keyword scoring and template summaries.")
    parser.add_argument("--no-resolve-links", action="store_true",
                        help="Keep news.google.com redirect links instead of resolving publishers.")
    parser.add_argument("-o", "--out", default="digest.txt",
                        help="Where to write the drafted email (default: digest.txt). "
                             "An .html sibling is written too.")
    parser.add_argument("--send", action="store_true",
                        help="Actually send the email over SMTP. Without this the digest is only drafted.")
    parser.add_argument("-q", "--quiet", action="store_true", help="Only log warnings and errors.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    config = Config.from_env()
    config.keywords = args.keywords or list(DEFAULT_KEYWORDS)
    config.per_keyword = args.per_keyword
    config.window = args.window
    config.max_candidates = args.max_candidates
    config.use_llm = not args.no_llm
    config.resolve_links = not args.no_resolve_links
    if args.no_widen:
        config.fallback_windows = []
    config.send = args.send
    config.articles_file = args.articles_file
    if args.articles_file and not args.keywords:
        # Let the file decide which keywords to digest, preserving its order.
        from . import sources

        found = list(dict.fromkeys(a.keyword for a in sources.load_json_file(args.articles_file) if a.keyword))
        if found:
            config.keywords = found
    if args.model:
        config.model = args.model

    # Fail before doing any work if sending was requested but isn't configured.
    if config.send:
        missing = config.missing_email_settings()
        if missing:
            print(
                f"error: --send requires these environment variables: {', '.join(missing)}\n"
                "See .env.example for the full list.",
                file=sys.stderr,
            )
            return 2

    digest = agent.run(config)

    text = emailer.build_text(digest)
    out_path = Path(args.out)
    out_path.write_text(text, encoding="utf-8")
    out_path.with_suffix(".html").write_text(emailer.build_html(digest), encoding="utf-8")

    print(text)
    print(
        f"\n[drafted {digest.article_count} articles -> {out_path} and "
        f"{out_path.with_suffix('.html')}]",
        file=sys.stderr,
    )

    if digest.article_count == 0:
        print("warning: no hydrology-related articles were found.", file=sys.stderr)

    if config.send:
        try:
            emailer.send(digest, config)
        except Exception as exc:  # noqa: BLE001 - surface any SMTP failure to the user
            print(f"error: failed to send email: {exc}", file=sys.stderr)
            return 1
        print(f"[sent to {', '.join(config.email_to)}]", file=sys.stderr)

    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
