"""Offline tests — no network, no API key required.

Run with: python -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from news_agent import emailer, relevance, sources, summarize  # noqa: E402
from news_agent.config import Config  # noqa: E402
from news_agent.models import Article, Assessment, Digest, DigestSection  # noqa: E402

FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <item>
    <title>Lake Mead water level rises as snowpack runoff arrives - Water Weekly</title>
    <link>https://news.google.com/rss/articles/ABC123</link>
    <pubDate>Sat, 25 Jul 2026 12:00:00 GMT</pubDate>
    <description>&lt;a href="x"&gt;Reservoir storage climbed on stronger than forecast runoff.&lt;/a&gt;</description>
    <source url="https://waterweekly.example">Water Weekly</source>
  </item>
  <item>
    <title>New casino opens near Lake Mead - Vegas Times</title>
    <link>https://news.google.com/rss/articles/DEF456</link>
    <pubDate>Sat, 25 Jul 2026 09:30:00 GMT</pubDate>
    <description>The resort and casino will open to guests this fall.</description>
    <source url="https://vegastimes.example">Vegas Times</source>
  </item>
</channel></rss>"""


def make_article(title: str, snippet: str = "", url: str = "https://example.com/a") -> Article:
    return Article(
        title=title,
        url=url,
        source="Example News",
        published=datetime(2026, 7, 25, tzinfo=timezone.utc),
        snippet=snippet,
        keyword="Lake Mead",
    )


class TestSearchQuery(unittest.TestCase):
    def test_query_is_exact_phrase_and_time_boxed(self):
        url = sources.build_query_url("Colorado River", window="1d")
        self.assertIn("%22Colorado+River%22", url)
        self.assertIn("when%3A1d", url)


class TestFeedParsing(unittest.TestCase):
    def test_parses_items(self):
        articles = sources.parse_feed(FEED, "Lake Mead")
        self.assertEqual(len(articles), 2)
        first = articles[0]
        self.assertEqual(first.title, "Lake Mead water level rises as snowpack runoff arrives")
        self.assertEqual(first.source, "Water Weekly")
        self.assertEqual(first.keyword, "Lake Mead")
        self.assertIn("Reservoir storage climbed", first.snippet)
        self.assertNotIn("<a href", first.snippet)
        self.assertEqual(first.published.year, 2026)

    def test_dedupe_drops_repeats(self):
        articles = sources.parse_feed(FEED, "Lake Mead")
        deduped = sources.dedupe(articles + articles)
        self.assertEqual(len(deduped), 2)

    def test_bad_xml_returns_empty(self):
        self.assertEqual(sources.parse_feed(b"not xml", "Lake Mead"), [])


class TestHeuristicRelevance(unittest.TestCase):
    def test_hydrology_article_is_kept(self):
        article = make_article(
            "Lake Mead elevation climbs on Colorado River runoff",
            "Reservoir storage rose after above-average snowpack melted into the basin.",
        )
        assessment = relevance.heuristic_assess(article)
        self.assertTrue(assessment.is_hydrology)
        self.assertGreater(assessment.confidence, 0.5)

    def test_unrelated_article_is_dropped(self):
        article = make_article(
            "New casino opens near Lake Mead",
            "The resort restaurant will seat 400 guests.",
        )
        self.assertFalse(relevance.heuristic_assess(article).is_hydrology)

    def test_select_ranks_and_limits(self):
        assessments = [
            Assessment(make_article(f"a{i}", url=f"https://example.com/{i}"), True, conf)
            for i, conf in enumerate([0.3, 0.9, 0.7, 0.95, 0.6])
        ]
        assessments.append(Assessment(make_article("nope", url="https://example.com/x"), False, 0.9))
        selected = relevance.select(assessments, limit=3, min_confidence=0.5)
        self.assertEqual([a.confidence for a in selected], [0.95, 0.9, 0.7])

    def test_select_backfills_when_few_are_confident(self):
        assessments = [
            Assessment(make_article(f"a{i}", url=f"https://example.com/{i}"), True, conf)
            for i, conf in enumerate([0.9, 0.2, 0.1])
        ]
        selected = relevance.select(assessments, limit=3, min_confidence=0.5)
        self.assertEqual(len(selected), 3)
        self.assertEqual(selected[0].confidence, 0.9)

    def test_select_never_returns_non_hydrology(self):
        assessments = [Assessment(make_article("nope"), False, 0.99)]
        self.assertEqual(relevance.select(assessments, limit=5), [])


