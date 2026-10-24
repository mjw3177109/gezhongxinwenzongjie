#!/usr/bin/env python3
"""Global, source-linked daily digest. Python standard library only."""
from __future__ import annotations

import csv
import hashlib
import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from investment_brief import investment_brief

ROOT = Path(__file__).resolve().parents[1]
TIMEZONE_NAME = os.getenv("REPORT_TIMEZONE", "Asia/Shanghai")
EDITIONS = {
    "en": ("en-US", "US", "US:en"),
    "zh": ("zh-TW", "TW", "TW:zh-Hant"),
    "jp": ("ja", "JP", "JP:ja"),
    "kr": ("ko", "KR", "KR:ko"),
    "es": ("es-419", "MX", "MX:es-419"),
}
USER_AGENT = "GlobalDailyIntelligence/1.0 (compatible; RSSReader; +https://github.com/mjw3177109/gezhongxinwenzongjie)"


def now_local() -> datetime:
    return datetime.now(ZoneInfo(TIMEZONE_NAME))


def report_path(date_text: str, root: Path = ROOT) -> Path:
    day = datetime.strptime(date_text, "%Y-%m-%d").date()
    return root / "reports" / f"{day.year:04d}" / f"{day.month:02d}" / f"{day.day:02d}"


def google_news_url(query: str, edition: str = "en") -> str:
    hl, gl, ceid = EDITIONS.get(edition, EDITIONS["en"])
    params = urllib.parse.urlencode({"q": query + " when:1d", "hl": hl, "gl": gl, "ceid": ceid})
    return "https://news.google.com/rss/search?" + params


def clean_markup(raw: str) -> str:
    raw = re.sub(r"<[^>]*>", " ", raw or "")
    raw = html.unescape(raw)
    return re.sub(r"\s+", " ", raw).strip()


def normalized_title(title: str) -> str:
    return re.sub(r"[\W_]+", "", title.casefold(), flags=re.UNICODE)


def parse_rss(xml_data: bytes, topic_id: str, default_source: str,
              now: datetime, window_hours: int, limit: int) -> list[dict]:
    root = ET.fromstring(xml_data)
    items = root.findall(".//channel/item")
    threshold = now.astimezone(timezone.utc) - timedelta(hours=window_hours)
    upper_bound = now.astimezone(timezone.utc) + timedelta(minutes=10)
    news = []
    for item in items[:limit]:
        source = clean_markup(item.findtext("source") or default_source)
        title = clean_markup(item.findtext("title") or "")
        if source and title.endswith(" - " + source):
            title = title[:-(len(source) + 3)].strip()
        link = (item.findtext("link") or "").strip()
        desc = clean_markup(item.findtext("description") or "")[:320]
        published = (item.findtext("pubDate") or "").strip()
        if not title or not link or not link.startswith(("http://", "https://")) or not published:
            continue
        try:
            instant = parsedate_to_datetime(published)
            if instant.tzinfo is None:
                instant = instant.replace(tzinfo=timezone.utc)
            instant = instant.astimezone(timezone.utc)
        except (TypeError, ValueError, IndexError, OverflowError):
            continue
        if instant < threshold or instant > upper_bound:
            continue
        article_id = hashlib.sha256((title + "|" + link).encode("utf-8")).hexdigest()[:12]
        news.append({
            "id": article_id,
            "title": title,
            "url": link,
            "source": source or "来源未标注",
            "published_utc": instant.isoformat(),
            "excerpt": desc,
            "topics": [topic_id],
        })
    return news


def read_url(url: str, attempts: int = 2) -> bytes:
    last_error = None
    for attempt in range(attempts):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": USER_AGENT, "Accept": "application/rss+xml,application/xml,text/xml"}
            )
            with urllib.request.urlopen(req, timeout=18) as response:
                data = response.read(2_500_001)
                if len(data) > 2_500_000:
                    raise ValueError("RSS 大小超过限制")
                return data
        except Exception as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(1 + attempt)
    raise RuntimeError(f"{type(last_error).__name__}: {str(last_error)[:180]}")


