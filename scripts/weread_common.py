#!/usr/bin/env python3
"""weread-export 共享基础设施：状态根 / book_id / 退出码 / 运行日志 / 节流单点 / 受阻信号分类。

本模块是全包的单一来源：
- 状态与登录态一律落 `WEREAD_EXPORT_HOME`（默认 `~/.weread-export/`）；
- 节流参数只在本文件的 `THROTTLE` 定义一次（默认值 = 上游现值，不加严）；
- 受限信号词表与分类函数只在这里定义；等待一律经 `_sleep`。
"""
import asyncio
import datetime
import os
import re

# ---- 退出码契约（全脚本共用；设计档 §2.5）----
EXIT_OK = 0             # 成功 / 达标
EXIT_USAGE = 1          # 用法错误 / 未分类异常（下载部分失败同码）
EXIT_NEED_LOGIN = 2     # 需要登录（未登录 / 登录失效 / 扫码超时）
EXIT_BLOCKED = 3        # 访问受限（验证码 / 频繁访问 / 需去 App 阅读 / 会员墙）
EXIT_STRUCTURE = 4      # 结构信号（翻页控件缺失 / 渲染通道全空 / 基线接口失败 / 未达书末）
EXIT_VERIFY_FAIL = 5    # 核验未达标（含基线缺失）
EXIT_MISSING_INPUT = 6  # 输入缺失（产物目录不存在）
EXIT_BUILD_FAIL = 7     # 构建失败（pandoc 缺失 / 三格式断言失败）

# ---- 节流单点（唯一处；默认值 = 上游现值，不得加严）----
THROTTLE = {
    "page_settle_after_turn": 1.0,
    "stable_poll": 0.5,
    "stable_timeout": 8.0,
    "page_capture_settle": 0.3,
    "dom_scroll_settle": 0.2,
    "repaint_settle": 0.5,
    "session_restart_wait": 3.0,
    "download_workers": 8,
    "download_retries": 3,
    "download_timeout": 20,
}

_scale_warned = False


def sleep_scale() -> float:
    """`WEREAD_SLEEP_SCALE` 乘子；默认 1.0（仅用户确认加速后调低，见停询触发点②）。"""
    try:
        scale = float(os.environ.get("WEREAD_SLEEP_SCALE") or 1.0)
    except ValueError:
        return 1.0
    return scale if scale > 0 else 1.0


def warn_scale_once() -> None:
    """scale ≠ 1 时打一次醒目横幅（启动警告；放行加速但必须可见）。"""
    global _scale_warned
    scale = sleep_scale()
    if _scale_warned or scale == 1.0:
        return
    _scale_warned = True
    print("=" * 60)
    print(f"  ⚠️  警告：WEREAD_SLEEP_SCALE={scale:g}（≠1）")
    print("  节流已被调整：低于 1.0 = 加快 = 账号风控风险升高。")
    print("  仅在用户明确确认加速后使用；默认 1.0 不得偏离。")
    print("=" * 60)


async def _sleep(seconds: float) -> None:
    """全包唯一等待入口：晚绑定 `asyncio.sleep`（monkeypatch 可拦截）+ 乘子。"""
    warn_scale_once()
    await asyncio.sleep(seconds * sleep_scale())


# ---- 状态根（状态与登录态统一；项目目录零污染）----
def state_root() -> str:
    return os.environ.get("WEREAD_EXPORT_HOME") or os.path.join(
        os.path.expanduser("~"), ".weread-export")


def profile_dir() -> str:
    return os.path.join(state_root(), "profile")


def backup_profile() -> str:
    """把旧登录态改名备份，返回备份路径；不存在旧登录态时返回空串。

    不直接删除 profile，避免用户误操作后无法恢复。时间戳相同的极端情况
    会自动追加序号；书籍、运行日志和续传进度均不受影响。
    """
    source = profile_dir()
    if not os.path.exists(source):
        return ""
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    target = os.path.join(state_root(), f"profile.backup-{stamp}")
    suffix = 1
    while os.path.exists(target):
        target = os.path.join(state_root(), f"profile.backup-{stamp}-{suffix}")
        suffix += 1
    os.replace(source, target)
    return target


def book_dir(book_id: str) -> str:
    return os.path.join(state_root(), "books", book_id)


def runs_log() -> str:
    return os.path.join(state_root(), "runs.log")


def log_run(event: str, book_id: str, note: str = "") -> None:
    """向 runs.log 追加一行：ISO8601<TAB>event<TAB>book_id<TAB>note（单日计数依据）。"""
    os.makedirs(state_root(), exist_ok=True)
    stamp = datetime.datetime.now().isoformat(timespec="seconds")
    with open(runs_log(), "a", encoding="utf-8") as f:
        f.write(f"{stamp}\t{event}\t{book_id}\t{note}\n")


# ---- book_id 解析（章节令牌后缀剥离）----
BASE_ID_RE = re.compile(r"^[0-9a-f]{17}g[0-9a-f]{6}")


def parse_book_id(raw: str) -> str:
    """从 URL / 原始参数解析稳定的基础 book_id。

    实测样本形态 = 24 字符、第 18 位为 g（如 dcc32bc0813abbeeag013b75）；
    章节切换后 URL 末段 = 基础 ID + 动态令牌后缀，必须截回稳定前缀
    （踩坑经验条目 1）。不匹配时回退整段原样（零回归兜底）。
    """
    raw = (raw or "").strip().rstrip("/")
    if not raw:
        return raw
    if "weread.qq.com" in raw or raw.startswith("http"):
        raw = raw.split("/")[-1]
    match = BASE_ID_RE.match(raw)
    return match.group(0) if match else raw


# ---- 受阻信号分类（词表单点；供会话分支与测试夹具直测）----
RESTRICTED_PHRASES = (
    "去 App 阅读",
    "去App阅读",
    "验证码",
    "安全验证",
    "访问过于频繁",
)

SIGNAL_NEED_LOGIN = "need-login"
SIGNAL_BLOCKED = "blocked"

# 信号 →（标记行, 退出码）；门面据此映射 CLI 退出码（设计档 §2.5）
SIGNAL_MARKERS = {
    SIGNAL_NEED_LOGIN: ("⛔ 登录失效", EXIT_NEED_LOGIN),
    SIGNAL_BLOCKED: ("⛔ 访问受限", EXIT_BLOCKED),
}


def classify_login_url(url: str) -> bool:
    """登录失效判定：URL 含 login（shelf / reader 任一入口）。"""
    return "login" in (url or "").lower()


def classify_page_text(text: str) -> bool:
    """受限页判定：正文文本命中受限词表（去 App 阅读 / 验证码 / 频繁访问 等）。"""
    return any(phrase in (text or "") for phrase in RESTRICTED_PHRASES)


# ---- 文件名消毒（合并稿与三格式输出同规则）----
_FILENAME_BAD_RE = re.compile(r'[<>:"/\\|?*]')
_WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def safe_filename(name: str) -> str:
    """书名 → 跨平台文件名：处理 Windows 保留字符、尾随点/空格与设备名。"""
    cleaned = _FILENAME_BAD_RE.sub("_", name).rstrip(" .") or "_"
    stem = cleaned.split(".", 1)[0].upper()
    return f"_{cleaned}" if stem in _WINDOWS_RESERVED_NAMES else cleaned
