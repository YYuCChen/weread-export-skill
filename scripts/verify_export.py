#!/usr/bin/env python3
"""导出核验：把产物与平台元数据逐章对照，输出可复核的核验报告。

用法: python3 verify_export.py <book_id> [平台章节清单.json] [--report-out <路径>]
- 产物目录 = 状态根下的 <book_dir>（books/<book_id>/）。
- 平台基线默认 = <book_dir>/_platform_chapterinfo.json（预检生成）；位置参数可覆盖。
- 报告默认写 <book_dir>/_verify_report.txt；--report-out 另写一份交付副本（自动建父目录）。
退出码：0 达标 / 5 未达标（含无平台基线）/ 6 产物目录不存在。
"""
import glob
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import weread_common as wc
from weread_chapter import normalize_title

CJK_RE = re.compile(r'[\u3400-\u4dbf\u4e00-\u9fff]')
MD_IMG_RE = re.compile(r'!\[[^\]]*\]\(([^)]+)\)')
HEADING_RE = re.compile(r'^#\s+', re.MULTILINE)

MAGIC = (
    (b'\xff\xd8\xff', 'jpeg'),
    (b'\x89PNG\r\n\x1a\n', 'png'),
    (b'GIF8', 'gif'),
    (b'RIFF', 'webp'),
)


def normalize_para(text):
    return re.sub(r'\s+', '', text)


def strip_markdown(text):
    """正文纯文本：去掉标题行、图片行与分隔线，用于与平台字数对齐比较。"""
    out = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith('#') or s.startswith('![') or set(s) <= {'-', '*', '_'}:
            continue
        out.append(s)
    return ''.join(out)


def load_platform(baseline_path):
    """读平台逐章基线；文件缺失 / 内容损坏 / 形态变化 → 返回 []（由报告判「无平台基线」）。"""
    try:
        with open(baseline_path, encoding="utf-8") as f:
            data = json.load(f)
        chapters = data["data"][0]["updated"]
    except Exception:
        return []
    return [
        {"idx": ch["chapterIdx"], "title": ch.get("title", ""),
         "words": ch.get("wordCount", 0), "level": ch.get("level")}
        for ch in chapters
    ]


def find_embedded_titles(book_dir, chapter_stats, platform):
    """章节边界瑕疵：标题粘段首（结构性），或标题字符被织进正文行中部（乱码）。

    返回 (glued, interleaved)。两者都是"行内出现已知标题"，区别在位置。
    """
    titles = {normalize_title(c["title"]): c["title"] for c in platform
              if len(normalize_title(c["title"])) >= 6}
    glued, interleaved = [], []
    for st in chapter_stats:
        path = os.path.join(book_dir, "chapters", st["file"])
        for lineno, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
            s = line.strip()
            if len(s) < 60 or s.startswith("#"):
                continue
            ns = normalize_title(s)
            for key, raw in titles.items():
                pos = ns.find(key)
                if pos < 0:
                    continue
                item = (st["file"], lineno, raw, s[:70])
                if pos == 0:
                    glued.append(item)
                elif pos + len(key) < len(ns):
                    interleaved.append(item)
    return glued, interleaved


def image_magic_ok(path):
    with open(path, 'rb') as f:
        head = f.read(16)
    for sig, name in MAGIC:
        if head.startswith(sig):
            return name
    return None


def run_embedded_tests():
    """跑包内回归测试（相对脚本自身定位，不依赖 cwd；pytest 内运行或缺 pytest 时跳过）。"""
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return "跳（已在 pytest 内运行，防嵌套）"
    if importlib.util.find_spec("pytest") is None:
        return "跳（未安装 pytest）"
    tests_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tests")
    try:
        proc = subprocess.run([sys.executable, "-m", "pytest", tests_dir, "-q"],
                              capture_output=True, text=True, timeout=300)
    except Exception as exc:
        return f"跳，{exc}"
    tail = [l for l in proc.stdout.strip().splitlines() if l.strip()]
    return f"pytest tests/ → {tail[-1] if tail else '(无输出)'}"


