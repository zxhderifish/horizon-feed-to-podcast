# horizon-feed-to-podcast

<div align="center">

[English](README.md) &nbsp；·&nbsp；**简体中文**

</div>

---

一份每天发两遍的简报：一遍是网页，一遍是按语言各一期的播客。一次典型的运行会读四十来条资讯，
留下十五条，写成稿，并在早餐前把两期节目送上 Apple Podcasts。

实际跑着的例子——两档节目出自同一条流水线、同一个早上：
硅基信噪比（中文，[小宇宙](https://www.xiaoyuzhoufm.com/podcast/6a69b0efdd4effa43566ecee) / [Apple Podcasts](https://podcasts.apple.com/us/podcast/%E7%A1%85%E5%9F%BA%E4%BF%A1%E5%99%AA%E6%AF%94/id6795804021)）
和 [Silicon SNR](https://podcasts.apple.com/us/podcast/silicon-snr/id6795804285)（英文）。同一条 RSS 喂给所有平台。

它存在的理由是：订阅量早已超过能读完的时间，而多数 AI 摘要工具会把所有内容压成同一种语气。
打分才是关键的一步，而打分是判断题，所以它放在一份可以随手修改的 prompt 里，而不是一个要重写
才能调整的排序函数里。

## 这个仓库里到底有什么

真正重要的文件是 Markdown，不是 Python。

`skills/horizon-radar/SKILL.md` 装的是编辑判断：一条资讯怎么打 0–10 分、两条报道什么时候算
同一件事、一段稿子怎么写才有人听得下去。每天早上智能体读它，然后干活。简报质量下降时，改的是
这个文件。

`tools/` 是管道部分——去重、渲染、编码、上传、生成 RSS。写成脚本，是因为这些步骤每次都该产出
完全一样的结果。

```
抓取 → 去重 → 打分 → 富化 → 撰稿 → 渲染 → 推送网站
                                      ↓
                     写播报稿 → TTS → 上传 → RSS → 推送 feed
```

## 它不是科技新闻工具

上面那个参考部署做的是 AI 和半导体，但机器本身不在意。听众和话题都写在 `config.toml` 里，
抓取器指向哪里由你决定。示例配置特意写成一份影视节目的设置，免得科技味看起来是内置的。

## 为什么没用 NotebookLM

这是最先会想到的方案，而且 Audio Overview 确实做得好。但它没有面向个人的 API，只有按坐席计费
的 Google Cloud 产品，给一个人的日更节目用不划算。第三方那些包装是逆向出来的，随时会失效。

在本地生成稿子反而更好：智能体本来就在流程里，于是结构和侧重都能自己控制。API 只负责配音。
Gemini TTS 的中英文都不错，这个量级下免费。

## 需要什么

- Python 3.11+、[uv](https://docs.astral.sh/uv/)、`ffmpeg`
- 一个 [Gemini API key](https://aistudio.google.com/apikey)
- 放 mp3 的对象存储。Cloudflare R2 比较合适——播客托管的成本大头是流量，而 R2 不收流量费。
- 放网站和 RSS 文件的静态托管。能托管 git 仓库的都行。
- 一个能按计划执行 prompt 的编码智能体。本项目是基于
  [Claude Code](https://claude.com/claude-code) 的定时任务开发的，这一环也是唯一没有直接替代
  品的部分。

存储和托管都用 Cloudflare 的话，[docs/cloudflare-setup_zh.md](docs/cloudflare-setup_zh.md)
写了逐步点哪里，包括两个很容易搭进去一下午的地方：R2 的边缘缓存在对象删除后还会继续提供一段
时间，以及上传时必须显式设置 `Content-Type`。

## 上手

```bash
git clone https://github.com/zxhderifish/horizon-feed-to-podcast
cd horizon-feed-to-podcast
cp config.example.toml config.toml
cp .env.example .env
```

先填 `config.toml`——域名、桶名、节目名、音色。其他配置大多从它派生。这两个文件都在 gitignore
里。

**1. 给产物新建一个仓库。** 代码在这边，发布出去的文件在那边。建一个空仓库，接上静态托管，
把域名指过去。流水线会把 HTML、RSS、`episodes.json`、`seen.json` 写进去并 push。

**2. 配置桶。** 建桶，绑一个自定义域名（**是这一步**让对象变成公开可读，不是 `r2.dev` 那个
地址），再签一个只限这个桶、有对象读写权限的 token。凭据写进 `.env`，桶名和公开地址写进
`config.toml`。

**3. 定义简报覆盖什么。** 两个文件：

- `config.toml` 里的 `[editorial]`——听众、范围内话题、要放进来的相邻领域，以及什么东西再热
  也不要。打分会先读它。
- `horizon/data/config.example.json`——要抓的 RSS、subreddit、仓库，以及打分阈值和配额。复制
  成 `horizon/data/config.json`。

两边要一致。一份写着建筑的 rubric 去抓 r/programming，会把所有条目判成噪声，然后发出一个空
页面。

**4. 加封面。** 至少 1400×1400，放在产物仓库的 `covers/cover-<lang>.jpg`。只需要让 feed 过
校验的话，`tools/make_covers.py` 能生成朴素的占位图。

**5. 先手动跑一遍。** 照着 `skills/horizon-radar/SKILL.md` 走完，确认网页和第一期音频都没问
题再自动化。然后把 `examples/scheduled-task.md` 改成适配自己定时任务的版本。

**6. 提交 feed。** Apple Podcasts Connect：**+ → New Show → Add a show with an RSS feed**。
每种语言提交一次，它们是两个独立节目。其他平台大多接受 RSS 地址，或从 Apple 目录同步。

## 支持哪些信源

RSS、Hacker News、Reddit、GitHub release、Telegram、Twitter/X、OpenBB、OSSInsight。只有
GitHub 可选地需要 token（`GITHUB_TOKEN`，用于提高限流额度）；Twitter 需要 token 或浏览器
cookie。

只要信源提供 feed 就能用——大多数新闻站、博客、期刊、论坛、YouTube 频道、邮件列表归档都提供。
留着其他抓取器，是因为 Hacker News 和 Reddit 带评论（打分把评论当质量信号），以及 GitHub 的
release 比它的 feed 干净。

既没 feed 又没现成抓取器？用 feed 桥接服务，或者照 `horizon/src/scrapers/base.py` 写一个。

需要注意：`horizon/data/presets.json` 里那八套现成信源包全是科技领域的，因为上游 Horizon 本身
就是科技工具。换成别的主题就得手写 RSS 列表——每个 feed 也就几行。

## vendored 的抓取器是魔改过的

`horizon/` 是 [Thysrael/Horizon](https://github.com/Thysrael/Horizon)（MIT）的 vendored 副
本，不是原版。加了 `--fetch-only --json`，让它只抓取和去重、不做自己那套 AI 摘要；并移除了
用不上的 MCP 层和 CI。[`horizon/VENDORED.md`](horizon/VENDORED.md) 里列了每一处改动。要原版
Horizon 请去上游。

## 关于播报

TTS 在长请求上会漂：一次要它念十分钟，语速会飘，后半段质量下降。所以稿子按 `---` 切段，每段
单独合成，再拼接起来。

段落长度的影响比预想中大。低于八十来字，念出来的语气会偏重；高于八百字质量开始下滑。目标区间
是 100–800 字。`***` 标记快讯块之前的边界；`~~~` 用来拆开长段、又不在接缝处插音效。

音效是可选的，没随仓库分发——格式和挂载点见
[`assets/audio/README.md`](assets/audio/README.md)。适合一个主题的开场曲放到另一个主题上就是
错的，自己挑。

## 成本

每天两期十分钟大约 11 MB，一年约 4 GB。R2 免费额度装得下还有余，而且它不收流量费——否则流量
才是成本大头。Gemini TTS 在这个量级内免费，超出后一天预算几美分。每天两次 Pages 部署远在免费
构建额度之内。

真正的成本是编码智能体的订阅，而那笔钱通常本来就在付。

## 测试

```bash
cd tools && python -m pytest . -q
```

测试跑在 `config.example.toml` 上，所以新克隆下来、什么都没配就是通过的。

## 许可

MIT。vendored 的 `horizon/` 同样是 MIT——见 `horizon/LICENSE` 和 `horizon/VENDORED.md`。
