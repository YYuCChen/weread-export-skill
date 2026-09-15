# weread-export：微信读书导出 Skill

[![tests](https://github.com/YYuCChen/weread-export-skill/actions/workflows/tests.yml/badge.svg)](https://github.com/YYuCChen/weread-export-skill/actions/workflows/tests.yml)

把一本微信读书导出成 Markdown、EPUB 和 PDF。

你只需要把书的链接发给 Thincoder Agent，剩下的步骤由 Agent 完成。

## 导出后会得到什么？

每本书会有 4 个文件：

- `<书名>.md`
- `<书名>.epub`
- `<书名>.pdf`
- `核验报告.txt`

核验报告会帮你检查是否存在缺失章节、重复内容或丢失图片。

## 使用前请注意

- 你的微信读书账号必须能正常阅读这本书。
- 第一次使用会弹出浏览器，需要用微信扫码登录。
- 导出一本书通常需要 10–15 分钟，书越长等待时间越长。
- 导出时不要关闭自动打开的浏览器。
- 一次只导出一本，不要同时运行多个导出任务。

## Windows 安装（推荐新手按这里操作）

### 第 1 步：准备 Python

安装 [Python 3.10 或更高版本](https://www.python.org/downloads/windows/)。

安装 Python 时，请勾选 **Add Python to PATH**。

安装完成后打开 PowerShell，输入：

```powershell
py -3 --version
```

如果能看到 Python 版本号，就可以继续。

### 第 2 步：把 Skill 放进 Thincoder

1. 点击 GitHub 页面右上方的 **Code**。
2. 点击 **Download ZIP**。
3. 解压下载的文件。
4. 把文件夹改名为 `weread-export`。
5. 把它放到你的项目目录：

```text
<你的项目>\.thincoder\skills\weread-export
```

放好后，请确认下面这个文件真实存在：

```text
<你的项目>\.thincoder\skills\weread-export\SKILL.md
```

### 第 3 步：安装必要组件

打开 `weread-export` 文件夹。在文件夹空白处点击右键，选择 **在终端中打开**，然后依次复制这 3 条命令：

```powershell
py -3 -m pip install -r "scripts\requirements.txt"
py -3 -m playwright install chromium
winget install --source winget --exact --id JohnMacFarlane.Pandoc
```

每条命令执行完再执行下一条。安装完成后，重新打开 Thincoder。

## 开始导出

1. 在微信读书中打开想导出的书。
2. 复制这本书的链接。
3. 对 Thincoder Agent 说：

> 用 weread-export 把这本书导出：<把链接粘贴在这里>

第一次运行时，按弹出浏览器的提示扫码登录。之后等 Agent 完成即可。

导出成功后，Agent 会告诉你 4 个文件保存在哪里。

## 常见问题

### PowerShell 提示“找不到 py”

Python 没有正确安装。重新安装 Python，并确认勾选了 **Add Python to PATH**。

### PowerShell 提示“找不到 winget”

可以从 [Pandoc 官方下载页](https://github.com/jgm/pandoc/releases/latest) 下载 Windows 安装包。安装完后重新打开 PowerShell 和 Thincoder。

### 提示“找不到 chromium”

在 `weread-export` 文件夹里重新运行：

```powershell
py -3 -m playwright install chromium
```

### 需要重新扫码

登录状态可能已失效。在弹出的浏览器中重新扫码，然后让 Agent 重试。

### 导出中途中断了

不需要从头开始。再次把同一个链接发给 Agent，Skill 会尝试从已完成的章节继续。

### 页面显示“去 App 阅读”

这是出版方限制，说明这本书无法通过微信读书网页版导出。

### Windows 显示中文乱码

在 PowerShell 中先运行：

```powershell
$env:PYTHONUTF8 = "1"
```

然后重试之前的命令。

<details>
<summary><strong>macOS / Linux 安装方法</strong></summary>

在 Thincoder 项目根目录执行：

```bash
mkdir -p .thincoder/skills
git clone https://github.com/YYuCChen/weread-export-skill.git \
  .thincoder/skills/weread-export
cd .thincoder/skills/weread-export
python3 -m pip install -r scripts/requirements.txt
python3 -m playwright install chromium
```

再安装 Pandoc：

```bash
brew install pandoc          # macOS
sudo apt install pandoc      # Ubuntu / Debian
```

</details>

<details>
<summary><strong>会使用 Git？可以用命令安装</strong></summary>

Windows PowerShell：

```powershell
New-Item -ItemType Directory -Force ".thincoder\skills" | Out-Null
git clone https://github.com/YYuCChen/weread-export-skill.git `
  ".thincoder\skills\weread-export"
```

如果想让所有 Thincoder 项目都能使用这个 Skill，可将安装路径换成：

```text
$HOME\.thincoder\skills\weread-export
```

</details>

<details>
<summary><strong>开发者信息</strong></summary>

- `SKILL.md`：Thincoder Agent 使用的操作说明。
- `scripts/`：导出、图片下载、核验和格式转换脚本。
- `references/playbook.md`：技术原理和故障排查记录。

运行测试：

```bash
python3 -m pip install -r scripts/requirements.txt
python3 -m pytest -q scripts/tests
```

GitHub Actions 会在 Windows 和 Ubuntu 上运行全部回归测试。

</details>

## 使用范围

本 Skill 只导出你的账号已经获得阅读权限的书，不会绕过验证码、会员限制或付费限制。

导出文件仅供个人学习和研究使用，请勿用于商业用途或大规模传播。

本项目参考了 [`lbq110/weread-exporter`](https://github.com/lbq110/weread-exporter)，并已获上游作者授权公开发布。