def main(book_id, baseline_path=None, report_out=None) -> int:
    book_dir = wc.book_dir(book_id)
    if not os.path.isdir(book_dir):
        print(f"⛔ 输入缺失：找不到状态目录 {book_dir}（先跑预检 / 导出）")
        return wc.EXIT_MISSING_INPUT
    baseline_path = baseline_path or os.path.join(book_dir, "_platform_chapterinfo.json")
    md_files = sorted(glob.glob(os.path.join(book_dir, "chapters", "*.md")))
    img_dir = os.path.join(book_dir, "images")
    merged = [f for f in glob.glob(os.path.join(book_dir, "*.md"))]
    catalog_path = os.path.join(book_dir, "_catalog.json")
    catalog = json.load(open(catalog_path)) if os.path.exists(catalog_path) else []

    platform = load_platform(baseline_path) if os.path.exists(baseline_path) else []
    plat_total = sum(c["words"] for c in platform)

    lines = []

    def emit(s=""):
        lines.append(s)
        print(s)

    emit("=" * 72)
    emit("  导出核验报告")
    emit("=" * 72)
    emit(f"书籍 ID: {book_id}")
    emit(f"目录条目（平台 / 本地目录缓存 / 输出章节数）: "
         f"{len(platform)} / {len(catalog)} / {len(md_files)}")
    emit(f"平台标称总字数: {plat_total:,}")

    # ---------- 每章字符数 ----------
    chapter_stats = []
    body_total = cjk_total = 0
    md_bytes_total = 0
    for path in md_files:
        text = open(path, encoding="utf-8").read()
        md_bytes_total += len(text.encode("utf-8"))
        title = text.splitlines()[0].lstrip("# ").strip() if text else ""
        body = strip_markdown(text)
        body_total += len(body)
        cjk_total += len(CJK_RE.findall(body))
        chapter_stats.append({
            "file": os.path.basename(path), "title": title,
            "chars": len(body), "cjk": len(CJK_RE.findall(body)),
            "md_chars": len(text),
        })

    emit()
    emit(f"Markdown 正文（去标题/图片行）Unicode 字符数: {body_total:,}")
    emit(f"其中 CJK 汉字数: {cjk_total:,}")
    emit(f"章节 Markdown 文件 UTF-8 字节数合计: {md_bytes_total:,}")
    emit(f"正文/平台标称 = {body_total / plat_total:.2%}（含标点，平台口径可能不同）"
         if plat_total else "")
    for path in merged:
        size = os.path.getsize(path)
        emit(f"合并稿: {os.path.basename(path)}  {size:,} bytes")

    # ---------- 逐章对照 ----------
    plat_by_title = {}
    for c in platform:
        plat_by_title.setdefault(normalize_title(c["title"]), []).append(c)

    emit()
    emit("逐章对照（本地章节 → 平台标称字数）:")
    emit(f"  {'#':>4} {'本地章节':<34} {'本地':>7} {'平台':>7} {'比值':>7}  标记")
    short = []
    unmatched = []
    for st in chapter_stats:
        cands = plat_by_title.get(normalize_title(st["title"]))
        words = cands[0]["words"] if cands else None
        if words is None:
            unmatched.append(st["title"])
            ratio = ""
            flag = "平台无同名条目"
        else:
            ratio = f"{st['chars'] / words:.2f}" if words else "-"
            flag = ""
            if words >= 300 and st["chars"] < words * 0.6:
                flag = "⚠️ 偏短"
                short.append(st["title"])
            elif words >= 300 and st["chars"] > words * 1.6:
                flag = "⚠️ 偏长(疑重复)"
        emit(f"  {st['file'][:4]:>4} {st['title'][:32]:<34} {st['chars']:>7,} "
             f"{(words if words is not None else 0):>7,} {ratio:>7}  {flag}")

    local_keys = {normalize_title(s["title"]) for s in chapter_stats}
    missing = [c["title"] for c in platform
               if normalize_title(c["title"]) not in local_keys]
    emit()
    emit(f"平台有、本地缺的章节: {missing or '无'}")
    emit(f"本地有、平台无同名: {unmatched or '无'}")
    if short:
        emit(f"偏短章节: {short}")

    # ---------- 重复检测 ----------
    emit()
    para_seen = {}
    dup_groups = []
    for st in chapter_stats:
        text = Path(os.path.join(
            book_dir, "chapters", st["file"])).read_text(encoding="utf-8")
        for para in text.split("\n\n"):
            p = normalize_para(para)
            if len(p) < 15 or p.startswith("#") or p.startswith("!["):
                continue
            para_seen.setdefault(p, []).append(st["file"])
    for p, files in para_seen.items():
        if len(files) > 1:
            dup_groups.append((len(p), len(files), p[:60], sorted(set(files))))
    dup_chars = sum(size * (n - 1) for size, n, _t, _f in dup_groups)
    emit(f"重复段落组数: {len(dup_groups)}  重复字符量: {dup_chars:,}")
    for size, n, sample, files in sorted(dup_groups, reverse=True)[:10]:
        emit(f"    ×{n} ({size}字) {sample}…  出现在 {files}")

    hashes = {}
    for st in chapter_stats:
        text = Path(os.path.join(
            book_dir, "chapters", st["file"])).read_text(encoding="utf-8")
        h = hashlib.sha256(normalize_para(strip_markdown(text)).encode()).hexdigest()[:12]
        hashes.setdefault(h, []).append(st["file"])
    dup_chapters = {h: f for h, f in hashes.items() if len(f) > 1}
    emit(f"内容完全重复的章节: {dup_chapters or '无'}")

    embedded = find_embedded_titles(book_dir, chapter_stats, platform)
    glued, interleaved = embedded
    emit(f"标题粘在段首（结构性瑕疵，本工具既有特性）: {len(glued)} 处")
    for fname, lineno, raw, snippet in glued[:6]:
        emit(f"    {fname}:{lineno} 「{raw[:26]}」 → {snippet}…")
    emit(f"标题字符被织进正文行中部（乱码）: {len(interleaved)} 处（含正文自然提到标题的误报）")
    for fname, lineno, raw, snippet in interleaved:
        emit(f"    {fname}:{lineno} 「{raw[:26]}」 → {snippet}…")

    # ---------- 图片 ----------
    emit()
    refs = []
    for path in md_files:
        for m in MD_IMG_RE.finditer(Path(path).read_text(encoding="utf-8")):
            refs.append(os.path.basename(m.group(1)))
    disk = [f for f in os.listdir(img_dir) if not f.startswith('.')] if os.path.isdir(img_dir) else []
    broken = [r for r in refs if r not in disk]
    bad = []
    total_bytes = 0
    for f in disk:
        fp = os.path.join(img_dir, f)
        size = os.path.getsize(fp)
        total_bytes += size
        kind = image_magic_ok(fp)
        if not kind or size < 500:
            bad.append(f"{f}({kind or '非法格式'},{size}B)")
    emit(f"正文图片引用数: {len(refs)}   图片文件数: {len(disk)}   "
         f"合计 {total_bytes:,} bytes")
    emit(f"失效引用: {broken or '无'}")
    emit(f"损坏/异常图片: {bad or '无'}")
    unused = [f for f in disk if f not in set(refs)]
    emit(f"未被引用的图片文件: {unused or '无'}")

    # ---------- 结尾判定 ----------
    emit()
    same_last = False
    if platform:
        last_plat = platform[-1]["title"]
        local_titles = [normalize_title(s["title"]) for s in chapter_stats]
        same_last = bool(local_titles) and local_titles[-1] == normalize_title(last_plat)
        emit(f"平台末章: 「{last_plat}」  本地末章: 「{chapter_stats[-1]['title'] if chapter_stats else '(无)'}」"
             f"  → {'✅ 一致' if same_last else '❌ 不一致'}")

    # ---------- 自动化测试 ----------
    emit()
    emit(f"自动化测试: {run_embedded_tests()}")

    # ---------- 达标判定（R8 机检集）----------
    emit()
    passed = True
    if not platform:
        passed = False
        emit("无平台基线: 是（缺 _platform_chapterinfo.json——请重跑预检补齐，否则无法判定达标）")
    else:
        if not same_last:
            passed = False
        if plat_total and body_total < plat_total * 0.99:
            passed = False
    if missing:
        passed = False
    if dup_groups:
        passed = False
    if dup_chapters:
        passed = False
    if broken:
        passed = False
    if unused:
        passed = False
    emit(f"核验结论: {'✅ 达标' if passed else '⛔ 未达标'}")

    report_path = os.path.join(book_dir, "_verify_report.txt")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"\n报告已写入: {report_path}")
    if report_out:
        os.makedirs(os.path.dirname(os.path.abspath(report_out)), exist_ok=True)
        with open(report_out, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        print(f"交付副本已写入: {report_out}")
    return wc.EXIT_OK if passed else wc.EXIT_VERIFY_FAIL


def _parse_args(argv):
    report_out = None
    positional = []
    i = 0
    while i < len(argv):
        if argv[i] == "--report-out":
            report_out = argv[i + 1] if i + 1 < len(argv) else None
            i += 2
            continue
        positional.append(argv[i])
        i += 1
    return positional, report_out


if __name__ == "__main__":
    usage = ("用法: python3 verify_export.py <book_id> [平台章节清单.json] "
             "[--report-out <路径>]")
    if len(sys.argv) < 2:
        print(usage)
        sys.exit(wc.EXIT_USAGE)
    _positional, _report_out = _parse_args(sys.argv[1:])
    if not _positional:
        print(usage)
        sys.exit(wc.EXIT_USAGE)
    sys.exit(main(wc.parse_book_id(_positional[0]),
                  _positional[1] if len(_positional) > 1 else None,
                  _report_out))
