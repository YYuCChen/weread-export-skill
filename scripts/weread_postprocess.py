#!/usr/bin/env python3
"""后处理与收尾：跨章合拢 / 末行标题 / 双写折叠 / 封面章 / 合并稿 / 图片下载委托。

依赖：text（折叠与封面变体）、common（状态根与文件名消毒）、download_images
（并发下载的单一实现——本模块只做委托与封面下载）。
"""
import glob
import json
import os
import re
import urllib.request

import download_images
import weread_common as wc
from weread_text import best_cover_url, collapse_doubled_line


def collapse_doubled_lines(md_dir):
    """后处理：把章节目录里“同一段文字重复两遍”的行折叠回一遍。返回修复处数。"""
    fixed = 0
    for path in sorted(glob.glob(os.path.join(md_dir, "*.md"))):
        with open(path, encoding="utf-8") as f:
            lines = f.read().split("\n")
        changed = False
        for i, line in enumerate(lines):
            collapsed = collapse_doubled_line(line.strip())
            if collapsed != line.strip():
                lines[i] = collapsed
                changed = True
                fixed += 1
        if changed:
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines).rstrip("\n") + "\n")
    return fixed


STRONG_END = set("。！？…")
CLOSERS = set("”』」）】》")


def _last_strong_end(text):
    """最后一个句末标点之后的下标（即未完成残句的起点）。"""
    for i in range(len(text) - 1, -1, -1):
        if text[i] in STRONG_END:
            return i + 1
    return 0


def _first_strong_end(text, limit):
    """第一个句末标点（含其后紧跟的引号/括号）之后的下标；没有则 0。"""
    for i, ch in enumerate(text[:limit]):
        if ch in STRONG_END:
            j = i + 1
            while j < len(text) and text[j] in CLOSERS:
                j += 1
            return j
    return 0


def stitch_chapter_boundaries(md_dir, max_fragment=60, max_sentence=60):
    """把跨章被切开的句子合回上一章。

    按页抓取时句子会跨章被切开：上一章结尾剩半句，下一章开头是后半句。
    保守判定（宁可不改，不可错搬）：
      * 末段不是标题行；残句起点不能是标点（避免把溢出的“：小标题”当成残句）
      * 末段整段无句末标点时，仅当它确实像句子（带逗号且≥10 字）才处理
      * 残句长度 2~40 字；下一章首段第一句 ≤60 字
      * 把首段整段搬走时，下一章必须还有其它正文（不把章节掏空）
    返回 [(上一章, 下一章, 搬回的句子), ...]
    """
    files = sorted(glob.glob(os.path.join(md_dir, "*.md")))
    fixed = []
    for prev_path, next_path in zip(files, files[1:]):
        with open(prev_path, encoding="utf-8") as f:
            prev_text = f.read()
        with open(next_path, encoding="utf-8") as f:
            next_text = f.read()
        prev_paras = [p for p in prev_text.strip().split("\n\n") if p.strip()]
        lines = next_text.split("\n")
        if not prev_paras or not lines:
            continue
        tail = prev_paras[-1].strip()
        if tail.startswith(("#", "**", "![")):
            continue
        start = _last_strong_end(tail)
        if start:
            fragment = tail[start:]
            if fragment[:1] in STRONG_END or fragment[:1] in CLOSERS \
                    or fragment[:1] in "：，、；！？。":
                continue
        else:
            # 整段都没有句末标点：带标题特征（：）或太短的，多半是溢出的标题，不动
            if len(tail) < 10 or "：" in tail:
                continue
            fragment = tail
        if not (2 <= len(fragment) <= max_fragment):
            continue
        head_idx = None
        for i, line in enumerate(lines):
            if line.strip() and not line.startswith(("#", "**", "![")):
                head_idx = i
                break
        if head_idx is None:
            continue
        head = lines[head_idx].strip()
        move_len = _first_strong_end(head, max_sentence)
        if not move_len:
            continue
        moved, rest = head[:move_len], head[move_len:].strip()
        if rest:
            lines[head_idx] = rest
        else:
            # 首段整段都是续句：删掉它，但下一章必须还有其它正文
            lines[head_idx] = ""
            others = [l for l in lines
                      if l.strip() and not l.startswith(("#", "**", "!["))]
            if not others:
                continue
        prev_paras[-1] = tail + moved
        with open(prev_path, "w", encoding="utf-8") as f:
            f.write("\n\n".join(prev_paras) + "\n")
        new_next = "\n".join(lines)
        # 折叠多余空行（首段被整段搬走时会留下空行）
        new_next = re.sub(r"\n{3,}", "\n\n", new_next).strip("\n") + "\n"
        with open(next_path, "w", encoding="utf-8") as f:
            f.write(new_next)
        fixed.append((os.path.basename(prev_path), os.path.basename(next_path), moved))
    return fixed


def write_cover_chapter(md_dir, raw_dir, cover_url, cover_file):
    """封面页在网页端是 DOM 简介页，正文通道取不到，单独补一章保证完整性。"""
    with open(os.path.join(md_dir, "0000.md"), "w") as f:
        f.write(f"# 封面\n\n![封面](images/{cover_file})\n")
    with open(os.path.join(raw_dir, "0000.json"), "w") as f:
        json.dump({"title": "封面", "text_len": 0,
                   "images": [{"url": cover_url, "file": cover_file}]},
                  f, ensure_ascii=False)


