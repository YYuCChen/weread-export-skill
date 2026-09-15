# weread-export —— 微信读书整本导出

[![tests](https://github.com/YYuCChen/weread-export-skill/actions/workflows/tests.yml/badge.svg)](https://github.com/YYuCChen/weread-export-skill/actions/workflows/tests.yml)

## 这是什么

把微信读书的一本书完整导出为 **Markdown / EPUB / PDF**，并生成一份可复核的**核验报告**：
正文按章节切分、插图按原文位置内嵌、对照平台逐章字数核验可达标。

能力清单：

- 预检：登录态 / 书籍可读性 / 端到端冒烟 / 平台逐章字数基线（全自动，可复跑）；
- 导出：逐页抓取（Canvas 文字 + 虚拟 DOM 文字 + 插图），按章节切分保存，中断可续；
- 核验：平台逐章对照（缺章 / 重复段落 / 重复章节 / 图片引用与文件 / 末章一致 / 字数比例）；
- 交付：`<书名>.md`（图片内嵌）/ `<书名>.epub` / `<书名>.pdf` / `核验报告.txt` 四件套。

目录结构与分工：

| 部件 | 用途 |
|---|---|
| `SKILL.md` | agent 入口：适用条件、停询触发点、合规边界、五步流程 |
| `README.md` | 本文件 —— 给人看的上手说明与故障对照表 |
| `references/playbook.md` | 「症状 → 判据 → 修法」速查表：踩过的坑与脚本落点 |
| `scripts/` | 预检 / 导出引擎 / 图片下载 / 核验 / 三格式转换 / 离线后处理 |
| `scripts/tests/` | 回归测试（导航 / 抓取 / 切章渲染 / 后处理 + 包级检查） |

参考出处：上游开源项目 **`lbq110/weread-exporter`**（本包在其基础上合入本机实测的全部修复）。
**仅供个人学习研究使用** —— 请勿用于商业用途或大规模传播，请尊重著作权。

## 工作原理（简版）

1. **Playwright 自动化**：启动本机 Chromium，持久化登录会话（扫码一次，后续自动复用）。
2. **Canvas fillText Hook**：注入钩子拦截 `fillText` 调用，连同变换矩阵一起记录真实页面坐标
   （否则标题字会被织进正文行）。
3. **双页拆分**：同一屏可能并排两个 canvas（左页 + 右页 / 标题页 + 正文页），按 canvas 物理区间分桶。
4. **虚拟 DOM 通道**：长章节后半段以乱序绝对定位 span 渲染，滚动整章后按文档坐标重建、重叠窗口去重。
5. **图文交错**：文字行与视口内图片按 y 坐标排序，插图落在对应段落之间；并记录下载名单。
6. **章节切分**：按「扉页字号 + 目录标题前缀」识别新章节（顶栏标题滞后一页，不可靠）；
   切章后比较一律归一化，避免重名重复章。
7. **重放判重**：长章节尾页会被反复重放；按 3-gram 重合度（阈值 0.85）只丢弃几乎完全重复的整页。
8. **后处理**：跨章断句合拢、行内双写折叠、溢出末行标题下移、封面章补齐、合并稿重建。
9. **退出码契约（0–7）**：每个脚本以退出码 + `⛔` / `✅` 标记行报告结果，agent 据此稳定分支。
10. **出错即停**：登录失效 / 访问受限 / 结构变化类失败不静默重试——停下询问（见 `SKILL.md` 停询与升级路径）。

## 环境要求

- Python 3.10+（实测 3.12）；依赖见 `scripts/requirements.txt`（playwright / pytest / anyio / Pillow）。
- 系统：macOS 为主（sips 压缩图）；Windows / Linux 尽力可跑（Pillow 回退、pandoc 动态解析、临时目录取系统值）。
- 磁盘：状态目录 + 交付目录合计数十 MB 量级（含插图与 PDF）；登录态约数百 MB（浏览器 profile）。

## 安装到 Thincoder

在 Thincoder 项目根目录执行：

```bash
mkdir -p .thincoder/skills
git clone https://github.com/YYuCChen/weread-export-skill.git \
  .thincoder/skills/weread-export
```

如果希望在所有项目中使用，可安装到用户级目录：

```bash
mkdir -p ~/.thincoder/skills
git clone https://github.com/YYuCChen/weread-export-skill.git \
  ~/.thincoder/skills/weread-export
```

安装后可直接对 Agent 说：

> 用 weread-export 把这本书导出：<微信读书链接>

## 5 分钟上手

**第 1 步：安装依赖**（macOS 示例；Linux 把 `brew` 换成对应包管理器命令）

```bash
cd .thincoder/skills/weread-export
python3 -m pip install -r scripts/requirements.txt
python3 -m playwright install chromium
brew install pandoc          # Linux: sudo apt install pandoc
```

**第 2 步：扫码登录**（首次一次，之后长期复用）

```bash
python3 scripts/preflight.py "https://weread.qq.com/web/reader/<book_id>"
```

首次运行会弹出浏览器窗口 → 用微信扫码登录。登录态保存在 `~/.weread-export/profile/`，换项目不用重扫。
同一命令顺带完成：登录态 / 可读性 / 端到端冒烟 / 平台逐章字数基线的检查与抓取。

**第 3 步：对 agent 说一句话**

> 用 weread-export 把这本书导出：<链接>

agent 会按 `SKILL.md` 跑完 预检 → 导出 → 核验 → 三格式转换 → 交付四件套。

## 运行节奏与耗时

- 导出速度受翻页等待限制（每页 1–2 秒，防封底线，默认不加速）；整本量级 **约 10–15 分钟**
  （实测：抓正文 ~6.5 分钟 / 220 页 ÷ 每页 ~1.8 秒 + 后处理与图片 ~3–4 分钟 + 三格式 ~1 分钟；随书长线性变化）。
- 中途中断（关窗 / 断网 / 手动停止）不会丢进度：重跑同一条命令会从**已完成章节**续起。
- 图片分两步：抓取时只记录 URL（避免下载阻塞翻页），抓完整本后 8 线程并发下载到 `images/`。
- 核验与三格式转换都是分钟级；可重复运行，输出覆盖写（幂等）。

## 手动路径（不用 agent）

```bash
S=.thincoder/skills/weread-export/scripts
python3 $S/preflight.py "<链接或 book_id>"            # 1 预检 + 平台基线
python3 $S/export_precise.py "<链接或 book_id>"        # 2 导出（长跑；中断重跑同命令续传）
python3 $S/download_images.py <book_id>               # 3 图片补齐（可重复运行）
python3 $S/verify_export.py <book_id> \
  --report-out "<当前项目目录>/<书名>/核验报告.txt"       # 4 核验（+交付副本）
python3 $S/make_formats.py \
  ~/.weread-export/books/<book_id>/<书名>.md "<当前项目目录>/<书名>/"   # 5 三格式
python3 $S/export_precise.py --postprocess <book_id>  # 不重爬，重跑后处理并重建合并稿
```

## 常见故障对照表

| 症状 | 原因 | 处理 |
|---|---|---|
| `ModuleNotFoundError: playwright` 等 | 依赖未装 | 按「5 分钟上手」第 1 步安装 |
| 浏览器启动报缺 chromium | chromium 未装 | `python3 -m playwright install chromium` |
| 停在登录页 / `⛔ 登录失效`（退出码 2） | 未登录或登录过期 | 在弹出窗口扫码重登，重跑当前步骤 |
| 详情页或阅读器显示「去 App 阅读」 | 出版社限制网页端 | 此类书无法导出（退出码 3）；换书或去官方 App 阅读 |
| 0 章秒退 / `⛔ 页面结构可能变化` | 阅读器页面结构变化 | 停下保留现场；对照 playbook「选择器」节排查 |
| `找不到 pandoc`（退出码 7） | pandoc 未装 | `brew install pandoc`，或设置 `$PANDOC` 指向可执行文件 |
| 图片下载 fail | 网络 / 链接失效 | 重跑 `download_images.py`（已下载的自动跳过） |
| 中断了怎么续传 | —— | 重跑同一条导出命令，自动从已完成章节续起 |
| 核验未达标（退出码 5） | 缺章 / 重复 / 失效引用等 | 打开核验报告看不达标字段；对照 playbook「核验基线」节 |
| 系统提示里看不到本技能 | 系统提示只列部分技能 | 直接说技能名 `weread-export`，或用 skill 工具 list |

## 输出与目录说明

- 状态目录 `~/.weread-export/`（环境变量 `WEREAD_EXPORT_HOME` 可覆盖）：
  - `profile/` —— 登录态（浏览器持久化目录，跨项目复用）；
  - `books/<book_id>/` —— 工作数据：`chapters/`（逐章 md）、`raw/`（图片名单与字数）、
    `images/`（插图与封面）、合并稿 `<书名>.md`、`_catalog.json`、`_meta.json`、
    平台基线 `_platform_chapterinfo.json`、核验报告 `_verify_report.txt`；
  - `runs.log` —— 每次导出的开始与结果（供防封单日计数核对）。
- 交付目录 `<当前项目目录>/<书名>/`：`<书名>.md`（图片 base64 内嵌，单文件自包含）、
  `<书名>.epub`、`<书名>.pdf`、`核验报告.txt`。
- 清理状态（含登录态）：`rm -rf ~/.weread-export`（下次运行需重新扫码）。

## 已知限制

- 需要有效的微信读书账号，且对目标书有阅读权限（无限卡 / 已购买）；
- 部分出版社限制网页端阅读（「去 App 阅读」），此类书无法导出；
- 纯图廊章节图片密集时，图注与图的配对偶尔差一位；正文章节里图片相对段落的位置准确；
- 以 macOS 为主；Windows / Linux 尽力可跑（无硬编码本机路径，图压缩有 Pillow 回退）。

## 合规与声明

- 仅导出**账号已获阅读权限**的书；不绕过验证码 / 会员 / 付费墙。
- 导出物**仅个人使用**：不传播、不上传、不分享给不特定人群。
- 参考出处：`lbq110/weread-exporter`；**仅供个人学习研究使用**，请尊重著作权。

## 与上游的关系

- 参考上游项目 [`lbq110/weread-exporter`](https://github.com/lbq110/weread-exporter)，并已获上游作者授权公开发布。
- 本仓库不主张上游代码的权利；使用时仍须遵守「仅供个人学习研究使用，请勿用于商业用途或大规模传播」的限制。
- 本包 = 上游代码 + 本机实测的全部修复（本目录 `references/playbook.md` 逐条记录）；
  上游后续演进需手动同步（无自动跟踪）。

## 开发与测试

```bash
python3 -m pip install -r scripts/requirements.txt
python3 -m pytest -q scripts/tests
```

GitHub Actions 会在每次 push 和 pull request 时运行同一套回归测试。
