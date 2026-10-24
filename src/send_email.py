#!/usr/bin/env python3
"""Email a dated report exactly once in normal successful workflow runs."""
from __future__ import annotations

import html
import json
import os
import re
import smtplib
import ssl
import sys
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from zoneinfo import ZoneInfo

from daily_digest import ROOT, TIMEZONE_NAME, report_path


def html_report(markdown: str) -> str:
    body = []
    for raw_line in markdown.splitlines():
        line = html.escape(raw_line, quote=True)
        line = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r'<a href="\2">\1</a>', line)
        if line.startswith("# "):
            body.append("<h1>" + line[2:] + "</h1>")
        elif line.startswith("## "):
            body.append("<h2>" + line[3:] + "</h2>")
        elif line.startswith("### "):
            body.append("<h3>" + line[4:] + "</h3>")
        elif line.startswith("- "):
            body.append("<p style='margin:6px 0;padding-left:14px'>• " + line[2:] + "</p>")
        elif not line.strip():
            body.append("<div style='height:9px'></div>")
        else:
            body.append("<p style='margin:5px 0'>" + line + "</p>")
    return """<!doctype html><html><head><meta charset="utf-8"></head>
    <body style="font-family:Arial,'Microsoft YaHei',sans-serif;max-width:880px;margin:30px auto;
    line-height:1.7;color:#263442;background:white">""" + "\n".join(body) + "</body></html>"


def build_message(report_date: str, folder: Path, sender: str, recipients: list[str]) -> EmailMessage:
    full_report = (folder / "report.md").read_text(encoding="utf-8")
    brief_file = folder / "investment-brief.md"
    report = brief_file.read_text(encoding="utf-8") if brief_file.exists() else full_report
    repo_link = (
        "https://github.com/mjw3177109/gezhongxinwenzongjie/tree/main/reports/"
        + report_date.replace("-", "/")
    )
    message = EmailMessage()
    message["Subject"] = f"【全球投资情报晨报】{report_date} | 事件总结·机会风险·大师框架"
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    message.set_content(report + "\n\nGitHub 归档：" + repo_link + "\n")
    message.add_alternative(html_report(report) + "\n<!-- " + html.escape(repo_link) + " -->", subtype="html")
    message.add_attachment(full_report.encode("utf-8"), maintype="text", subtype="markdown",
                           filename=f"Global_Daily_Report_{report_date}.md")
    if brief_file.exists():
        message.add_attachment(report.encode("utf-8"), maintype="text", subtype="markdown",
                               filename=f"Investment_Brief_{report_date}.md")
    csv_file = folder / "sources.csv"
    if csv_file.exists():
        message.add_attachment(csv_file.read_bytes(), maintype="text", subtype="csv",
                               filename=f"News_Sources_{report_date}.csv")
    return message


def deliver() -> int:
    date_text = os.getenv("REPORT_DATE", datetime.now(ZoneInfo(TIMEZONE_NAME)).strftime("%Y-%m-%d"))
    folder = report_path(date_text)
    if not (folder / "report.md").exists():
        print(f"[ERROR] 报告不存在：{folder / 'report.md'}", file=sys.stderr)
        return 2
    receipt = folder / "mail-sent.json"
    if receipt.exists():
        print(f"[SKIP] {date_text} 已有成功发送标记，不重复发送")
        return 0
    username = os.getenv("SMTP_USER", "").strip()
    password = os.getenv("SMTP_PASSWORD", "").strip()
    host = os.getenv("SMTP_HOST", "smtp.qq.com").strip()
    try:
        port = int(os.getenv("SMTP_PORT", "465"))
    except ValueError:
        print("[ERROR] SMTP_PORT 必须是数字", file=sys.stderr)
        return 2
    sender = (os.getenv("EMAIL_FROM") or username).strip()
    recipient_string = (os.getenv("EMAIL_TO") or username).strip()
    recipients = [r.strip() for r in re.split(r"[,;]", recipient_string) if r.strip()]
    if not username or not password or not host or not sender or not recipients:
        print("[ERROR] 尚未配置 SMTP_USER、SMTP_PASSWORD 等邮件参数；日报已归档，但邮件没有发送。",
              file=sys.stderr)
        return 2
    if any(("@" not in address or "\n" in address or "\r" in address) for address in recipients + [sender]):
        print("[ERROR] 发信/收信邮箱格式不正确", file=sys.stderr)
        return 2
    msg = build_message(date_text, folder, sender, recipients)
    context = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=35, context=context) as smtp:
            smtp.login(username, password)
            smtp.send_message(msg)
    elif port == 587:
        with smtplib.SMTP(host, port, timeout=35) as smtp:
            smtp.starttls(context=context)
            smtp.login(username, password)
            smtp.send_message(msg)
    else:
        print("[ERROR] 仅允许安全 SMTP 端口 465(SSL) / 587(STARTTLS)", file=sys.stderr)
        return 2
    receipt.write_text(json.dumps({
        "report_date": date_text,
        "status": "sent",
        "sent_at_utc": datetime.now(timezone.utc).isoformat(),
        "recipient_count": len(recipients),
        "method": "SMTP TLS",
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[DONE] 邮件成功投递 SMTP 服务器；收件人数：{len(recipients)}（最终邮箱送达仍由服务器决定）")
    return 0


if __name__ == "__main__":
    sys.exit(deliver())