class TestTermMatching(unittest.TestCase):
    def test_negative_terms_need_word_boundaries(self):
        # "nfl" must not match inside "inflow", nor "dam" inside "damage".
        article = make_article(
            "Reclamation reports lower inflow",
            "Inflow to the reservoir fell; no damage was reported at the dam.",
        )
        assessment = relevance.heuristic_assess(article)
        self.assertTrue(assessment.is_hydrology)
        self.assertNotIn("penalized", assessment.reason)

    def test_real_negative_term_still_penalizes(self):
        article = make_article("Lake Mead casino expansion", "The casino adds a restaurant.")
        self.assertIn("penalized", relevance.heuristic_assess(article).reason)


class TestJsonSource(unittest.TestCase):
    def test_loads_and_normalizes_entries(self):
        import json
        import tempfile

        payload = [
            {
                "title": "Lake Mead hits record low",
                "url": "https://example.com/1",
                "source": "Example",
                "published": "2026-07-22T12:00:00Z",
                "snippet": "Reservoir storage fell.",
                "keyword": "Lake Mead",
            },
            {"title": "no url here"},  # skipped
        ]
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            json.dump(payload, handle)
            path = handle.name

        articles = sources.load_json_file(path)
        self.assertEqual(len(articles), 1)
        self.assertEqual(articles[0].keyword, "Lake Mead")
        self.assertEqual(articles[0].published.tzinfo, timezone.utc)


class TestSummaryFallback(unittest.TestCase):
    def test_heuristic_summary_mentions_counts_and_llm_status(self):
        section = DigestSection(
            keyword="Lake Mead",
            assessments=[Assessment(make_article("Lake Mead rises"), True, 0.8, topic="reservoir storage")],
        )
        overall = summarize.heuristic_summarize([section])
        self.assertIn("Lake Mead", section.summary)
        self.assertIn("reservoir storage", section.summary)
        self.assertIn("keyword scoring", overall)

    def test_empty_digest_short_circuits(self):
        overall, used = summarize.summarize([DigestSection(keyword="Lake Mead")], model="x", use_llm=True)
        self.assertFalse(used)
        self.assertIn("No hydrology-related articles", overall)


class TestEmail(unittest.TestCase):
    def make_digest(self) -> Digest:
        article = make_article("Lake Mead rises", url="https://example.com/story")
        section = DigestSection(
            keyword="Lake Mead",
            assessments=[Assessment(article, True, 0.8, topic="reservoir storage")],
            summary="Storage rose this week.",
            takeaways={article.url: "Elevation up two feet on late runoff."},
        )
        return Digest(
            generated_at=datetime(2026, 7, 25, tzinfo=timezone.utc),
            sections=[section],
            overall="A wetter-than-expected week in the basin.",
            llm_used=True,
        )

    def test_text_body_contains_links_and_summaries(self):
        body = emailer.build_text(self.make_digest())
        self.assertIn("https://example.com/story", body)
        self.assertIn("Storage rose this week.", body)
        self.assertIn("Elevation up two feet", body)

    def test_html_body_escapes_and_links(self):
        digest = self.make_digest()
        digest.sections[0].assessments[0].article.title = 'Storm & <flood> watch'
        html_body = emailer.build_html(digest)
        self.assertIn("&amp;", html_body)
        self.assertNotIn("<flood>", html_body)
        self.assertIn('href="https://example.com/story"', html_body)

    def test_message_has_both_parts(self):
        config = Config(email_from="a@example.com", email_to=["b@example.com"])
        message = emailer.build_message(self.make_digest(), config)
        self.assertEqual(message["To"], "b@example.com")
        self.assertIn("Lake Mead", message["Subject"])
        self.assertEqual(
            {part.get_content_subtype() for part in message.iter_parts()}, {"plain", "html"}
        )

    def test_send_refuses_without_settings(self):
        with self.assertRaises(RuntimeError) as ctx:
            emailer.send(self.make_digest(), Config())
        self.assertIn("SMTP_HOST", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
