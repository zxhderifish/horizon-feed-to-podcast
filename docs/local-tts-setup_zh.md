# 本地 TTS 配置（Qwen3-TTS + vLLM-Omni）

可选。流水线开箱即用的配音走 Gemini TTS，这条路永远保留——本文只是在它前面加一个本地
模型，失败时自动回退到 Gemini。

值得折腾的理由不是省钱。每种语言每天一期，Gemini TTS 本来就便宜，而一块显卡另外 23
小时闲着也谈不上更省。真正站得住的理由是：不再有按分钟计费这件事要想，于是单集时长和
语言数量不再是预算决策；不再赌某家厂商的免费额度能撑过今年；以及稿子的内容不出本机。

## 各部分跑在哪

`tools/podcast_tts.py` 每种语言发一个 JSON 请求——整篇稿子、所有段落一次性——给
`tools/remote/synth_qwen.py`，后者加载模型、一批合成完所有段落、把带帧头的 PCM 写回来。
这要么是同机上的一个子进程，要么是显卡在别的机器上时的一次 SSH 调用。

合成器是无状态的：启动、合成、退出。没有常驻服务要守，重启后不用拉起，也不用守一个端口。

每次运行会打印 `tts=local` 或 `tts=gemini (...)`。best-of-N 会附加类似
`tts=local (best-of-2: 3/12 swapped, 0 loud)` 的摘要。**这行一定要看。**
出现 `asr=off` 或非零 `loud` 时，即使合成完成也需要排查。

## 前置条件

- **一块 NVIDIA 显卡，可用显存约 9 GB。** 实测峰值 8.5 GiB。
- **约 12 GB 磁盘**：首次运行会往 `~/.cache/huggingface` 下约 4 GB 权重，venv 本身约
  8 GB（vLLM 和 CUDA wheel 占大头）。
