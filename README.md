# 全球全行业每日情报（Global Daily Intelligence）

每天自动采集全球多地区、多行业公开资讯，归档为中文 Markdown、结构化 JSON 与来源 CSV，并通过 SMTP 邮件发送日报。

**状态说明：** 工作流在代码提交到默认分支后按计划触发；首次启用邮件必须配置 GitHub Actions Secrets，详见 [小白使用说明](docs/小白使用说明.md)。未配置邮件服务时仍会保存日报，但不会声称邮件已发送。

## 每日定时
默认北京时间 **08:17（UTC 00:17）**，每天一次。支持 Actions 页面手动运行。新闻窗口为运行时向前 24 小时，日期按北京时间归档。GitHub 的定时作业可能延迟或偶发被跳过。

## 归档结构

```text
reports/
  2026/
    10/
      21/
        report.md           # 当日中文综合日报
        data.json           # 结构化新闻及统计
        sources.csv         # 新闻标题、媒体、时间、链接
        mail-sent.json      # 成功发送邮件后写入（不含账号密码）
```

## 功能边界
- 约 30 个行业和区域观察主题；多语言检索与公开 RSS 来源。广覆盖不代表采集全世界所有媒体。
- 根据实际抓到的标题/摘要生成**有出处的事实摘录**；可选 LLM 生成跨行业研判，必须有引用、不许凭空补事实。
- 过滤过期/重复报道，记录来源故障、样本规模。若没有数据则明确标注抓取失败，不生成假新闻。
- 报告按日期归档，同日默认不覆盖；可手工触发强制刷新。
- 邮件 HTML 正文 + Markdown 附件；用 SMTP 发送。密钥只放 GitHub Secrets，不写进仓库。
- 所有抓取结果均为信息摘要，不转载付费全文；涉及投资、医疗、法律等领域不构成专业建议。

## 立即配置
请查看 **[docs/小白使用说明.md](docs/小白使用说明.md)**，配置 SMTP 收发件人及应用密码。若不配置 `OPENAI_API_KEY`，照样生成可追溯的标题型日报，只是不生成 AI 深度研判。

## 程序
```bash
python -m unittest discover -s tests -v
python src/daily_digest.py
python src/send_email.py
```

本项目使用 Python 标准库，无需额外安装 pip 依赖。

## 声明
Google News RSS 属于未承诺 SLA 的公开聚合入口，内容可能受搜索可用性、地区、版权及服务策略影响。自动化需接受 GitHub Actions 配额及定时行为约束。