def deduplicate(articles: list[dict]) -> list[dict]:
    """Merge identical headlines across topics; prefer a direct publisher URL."""
    exact = {}
    for article in articles:
        key = normalized_title(article["title"])
        if not key:
            continue
        old = exact.get(key)
        if old is None:
            exact[key] = article
            continue
        combined = sorted(set(old["topics"] + article["topics"]))
        old_is_google = urllib.parse.urlparse(old["url"]).hostname in ("news.google.com",)
        new_is_google = urllib.parse.urlparse(article["url"]).hostname in ("news.google.com",)
        if old_is_google and not new_is_google:
            article["topics"] = combined
            exact[key] = article
        else:
            old["topics"] = combined
    return sorted(exact.values(), key=lambda a: a["published_utc"], reverse=True)


def collect_news(config: dict, now: datetime) -> tuple[list[dict], list[dict], int]:
    jobs = []
    for topic in config["topics"]:
        jobs.append({
            "label": topic["label"],
            "topic_id": topic["id"],
            "url": google_news_url(topic["query"], topic.get("edition", "en")),
            "source": "Google News RSS",
        })
    for feed in config.get("direct_feeds", []):
        jobs.append({
            "label": feed["label"],
            "topic_id": feed["topic_id"],
            "url": feed["url"],
            "source": feed["label"],
        })
    errors, articles, succeeded = [], [], 0
    def work(job: dict):
        xml = read_url(job["url"])
        return parse_rss(xml, job["topic_id"], job["source"], now,
                         config.get("window_hours", 24), config.get("max_items_per_feed", 25))

    with ThreadPoolExecutor(max_workers=7) as executor:
        futures = {executor.submit(work, job): job for job in jobs}
        for future in as_completed(futures):
            job = futures[future]
            try:
                results = future.result()
                succeeded += 1
                articles.extend(results)
                print(f"[OK] {job['label']}: {len(results)}")
            except Exception as exc:
                errors.append({"feed": job["label"], "error": str(exc)[:250]})
                print(f"[WARN] {job['label']}: {exc}", file=sys.stderr)
    return deduplicate(articles), errors, succeeded


def safe_md(text: str) -> str:
    return (text or "").replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]").replace("|", "\\|").replace("\n", " ")


def render_report(date_text: str, now: datetime, config: dict, articles: list[dict],
                  errors: list[dict], succeeded: int, insights: str, ai_note: str) -> str:
    topics = config["topics"]
    names = {topic["id"]: topic["label"] for topic in topics}
    count = {topic["id"]: sum(topic["id"] in a["topics"] for a in articles) for topic in topics}
    total_feeds = len(topics) + len(config.get("direct_feeds", []))
    lines = [
        f"# 全球全行业情报日报 · {date_text}",
        "",
        f"> 截止：{now.strftime('%Y-%m-%d %H:%M')} 北京时间（Asia/Shanghai）；观察窗口：过去 {config.get('window_hours', 24)} 小时。",
        f"> 唯一报道：**{len(articles)}** 篇；成功采集来源入口：**{succeeded}/{total_feeds}**；失败：**{len(errors)}**。",
        "> 来源以原始发布方为准；Google News 链接可能跳转至聚合页面。报道仅依据公开 RSS 标题/摘要整理，不等于独立事实核查。",
        "",
        "## 一、今日总览",
        "",
    ]
    ranked = sorted([t for t in topics if t["kind"] == "industry"], key=lambda t: count[t["id"]], reverse=True)
    nonempty = [t for t in ranked if count[t["id"]] > 0]
    lines.append("**报道覆盖较多的主题（不代表事件更重要）：** " +
                 ("、".join(f"{t['label']}（{count[t['id']]}）" for t in nonempty[:8]) if nonempty else "暂无"))
    lines += ["", "### 核心新闻速览", ""]
    seen = set()
    top_items = []
    for topic in nonempty[:12]:
        for item in articles:
            if topic["id"] in item["topics"] and item["id"] not in seen:
                seen.add(item["id"])
                top_items.append((topic, item))
                break
    for topic, item in top_items[:12]:
        lines.append(f"- **{topic['label']}** · [{safe_md(item['title'])}]({item['url']})（{safe_md(item['source'])}）")
    if not top_items:
        lines.append("- 抓取窗口内暂无可归档报道。")
    lines += ["", "## 二、行业动态", ""]
    for topic in [t for t in topics if t["kind"] == "industry"]:
        selected = [a for a in articles if topic["id"] in a["topics"]][:config.get("report_items_per_topic", 4)]
        lines += [f"### {topic['label']}（{count[topic['id']]} 条）", ""]
        if not selected:
            lines += ["- 本期未获取到符合时间窗口的报道；不代表该行业没有新闻。", ""]
            continue
        for item in selected:
            when = datetime.fromisoformat(item["published_utc"]).astimezone(now.tzinfo).strftime("%m-%d %H:%M")
            lines.append(f"- **[{safe_md(item['title'])}]({item['url']})** — {safe_md(item['source'])} · {when}")
            if item["excerpt"] and item["excerpt"].casefold() != item["title"].casefold():
                lines.append(f"  - RSS 摘要：{safe_md(item['excerpt'][:190])}")
        lines.append("")
    lines += ["## 三、地区观察", ""]
    for topic in [t for t in topics if t["kind"] == "region"]:
        selected = [a for a in articles if topic["id"] in a["topics"]][:3]
        lines += [f"### {topic['label']}（{count[topic['id']]} 条）", ""]
        if not selected:
            lines.append("- 暂无符合窗口的有效报道。")
        for item in selected:
            lines.append(f"- [{safe_md(item['title'])}]({item['url']}) — {safe_md(item['source'])}")
        lines.append("")
    lines += ["## 四、跨行业归纳与后续观察", ""]
    if insights:
        lines += [insights, "", f"> {ai_note}", ""]
    else:
        lines += [
            "本期默认采用保守型归纳：按行业与地区整合报道、合并重复主题，并保留逐条来源。"
            "只有启用可选 LLM 后才会额外生成跨行业趋势研判；不把媒体标题擅自升级为已核实结论。",
            "",
            f"> {ai_note}", "",
        ]
    lines += [
        "## 五、样本质量与采集异常",
        "",
        f"- 去重后有效报道：{len(articles)}；查询与直连 RSS 成功：{succeeded}/{total_feeds}。",
        "- 报道数量与地区覆盖受到 RSS 采样、时区、媒体可用性、语言检索及标题相似性的影响。",
        "- 本日报不保证覆盖世界上每个国家、每家媒体或每个行业，也不替代专业尽职调查。",
    ]
    if errors:
        lines += ["", "### 失败的采集入口", ""]
        for error in errors:
            lines.append(f"- {safe_md(error['feed'])}：{safe_md(error['error'])}")
    lines += ["", "---", "", "生成程序与方法：见仓库 README；结构化原始记录请查看同日 data.json、sources.csv。", ""]
    return "\n".join(lines)