def move_trailing_headings(md_dir, max_len=30):
    """把“上一章末尾溢出的小标题”移到下一章开头（它本来是下一节的标题）。

    典型情形：小节标题画在页尾，落在上一章文件里，而它下面的正文在下一章。
    仅当该行确实像标题（短、无句末标点、无逗号）且下一章首段是正常句子时才动。
    """
    files = sorted(glob.glob(os.path.join(md_dir, "*.md")))
    moved = []
    for prev_path, next_path in zip(files, files[1:]):
        with open(prev_path, encoding="utf-8") as f:
            prev_paras = [p for p in f.read().strip().split("\n\n") if p.strip()]
        with open(next_path, encoding="utf-8") as f:
            lines = f.read().split("\n")
        if not prev_paras or not lines:
            continue
        tail = prev_paras[-1].strip()
        if tail.startswith(("#", "**", "![")) or not (4 <= len(tail) <= max_len):
            continue
        if any(ch in tail for ch in "。！？…，、；"):
            continue
        head_idx = None
        for i, line in enumerate(lines):
            if line.strip() and not line.startswith(("#", "**", "![")):
                head_idx = i
                break
        if head_idx is None:
            continue
        head = lines[head_idx].strip()
        if len(head) < 30 or "。" not in head:
            continue
        prev_paras.pop()
        lines.insert(head_idx, tail)
        lines.insert(head_idx + 1, "")
        with open(prev_path, "w", encoding="utf-8") as f:
            f.write("\n\n".join(prev_paras).rstrip("\n") + "\n")
        with open(next_path, "w", encoding="utf-8") as f:
            f.write(re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip("\n") + "\n")
        moved.append((os.path.basename(prev_path), os.path.basename(next_path), tail))
    return moved


def build_merged_md(book_dir, md_dir, book_title, book_author):
    """把章节目录合并成一本书的 Markdown（放在图片同级目录，保证相对路径可用）。"""
    total_files = sorted(f for f in os.listdir(md_dir) if f.endswith(".md"))
    safe = wc.safe_filename(book_title)
    merged = os.path.join(book_dir, f"{safe}.md")
    with open(merged, "w") as out:
        out.write(f"# {book_title}\n\n**{book_author}**\n\n---\n\n")
        for fn in total_files:
            out.write(open(os.path.join(md_dir, fn)).read())
            out.write("\n\n---\n\n")
    return merged, len(total_files)


def download_all_images(raw_dir, img_dir):
    """下载 raw/ 名单里的全部图片（委托 download_images.download_all，8 线程单一实现）。"""
    stats = download_images.download_all(raw_dir, img_dir)
    return stats["ok"]


def download_cover(book_dir, img_dir):
    """下载封面（优先高清变体 t9_，退回 DOM 给的变体），返回 (url, 文件名)。"""
    try:
        with open(os.path.join(book_dir, "_meta.json")) as f:
            cover_url = json.load(f).get("cover", "")
    except Exception:
        cover_url = ""
    if not cover_url:
        return "", ""
    dest = os.path.join(img_dir, "cover.jpg")
    with download_images.ipv4_only():
        for url in dict.fromkeys([best_cover_url(cover_url), cover_url]):
            try:
                req = urllib.request.Request(url, headers=download_images.HEADERS)
                data = urllib.request.urlopen(
                    req, timeout=wc.THROTTLE["download_timeout"]).read()
            except Exception as e:
                print(f"    ⚠️  封面下载失败: {e}")
                continue
            if len(data) > 2000:
                with open(dest, "wb") as f:
                    f.write(data)
                print(f"  🖼 封面已下载: cover.jpg ({len(data):,} bytes)")
                return url, "cover.jpg"
    return "", ""


def postprocess_book(book_id) -> int:
    """对已抓取的产物重跑后处理（跨章合拢、行内重复折叠）并重建合并稿。"""
    book_dir = wc.book_dir(book_id)
    if not os.path.isdir(book_dir):
        print(f"⛔ 输入缺失：找不到状态目录 {book_dir}（先跑预检 / 导出）")
        return wc.EXIT_MISSING_INPUT
    md_dir = os.path.join(book_dir, "chapters")
    existing = [f for f in os.listdir(book_dir) if f.endswith(".md")]
    if not existing:
        print("找不到合并稿，无法确定书名")
        return wc.EXIT_MISSING_INPUT
    with open(os.path.join(book_dir, existing[0]), encoding="utf-8") as f:
        head = f.read().splitlines()
    book_title = head[0].lstrip("# ").strip() if head else book_id
    book_author = head[2].strip("* ").strip() if len(head) > 2 else ""
    stitched = stitch_chapter_boundaries(md_dir)
    doubled = collapse_doubled_lines(md_dir)
    headed = move_trailing_headings(md_dir)
    print(f"  跨章合拢 {len(stitched)} 处，行内重复折叠 {doubled} 处，末行标题下移 {len(headed)} 处")
    for prev_name, next_name, moved_text in stitched:
        print(f"      {prev_name} ← {next_name}: {moved_text[:28]}")
    merged, count = build_merged_md(book_dir, md_dir, book_title, book_author)
    print(f"  📄 {count} 章, {os.path.getsize(merged):,} bytes → {merged}")
    return wc.EXIT_OK
