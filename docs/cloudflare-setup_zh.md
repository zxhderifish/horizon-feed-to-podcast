# Cloudflare 配置指南

这套流水线需要托管两样东西：**单集音频**，以及**网站和 RSS**。Cloudflare 的免费额度
两样都够，而且 R2 不收流量费——这一点很关键，因为每个播客客户端下载一期节目都是流量。

| 内容 | Cloudflare 产品 | 对外地址 |
|---|---|---|
| 单集 mp3 | **R2** 桶 + 自定义域名 | `podcast.example.com/2026-07-28-en.mp3` |
| 网站、RSS、封面 | 连接发布仓库的 **Pages** 项目 | `feed.example.com/podcast-en.xml` |

原理上并不绑定 Cloudflare：R2 说 S3 协议，任何 S3 兼容存储都能用同一套凭据结构；Pages
也可以换成任意静态托管。下面只是本项目实际走过的路径。

**开始之前：** 先把域名接入 Cloudflare（控制台 → *Add a site*，然后在注册商那里把
nameserver 指向 Cloudflare）。下面两个自定义域名都假设该 zone 已经生效——否则你就得手工
加 DNS 记录并等待生效。

---

## 第一部分 —— 放音频的 R2 桶

### 1. 建桶

控制台 → **R2** → *Create bucket*。

- **名称**：随意（比如 `my-podcast-audio`）。这个值填进 `config.toml` 的
  `hosting.bucket`。
- **位置**：选 *Automatic*，除非你有特别理由。
- **存储类**：*Standard*。不要用 Infrequent Access——播客单集会被频繁读取，IA 会额外
  收取取回费用。

其余保持默认。此时桶是私有的，这是对的。

### 2. 绑定自定义域名

桶 → **Settings** → *Custom Domains* → *Connect Domain* → `podcast.example.com`。

**这一步才是让对象可以被公开读取的关键**，也是最容易搞错的一步。暴露 R2 有两种方式：

- **自定义域名**（用这个）：对象从你自己的域名提供，由 Cloudflare 边缘缓存，URL 稳定
  且体面。
- **公开开发 URL**（`pub-<hash>.r2.dev`）：有速率限制，不适合生产，放在公开 RSS 里也难看。
  跳过。

只要域名 zone 在同一个账号下，Cloudflare 会自动建好 DNS 记录。等一会儿，确认域名状态是
*Active*。

### 3. 创建受限的 API token

控制台 → **R2** → *API* → *Manage API Tokens* → *Create API Token*。

- **权限**：**Object Read & Write**。不要给 Admin——流水线只需要写入和删除对象，权限越窄，
  万一泄露损失越小。
- **指定桶**：只勾选你的播客桶。一个能碰到账号内所有桶的 token，早晚会让你后悔。
- **有效期**：可以不过期，或者设个提醒定期轮换。

结果页面会**只显示一次**这三个值：

| 页面上的名称 | 填进 `.env` 的键 |
|---|---|
| Access Key ID | `R2_ACCESS_KEY_ID` |
| Secret Access Key | `R2_SECRET_ACCESS_KEY` |
| （Account ID —— 侧边栏和 endpoint URL 里也有） | `R2_ACCOUNT_ID` |

页面还会给一个 *Token value*（`cfat_…`），那是给 Cloudflare 自家 REST API 用的。本流水线
走 S3 协议，**不需要**它。

S3 endpoint 是推导出来的，不用配置：
`https://<R2_ACCOUNT_ID>.r2.cloudflarestorage.com`，region 填 `auto`。这些已经写在
`tools/podcast_upload.py` 里了。

### 4. 填配置

`.env`（绝不提交）：

```
R2_ACCOUNT_ID=你的 32 位十六进制 account id
R2_ACCESS_KEY_ID=...
R2_SECRET_ACCESS_KEY=...
```

`config.toml`:

```toml
[hosting]
audio_url = "https://podcast.example.com"
bucket = "my-podcast-audio"
```

### 5. 先验证再信任

```bash
set -a && source .env && set +a
cd tools
printf 'test' > /tmp/probe.mp3
python podcast_upload.py /tmp/probe.mp3          # 打印公开 URL 和字节数
curl -sI https://podcast.example.com/probe.mp3   # 期望 200,content-type audio/mpeg
```

如果上传成功但 URL 返回 404，说明自定义域名没绑好（或还没生效）——凭据是好的，对象确实
在那里。测完清理：

```bash
python -c "import podcast_upload; podcast_upload._client().delete_object(
    Bucket='my-podcast-audio', Key='probe.mp3')"
```

---

## 第二部分 —— 放网站和 RSS 的 Pages 项目

### 1. 建发布仓库

这是**另一个** git 仓库，和本仓库分开：代码在这边，发布产物在那边。建一个空仓库（私有
也行，Pages 两种都能读），记下名字。

流水线会往里写：`index.html`、`YYYY-MM-DD.html`、`archive.html`、
`podcast-<lang>.xml`、`episodes.json`、`seen.json`、`covers/`、`scripts/`。