def run() -> int:
    now = now_local()
    date_text = os.getenv("REPORT_DATE", now.strftime("%Y-%m-%d"))
    folder = report_path(date_text)
    if (folder / "report.md").exists() and os.getenv("FORCE_REPORT") != "1":
        print(f"[SKIP] 当日报告已存在：{folder / 'report.md'}")
        return 0
    config = json.loads((ROOT / "config" / "topics.json").read_text(encoding="utf-8"))
    articles, errors, succeeded = collect_news(config, now)
    if not articles:
        raise RuntimeError("所有有效报道为 0，拒绝归档虚假空报告；请检查网络、RSS 或时间窗口")
    # Deep investment synthesis uses actual publisher excerpts rather than title-only guesses.
    brief, investment_metadata = investment_brief(articles, config["topics"], date_text,
                                                    max_items=int(os.getenv("INVESTMENT_ARTICLES", "26")))
    insight_note = ("详见报告开头的投资委员会晨报；" +
                    ("模型已生成跨行业判断" if investment_metadata["llm_success"]
                     else "模型尚未配置/不可用，不能虚构专家结论"))
    appendix = render_report(date_text, now, config, articles, errors, succeeded, "", insight_note)
    content = brief + "\n---\n\n" + appendix
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "investment-brief.md").write_text(brief, encoding="utf-8")
    (folder / "report.md").write_text(content, encoding="utf-8")
    payload = {
        "report_date": date_text,
        "generated_at": now.isoformat(),
        "window_hours": config.get("window_hours", 24),
        "unique_articles": len(articles),
        "feed_success": succeeded,
        "feed_failure": len(errors),
        "investment_research": investment_metadata,
        "errors": errors,
        "topics": config["topics"],
        "articles": articles,
    }
    (folder / "data.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with (folder / "sources.csv").open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["id", "published_utc", "source", "title", "topics", "url", "excerpt"])
        for a in articles:
            writer.writerow([a["id"], a["published_utc"], a["source"], a["title"], ";".join(a["topics"]), a["url"], a["excerpt"]])
    print(f"[DONE] {folder}: {len(articles)} unique articles; {succeeded} feeds ok; {len(errors)} failed")
    return 0


if __name__ == "__main__":
    sys.exit(run())
