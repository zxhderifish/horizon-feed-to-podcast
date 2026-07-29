---
name: daily-feed
description：每日feed
---

你要执行每日 Horizon Radar 新闻简报。全部在本地机器运行；git、gh 和 uv 都已安装并完成认证，无需任何额外 token。

仓库（已在本地克隆）：
- 代码 + skill：<repo>
- 发布站点 + 状态：<site>（git remote：github.com/<you>/<site-repo>，Cloudflare Pages 自动部署到 https://feed.example.com）

步骤：
1. 先更新两个仓库：`git -C <repo> pull --ff-only` 和 `git -C <site> pull --ff-only`。

2. 读 <repo>/skills/horizon-radar/SKILL.md 并端到端严格遵循它。关键点：
   - 抓取（确定性）：`cd <repo>/horizon && uv run horizon --fetch-only --json`，从 stdout 解析 JSON 数组，忽略 stderr 进度。
   - 去重：对 <site>/seen.json，用 tools/dedup.py 的 filter_unseen（items，ledger） 只保留新条目（返回 （fresh_items，new_entries））。
   - 打分 0–10：按 SKILL 里的 rubric，面向 SE/AI/ML/系统 + 半导体读者。按 horizon/data/config.json 的 filtering.ai_score_threshold 过滤、降序排序、做 topic dedup、应用 quota。
   - 富化：对入选条目用 WebSearch/WebFetch 写 background（2–4 句，基于真实搜索结果，不要编造）、community discussion（若有评论）、refs（只放你真正检索过的 URL，绝不臆造）。
   - 构建 issue.json：双语（EN+ZH），schema 见 SKILL 第 10 步；source_type 必须正好是 Hacker News/Reddit/RSS/GitHub；background/discussion 缺失时为 null。写到 <site>/issue.json（临时文件，已被 .gitignore 忽略——绝对不要提交它）。
   - 渲染：`cd <repo>/horizon && uv run python -c "import json,sys; sys.path.insert(0,'../tools'); import render; from pathlib import Path; render.write_issue(json.load(open('<site>/issue.json')), Path('../tools/templates'), Path('<site>'))"`。这会写出 index.html、YYYY-MM-DD.html、YYYY-MM-DD.meta.json、archive.html。
   - 更新去重账本：把第 2 步的 new_entries 合并进 <site>/seen.json，并用 tools/dedup.py 的 prune_ledger（ledger，days=7） 修剪到 7 天，写回。

3. 发布：在 <site> 里执行 `git add -A && git commit -m "feed: <UTC日期 YYYY-MM-DD>" && git push origin master`。Cloudflare Pages 会自动部署。若 push 失败，报告错误并停止（保留 commit 供下次重试）。

4. 若抓取返回空数组或没有新条目：不要提交空 issue，保留上一期在线，报告 "no new items"。

5. 播客（你的节目）：文字版发布成功后，严格按
   <repo>/skills/horizon-radar/SKILL.md 的
   Podcast 段（步骤 15–20）执行：写两份播报稿 → podcast_tts.py 合成 →
   podcast_upload.py 上传 R2 → 更新 episodes.json → podcast_rss.py 重生成
   RSS → 单独 commit（"podcast：<日期>"）并 push。环境变量在
   <repo>/.env（用 `set -a && source .env && set +a` 加载）。
   任何一步失败：跳过播客、照常报告文字版结果，并在报告中写明播客失败原因
   （下次运行会自动重试）。若当天 "no new items"，播客也跳过。

完成后报告：抓取数量、入选数量、commit SHA（或 "no new items"），确认 https://feed.example.com 已更新；若播客成功，附两期时长与 mp3 URL，若失败附原因。