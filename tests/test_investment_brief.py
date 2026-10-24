import io
import json
import os
import sys
import unittest
from unittest.mock import patch
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from investment_brief import (
    ArticleParser, extract_html_evidence, is_safe_public_url,
    select_key_articles, investment_brief, _call_llm
)


def article(i, topic="ai", source="BBC Technology", direct=True):
    return {
        "id": ("%012x" % (i+1)),
        "title": f"Industry demand and investment growth #{i}",
        "url": f"https://bbc.com/news/{i}" if direct else f"https://news.google.com/rss/articles/{i}",
        "source": source,
        "published_utc": datetime(2026, 10, 24, tzinfo=timezone.utc).isoformat(),
        "excerpt": "This is a brief event description, based on public reporting, with enough detail to qualify as RSS evidence text.",
        "topics": [topic],
    }


class InvestmentResearchTests(unittest.TestCase):
    def test_article_parser_extracts_publisher_body(self):
        raw = ("""<html><head><meta name='description' content='Summary'/></head>
        <body><nav>""" + "menu " * 100 + """</nav><article>"""
        + "<p>" + "Financial statement says orders grew, but debt also increased. " * 12 + "</p>"
        + "<script>ignore secret navigation</script></article></body></html>").encode()
        extracted = extract_html_evidence(raw)
        self.assertIn("orders grew", extracted)
        self.assertNotIn("menu", extracted)
        self.assertNotIn("ignore secret", extracted)

    def test_json_ld_body(self):
        content = ("<script type='application/ld+json'>"
                   + json.dumps({"@type": "NewsArticle", "articleBody": "Full factual article text. " * 50})
                   + "</script>").encode()
        self.assertIn("Full factual", extract_html_evidence(content))

    def test_guard_url(self):
        for url in ("http://example.com/", "https://localhost/a", "https://127.0.0.1/a",
                    "https://news.google.com/rss/articles/1", "file:///etc/passwd",
                    "https://user:password@example.com", "https://10.0.0.3/private"):
            self.assertFalse(is_safe_public_url(url), url)
        self.assertTrue(is_safe_public_url("https://www.bbc.com/news/a"))

    def test_selection_diverse_and_bounded(self):
        articles = [article(i, "ai", "publisher-1") for i in range(20)]
        articles += [article(30, "finance", "publisher-2")]
        selected = select_key_articles(articles, limit=26)
        self.assertLessEqual(len(selected), 3)
        self.assertIn("finance", [s["topics"][0] for s in selected])

    def test_honest_no_llm_fallback_and_no_network(self):
        values = [article(1, "ai", "BBC"), article(2, "finance", "BBC")]
        topics = [{"id": "ai", "label": "人工智能"}, {"id": "finance", "label": "金融"}]
        with patch("investment_brief.enrich_articles") as mock_enrich, patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            def decorate(rows):
                return [dict(row, evidence="This is a sample based on article text " * 20,
                             evidence_type="article_excerpt") for row in rows]
            mock_enrich.side_effect = decorate
            content, meta = investment_brief(values, topics, "2026-10-24")
        self.assertFalse(meta["llm_success"])
        self.assertIn("尚未完成模型深度归纳", content)
        self.assertIn("投资研究需检查", content)
        self.assertIn("已读取正文片段", content)

    def test_llm_rejects_wrong_reference_even_in_long_answer(self):
        evidence = [dict(article(i), evidence_type="article_excerpt", evidence="Evidence " * 80) for i in range(10)]
        mock_response = {"choices":[{"message":{"content":
            "### 核心观点\n### 十大值得读的事件\n### 跨行业影响链\n### 投资委员会的反方意见\n### 明日跟踪清单\n"
            + "投资分析：" * 350 + "\n[REF:ffffffffffff]"}}]}
        class Response:
            def __enter__(self):
                return io.BytesIO(json.dumps(mock_response).encode())
            def __exit__(self, a,b,c):
                pass
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}), patch("urllib.request.urlopen", return_value=Response()):
            result, message = _call_llm(evidence, {"ai":"AI"})
        self.assertFalse(result)
        self.assertIn("失败", message)


if __name__ == "__main__":
    unittest.main()
