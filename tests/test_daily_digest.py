import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from daily_digest import (clean_markup, deduplicate, google_news_url,
                          normalized_title, parse_rss, render_report, report_path)
from send_email import build_message, html_report


class NewsDigestTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 21, 0, 17, tzinfo=timezone.utc)

    def rss(self, pub_date, title="Example news", link="https://example.org/a"):
        return (
            "<rss><channel><item>"
            f"<title>{title}</title><link>{link}</link>"
            f"<pubDate>{pub_date}</pubDate><source>Test Agency</source>"
            "<description>Verified feed description.</description>"
            "</item></channel></rss>"
        ).encode("utf-8")

    def test_url_query_and_locales(self):
        url = google_news_url("人工智能 AI", "zh")
        params = parse_qs(urlparse(url).query)
        self.assertIn("when:1d", params["q"][0])
        self.assertEqual(params["ceid"][0], "TW:zh-Hant")

    def test_parse_only_valid_recent_items(self):
        recent = self.now - timedelta(hours=3)
        rows = parse_rss(self.rss(format_datetime(recent)), "ai", "fallback", self.now, 24, 25)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["topics"], ["ai"])
        self.assertEqual(rows[0]["source"], "Test Agency")
        older = self.now - timedelta(days=3)
        self.assertEqual(parse_rss(self.rss(format_datetime(older)), "ai", "fallback", self.now, 24, 25), [])

    def test_dedupe_cross_sector_and_direct_link_preferred(self):
        base = {"id": "one", "title": "Semiconductor prices rise", "url": "https://news.google.com/a",
                "source": "Google News", "published_utc": self.now.isoformat(), "excerpt": "", "topics": ["ai"]}
        direct = {**base, "id": "two", "url": "https://example.com/direct", "topics": ["finance"]}
        merged = deduplicate([base, direct])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["url"], "https://example.com/direct")
        self.assertEqual(merged[0]["topics"], ["ai", "finance"])

    def test_report_has_attributions_and_failures(self):
        cfg = {"window_hours": 24, "report_items_per_topic": 4, "topics": [
            {"id": "ai", "label": "人工智能", "kind": "industry"},
            {"id": "europe", "label": "欧洲", "kind": "region"},
        ], "direct_feeds": []}
        row = {"id": "1", "title": "A real headline", "url": "https://example.org/story",
               "source": "Example Publisher", "published_utc": self.now.isoformat(),
               "excerpt": "Some excerpt.", "topics": ["ai", "europe"]}
        output = render_report("2026-10-21", self.now, cfg, [row],
                               [{"feed": "X", "error": "timed out"}], 1, "", "LLM not enabled")
        self.assertIn("https://example.org/story", output)
        self.assertIn("Example Publisher", output)
        self.assertIn("timed out", output)

    def test_report_path_and_mime_message(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = report_path("2026-10-21", Path(temp))
            folder.mkdir(parents=True)
            (folder / "report.md").write_text("# Daily Report\n\n- [Source](https://example.org)", encoding="utf-8")
            (folder / "sources.csv").write_text("source,url\nExample,https://example.org\n", encoding="utf-8")
            msg = build_message("2026-10-21", folder, "sender@example.org", ["reader@example.org"])
            self.assertIn("2026-10-21", msg["Subject"])
            self.assertEqual(len(list(msg.iter_attachments())), 2)
            self.assertIn("href=", html_report("# Daily\n- [Link](https://example.org)"))


if __name__ == "__main__":
    unittest.main()