### 2. 接到 Pages

控制台 → **Workers & Pages** → *Create* → **Pages** → *Connect to Git* → 选那个仓库。

构建设置——产物本来就是静态的，没什么要构建：

| 字段 | 值 |
|---|---|
| Framework preset | *None* |
| Build command | 留空 |
| Build output directory | `/` |
| Production branch | `master` 或 `main`，与仓库一致 |

保存并部署。仓库是空的话第一次部署可能是空的，没关系。

### 3. 加自定义域名

Pages 项目 → **Custom domains** → *Set up a domain* → `feed.example.com`。

然后在 `config.toml` 里：

```toml
[hosting]
site_url = "https://feed.example.com"

[site]
brand = "feed.example.com"
brand_html = 'feed<span class="dot">.</span>example.com'
```

其他所有 URL——封面、各条 feed——都由 `site_url` 派生，所以域名只出现在这一处。

### 4. 验证

流水线第一次运行、推送到发布仓库之后：

```bash
curl -sI https://feed.example.com/podcast-en.xml   # 200,content-type application/xml
xmllint --noout <(curl -s https://feed.example.com/podcast-en.xml) && echo "XML 有效"
```

push 之后部署大约 30–60 秒。生效过程中你可能会看到 `307`，跟随重定向（`curl -L`）后是
`200`——这是正常的，不是配置错误。

---

## 值得提前知道的坑

**删掉的对象仍可能返回 200。** Cloudflare 边缘会缓存 R2 响应。删除对象后，`curl` 可能
还会从缓存里继续拿到它一段时间。对象确实已经没了；如果你需要 URL 立刻 404，就清缓存
（控制台 → *Caching* → *Configuration* → *Purge Everything*，或只清那一个 URL）。
**用同一个 key 覆盖文件时这点最要紧**——要假设听众在一小段时间内可能拿到旧内容。

**上传时必须显式设置 Content-Type。** R2 不会根据扩展名猜。`podcast_upload.py` 设的是
`audio/mpeg`，这是播客客户端对 enclosure 的要求；你用其他方式上传的东西（比如封面图）
得各自设对类型，否则浏览器会下载而不是显示。

**Range 请求天然可用。** 播客 App 靠 HTTP range 请求在单集内拖动进度。R2 原生支持——
不用配置，但知道这点可以省得你去翻设置。

**不需要配 CORS。** feed 和音频是播客客户端与服务器抓取的，不是浏览器 JavaScript，所以
默认（无 CORS 规则）就行。只有当你要做一个跨域读取音频的网页播放器时才需要加规则。

**别把 token 放进仓库。** 本项目已把 `.env` 加入 gitignore。万一泄露，去
*Manage API Tokens* 吊销后重新签发——桶里的内容不受影响。

**一个桶只干一件事。** 别往播客桶里丢无关文件。它通过自定义域名是公开可读的，你扔进去
的任何东西，都可以被人用可猜到的 URL 读到。

---

## 花多少钱

以下是撰写时的免费额度——Cloudflare 会调整，所以别想当然，查一下当前的
[R2](https://developers.cloudflare.com/r2/pricing/) 和
[Pages](https://developers.cloudflare.com/pages/platform/limits/) 定价页：

- **R2**：10 GB·月 存储、100 万次 Class A（写）操作、1000 万次 Class B（读）操作，
  **流量费为零**。
- **Pages**：每月 500 次构建，免费计划下请求数和带宽不限。

按每天两期、每期约 10 分钟、每个约 5 MB 算，大约 11 MB/天——一年约 4 GB，所以就算完全
不清理，存储也能在免费额度内待上一年多。写操作每天只有几次，对着 100 万的额度。每天两次
Pages 部署，一个月约 60 次，对着 500 的额度。

流量费是在别处会压垮这套方案的那一项：一档小有起色的播客，下载流量远大于存储量，
而 R2 对此不收费。

存储真的快到上限时，删掉最旧的单集对象（并从 `episodes.json` 里去掉对应条目）——或者
想保留完整存档的话，把它们转到更便宜的存储类。

---

## 排错

| 现象 | 可能原因 |
|---|---|
| 上传成功，公开 URL 404 | 自定义域名没绑，或还没变成 *Active* |
| `SignatureDoesNotMatch` | secret 填错，或 `R2_ACCOUNT_ID` 来自另一个账号 |
| `NoSuchBucket` | `config.toml` 里桶名与 R2 不一致，或 token 限定在别的桶 |
| 上传报 `AccessDenied` | token 是只读的——重新签发为 *Object Read & Write* |
| push 之后 feed URL 404 | Pages 还在部署，或 *Build output directory* 不是 `/` |
| feed URL 返回旧版本 | 边缘缓存；清一下这个 URL |
| 播客客户端放不出某期 | Content-Type 不是 `audio/mpeg`，或 enclosure URL 外部访问不到 |
| 提交时 Apple 拒收 feed | 先自己抓一遍 feed——可能是封面缺失、`itunes:owner` 邮箱没写，或 enclosure 返回非 200 |