- **Linux + 可用的 CUDA 驱动。** `nvidia-smi` 要能认出你的卡。WSL2 可以，见文末。
- **Python 3.12 和 [uv](https://docs.astral.sh/uv/)。**

## 为什么是这个模型

`Qwen/Qwen3-TTS-12Hz-1.7B-Base`（Apache-2.0）。拿真实的一期节目做过横评，它在四件事上
胜出：

- **术语准确度。** 把输出转写回文字跟原稿比对，在技术节目最容易念错的地方——中文里夹的
  拉丁字母术语、英文里连着的缩写——它比 Gemini 和另一个开源竞品都好。
- **速度。** 所有段落一次性提交给调度器后，约 22 分钟音频花约 6.6 分钟 GPU 时间。
- **格式。** 输出 24 kHz 单声道，跟 Gemini TTS 一致，所以为其中一个做的音效包换到另一个
  不用改。48 kHz 立体声的模型意味着音效素材得重做。
- **许可证。** TTS 排行榜上好几个排名更高的模型权重是仅限研究用途的。如果你要公开发布
  音频，先看许可证，再看音质。

还有两件事省得你去找更大的：Qwen3-TTS 的开源权重就停在 1.7B / 12 Hz，更大和更高帧率的
只在闭源 API 里；Google 没有开源 TTS 模型，Gemma 也没有。

## 第一步：建 TTS 环境

vLLM 太重，不该塞进流水线自己的 venv。单独给它一个。在有显卡的机器上：

```bash
VLLM_OMNI_COMMIT=63ed0fdef4e6e9bdd866ea418ad9cf65e498c67a
mkdir -p ~/horizon-tts && cd ~/horizon-tts
uv venv --managed-python --python 3.12 .venv
uv pip install --python .venv/bin/python vllm==0.28.0 --torch-backend=auto
git clone -q https://github.com/vllm-project/vllm-omni.git
git -C vllm-omni checkout -q $VLLM_OMNI_COMMIT
uv pip install --python .venv/bin/python -e vllm-omni
mkdir -p vllm_omni_helpers
cp vllm-omni/examples/offline_inference/text_to_speech/qwen3_tts/end2end.py vllm_omni_helpers/
.venv/bin/python -c "import vllm, vllm_omni; print(vllm.__version__)"
```

预期输出 `0.28.0`。中间可能出现 vLLM 与 vLLM-Omni 版本不匹配的 `RuntimeWarning`，无害。

**检查安装日志里是 `torch==*+cu*` 而不是 `torch==*+cpu`。** 显卡检测失败时
`--torch-backend=auto` 会装 CPU 版 torch 并且一声不吭。你会在很久以后、合成慢得离谱时
才发现。

### `vllm_omni_helpers/end2end.py` 是一个钉死版本的依赖

`synth_qwen.py` 调用的是上游**示例文件**里的一个私有函数（`_estimate_prompt_len`）。
所以上面的 clone 钉在一个具体 commit 上，而不是 `--depth 1` 拉 `main`：明年再跑一遍这套
配置，必须复制到同一个 helper。

如果这个文件丢了、或者上游改了函数名，`synth_qwen.py` 会报出文件路径并指回这一步，而不是
抛一个光秃秃的 `ImportError`。用上面那行 `cp` 重新复制即可。

### 可选的 best-of-N 裁判

默认仍是每段生成一次，以保持原有约 9 GB 显存需求。显存更大的卡可以安装可选 ASR 裁判，
让每段生成两个独立候选：

```bash
cd ~/horizon-tts
uv pip install --python .venv/bin/python faster-whisper
# 写进流水线的 .env
TTS_TAKES=2
```

`TTS_TAKES=2` 时，0.5 秒窗口 RMS 超过 0.30 的候选先被淘汰；Qwen 释放显存后，
faster-whisper `large-v3` 转写两个候选，选择与原稿更接近的一个。Whisper 加载失败不会中断
合成，但会退化成只按响度选择，并在 marker 里写 `asr=off`。

该模式已在 16 GB 显卡上跑通；9 GB 最低配置下的显存表现尚未验证，小卡请保持一次。
首次运行还会向 Hugging Face cache 下载约 3 GB Whisper 权重。

## 第二步：生成声音参考音

本地模型没有预置音色，它从一段约 30 秒的样本 + **样本里逐字对应的文稿**克隆出一个。
见 [`assets/voice/README.md`](../assets/voice/README.md)——只有一条命令，但它会花掉真实的
Gemini 额度，并且改变之后每一期的声音，所以先读它。

## 第三步：把流水线指过去

**和流水线同一台机器**——常见情况。告诉它哪个解释器装了 vLLM：

```bash
TTS_PYTHON=~/horizon-tts/.venv/bin/python
```

就这些。`podcast_tts.py` 会把 `tools/remote/synth_qwen.py` 当子进程跑，直接读仓库里的
`assets/voice/`。

**显卡在另一台机器上**——加一个 SSH 目标：

```bash
TTS_REMOTE_HOST=gpu-box          # ~/.ssh/config 里的 Host，或 user@host
TTS_REMOTE_DIR=~/horizon-tts     # 可选，这就是默认值
```

密钥登录必须能免交互完成，远端目录要和第一步建的是同一个。每次运行都会先把
`synth_qwen.py` 和 `assets/voice/` scp 过去，所以这个仓库是唯一事实来源，两边不会漂移。
这条路径下 `TTS_PYTHON` 会被忽略，用的是远端的 `.venv/bin/python`。

然后渲染一期、听一遍，再决定信不信它。

## 关掉它

```bash
TTS_LOCAL=0
```

完全跳过本地路径，直接走 Gemini。还在观望阶段值得设上，因为默认是本地优先。

## 预期表现

在一块 RTX 5060 Ti（16 GB）上实测，两种语言各约 13 段：

| | 产出音频 | 墙钟时间 |
|---|---|---|
| 中文 | 636 s | 约 6 分钟 |
| 英文 | 564 s | 约 6 分钟 |

固定开销里模型加载占大头——两段的测试仍要约 2.5 分钟，所以短单集并不会按比例变快。
当前上限是 `LOCAL_BUDGET_S = 1000` 和 `ATTEMPT_TIMEOUT_S = 900`，给双候选生成和 ASR
留出余量；卡更慢就调大。

同样的稿子，本地声音的语速比 Gemini 快 3–9%。这是需要去听的差别，不是错误。

## 排障

先看这次运行打印的 `tts=` 值。纯 `tts=local` 是单候选路径；best-of-N marker 会报告
切换数和响度异常。`asr=off` 表示裁判没能加载，只按响度选取。`tts=gemini (local failed:
...)` 带着一行原因，完整错误在 stderr 里。

**best-of-N 显示 `asr=off`** —— 确认 `faster-whisper` 装在同一个 TTS venv，然后真正跑一
期节目。只有 marker 出现 `best-of-2` 且没有 `asr=off` 才证明生产路径可用；仅仅构造
`WhisperModel` 不足以验证 CUDA encoder。若 stderr 报 CUDA 动态库缺失，在该 venv 安装
CUDA 12 的 `nvidia-cublas-cu12` 与 `nvidia-cudnn-cu12` wheel；合成器会在导入 ctranslate2
前预加载其中的动态库。

**`malformed TTS payload: bad header (...): 'INFO ...'`** —— 有东西在带帧头的载荷之前
写了 stdout。vLLM 默认的日志 handler 就指向 stdout，所以 `synth_qwen.py` 的 `__main__`
会先保存 fd 1、把 fd 1 指向 stderr，只往保存下来的描述符写载荷。如果还复发，错误里的
header 片段会指出是谁写的。**不要**用 `VLLM_CONFIGURE_LOGGING=0` 或
`contextlib.redirect_stdout` 去"修"它——这两者都只覆盖一部分写入者，挡不住 C 扩展和
fork 出来的子进程。

**`cannot recover segment order from request ids ...`** 或
**`unexpected request id format ...`** —— vllm-omni 改了请求命名方式。它用的是
`f"{index}_{uuid4()}"`，`order_request_ids` 解析前缀。这里的排列校验是刻意的：格式一变必须
大声失败，而不是产出一集听起来像回事、顺序却是乱的节目。对一下上面钉的
`VLLM_OMNI_COMMIT` 和机器上实际的版本。

**`missing remote helper .../end2end.py`** 或 **`has no _estimate_prompt_len`** ——
见第一步里钉版本依赖那一节。

**`attempt 1 timeout after 600s`** —— 合成器接了活然后没声了。超时后**不会**重试：那个
孤儿进程还占着显存，第二次尝试只会落到一块被占满的卡上。让它自己退出。快速失败（机器
没开、连接被拒、vLLM 崩了）仍然享受三次尝试。

**显存不足，或者进程没有任何 traceback 就死了。** 大约在 13.75 到 15.9 GiB 之间有一个
悬崖，越过去内核会直接杀掉进程——`OutOfMemoryError` 根本来不及抛，所以再多的异常处理也
没用。批次大小刻意没做成可配置项；如果你要适配更小的卡，去 `synth_qwen.py` 里把它改小，
而不是加一个永远触发不了的回退。

**永远不要对正在做 CUDA 计算的进程 `kill -9`。** 这会把显卡驱动搞死，只能重启恢复。
客户端超时不代表合成器停了——盯着 `nvidia-smi`，让它自己跑完。

## WSL2 注意事项

上面的一切在 WSL2 下都能用，有四个坑：

- **`nvidia-smi` 不在默认 PATH 上。** 任何需要它的命令之前都要
  `export PATH=/usr/lib/wsl/lib:$HOME/.local/bin:$PATH`——**包括 `uv pip install`**，
  它靠 `nvidia-smi` 判断要不要装 CUDA 版 torch。这是静默装成 `+cpu` 的头号原因。
- **流水线连过来之前 WSL 必须已经在跑**（如果你是从另一台机器走 SSH 驱动它）。Windows
  重启后它是停的，直到有东西把它拉起来。
- **WSL 里的 `sshd`** 才是 SSH 路径真正对话的对象，用 `systemctl is-active ssh` 确认。
- **如果显卡卡死**，恢复手段是在 **Windows 侧**（PowerShell/cmd）执行 `wsl --shutdown`。
  WSL 内部没有任何命令能解开驱动，也没法从 SSH 进 WSL 的会话里脚本化完成。
