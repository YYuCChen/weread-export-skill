#!/usr/bin/env python3
"""weread-export 门面：CLI 入口（main / __main__）+ 测试与验收引用名的显式再导出。

导出流程的拆分见设计档 §2.4.8——本文件只保留入口与再导出；引擎各段在
weread_common / weread_chapter / weread_text / weread_capture / weread_nav /
weread_session / weread_postprocess。等待一律经 `weread_common._sleep`
（晚绑定 asyncio.sleep —— 回归测试的 monkeypatch 目标）。
"""
import asyncio
import json
import os
import sys

import weread_common as wc
import weread_nav as nav
import weread_postprocess as postprocess
import weread_session as session_mod

# —— 测试与验收引用名的显式再导出（共 32 名，禁用 import *；清单见设计档 §2.4.8-D）——
from weread_capture import (CANVAS_BOTTOM_JS, CANVAS_HOOK, CANVAS_RECTS_JS,
                            POSITIONED_DOM_JS, VIEWPORT_IMGS_JS,
                            capture_current_page, capture_full_chapter,
                            force_repaint)
from weread_chapter import find_title_line, normalize_title, split_heading_prefix
from weread_common import THROTTLE
from weread_nav import (LEGACY_FOOTER_SELECTOR, NEXT_PAGE_SELECTORS, _title,
                        advance_reader, close_catalog_if_reader_hidden,
                        goto_first_chapter, wait_stable)
from weread_postprocess import (build_merged_md, move_trailing_headings,
                                stitch_chapter_boundaries)
from weread_text import (best_cover_url, build_page_blocks, chars_to_lines,
                         collapse_doubled_line, merge_positioned_chars,
                         render_chapter_md, save_chapter, split_spread_by_canvas,
                         text_already_seen, text_grams)


async def main(book_id: str) -> int:
    """导出主流程：会话循环 → 封面/后处理 → 图片下载 → 合并稿；返回退出码。"""
    print("=" * 60)
    print("  weread-exporter — 精确图文导出 v3（weread-export skill）")
    print("=" * 60)
    wc.log_run("start", book_id, "")
    os.makedirs(wc.profile_dir(), exist_ok=True)
    book_dir = wc.book_dir(book_id)
    md_dir = os.path.join(book_dir, "chapters")
    raw_dir = os.path.join(book_dir, "raw")
    img_dir = os.path.join(book_dir, "images")
    for d in (md_dir, raw_dir, img_dir):
        os.makedirs(d, exist_ok=True)

    seen_imgs = set()
    for jf in os.listdir(raw_dir):
        if jf.endswith(".json"):
            with open(os.path.join(raw_dir, jf)) as f:
                for img in json.load(f).get("images", []):
                    seen_imgs.add(img["url"])

    catalog_path = os.path.join(book_dir, "_catalog.json")
    book_title = book_author = ""
    reached_end = False
    session = 0
    while True:
        session += 1
        last_title, last_idx = nav.get_last_chapter_title(md_dir)
        start_idx = last_idx + 1 if last_idx > 0 else 1
        print(f"\n--- 会话 {session} ---")
        print(f"  上次: {last_title or '(无)'}, 编号: {last_idx}")
        goto_first = (session == 1 and last_idx == 0)
        (title, author, added, chars_added, end_idx,
         reached_end, signal) = await session_mod.run_session(
            book_id, md_dir, raw_dir, start_idx, seen_imgs,
            goto_first=goto_first, catalog_path=catalog_path)
        if signal:
            marker, code = wc.SIGNAL_MARKERS[signal]
            print(f"\n  {marker}")
            wc.log_run("end", book_id, signal)
            return code
        if title:
            book_title = title
        if author:
            book_author = author
        print(f"\n  本次: +{added} 章, +{chars_added:,} 字")
        if reached_end:
            print("\n  ✅ 已到全书最后一章，导出完成。")
            break
        if added == 0:
            print("\n  无新章节，导出完成。")
            break
        print("  3 秒后自动重开继续...")
        await wc._sleep(wc.THROTTLE["session_restart_wait"])

    if not reached_end:
        print("\n  ⛔ 未检测到书末（导出可能不完整）")

    cover_url, cover_file = postprocess.download_cover(book_dir, img_dir)
    if cover_file:
        postprocess.write_cover_chapter(md_dir, raw_dir, cover_url, cover_file)
    stitched = postprocess.stitch_chapter_boundaries(md_dir)
    if stitched:
        print(f"  🔗 跨章断句已合拢 {len(stitched)} 处")
        for prev_name, next_name, moved in stitched:
            print(f"      {prev_name} ← {next_name}: {moved[:28]}")
    doubled = postprocess.collapse_doubled_lines(md_dir)
    if doubled:
        print(f"  🧹 行内重复折叠 {doubled} 处")
    headed = postprocess.move_trailing_headings(md_dir)
    if headed:
        print(f"  ↪  末行标题下移 {len(headed)} 处")
    postprocess.download_all_images(raw_dir, img_dir)

    total_files = sorted(f for f in os.listdir(md_dir) if f.endswith(".md"))
    img_count = len([f for f in os.listdir(img_dir) if not f.startswith(".")])
    if not book_title:
        book_title = book_id
    merged, _count = postprocess.build_merged_md(book_dir, md_dir, book_title, book_author)
    print(f"\n{'=' * 60}")
    print(f"  ✅ 全书导出完成!  📖 {book_title} — {book_author}")
    print(f"  📄 {len(total_files)} 章, {os.path.getsize(merged):,} bytes,  🖼 {img_count} 张图")
    print(f"  📦 {merged}")
    print(f"{'=' * 60}")
    if not reached_end:
        wc.log_run("end", book_id, "incomplete")
        return wc.EXIT_STRUCTURE
    wc.log_run("end", book_id, "ok")
    return wc.EXIT_OK


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python3 export_precise.py <book_url_or_id> | --postprocess <book_id>")
        sys.exit(wc.EXIT_USAGE)
    if sys.argv[1] == "--postprocess":
        if len(sys.argv) < 3:
            print("用法: python3 export_precise.py --postprocess <book_id>")
            sys.exit(wc.EXIT_USAGE)
        sys.exit(postprocess.postprocess_book(sys.argv[2].strip().rstrip("/")))
    _book_id = wc.parse_book_id(sys.argv[1])
    print(f"  Book ID: {_book_id}")
    sys.exit(asyncio.run(main(_book_id)))
