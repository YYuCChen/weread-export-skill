#!/usr/bin/env python3
"""会话循环（run_session）：逐页推进与切章、续传、进度输出、受阻判定。

依赖：common / chapter / text / capture / nav。受阻判定：
- 会话入口 URL 含 login（扫码超时）→ 返回 SIGNAL_NEED_LOGIN；
- 页面文本命中受限词表 → 返回 SIGNAL_BLOCKED；
- 正常结束 → 信号为空串，reached_end 表示是否到书末。
"""
from playwright.async_api import async_playwright

from weread_capture import CANVAS_HOOK, capture_full_chapter
from weread_chapter import load_heading_titles, normalize_title
from weread_common import (SIGNAL_BLOCKED, SIGNAL_NEED_LOGIN, THROTTLE, _sleep,
                           classify_login_url, classify_page_text, profile_dir)
from weread_nav import (_title, advance_reader, fetch_book_title,
                        goto_first_chapter, load_last_catalog_title,
                        wait_stable)
from weread_text import save_chapter, text_grams


async def run_session(book_id, md_dir, raw_dir, start_idx, seen_imgs,
                      goto_first=False, catalog_path=None):
    """跑一轮抓取；返回 (书名, 作者, 本轮章数, 本轮字数, 末章号, reached_end, 阻断信号)。"""
    reached_end = False
    last_cat_title = load_last_catalog_title(catalog_path) if catalog_path else ""
    headings = load_heading_titles(catalog_path) if catalog_path else set()
    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            profile_dir(), headless=False, viewport={"width": 1200, "height": 900},
            args=["--disable-blink-features=AutomationControlled"])

        login_page = await ctx.new_page()
        await login_page.goto("https://weread.qq.com/web/shelf", timeout=30000)
        await _sleep(3)
        if classify_login_url(login_page.url):
            print("\n  ⚠️  请扫码登录微信读书")
            for _ in range(120):
                await _sleep(5)
                if not classify_login_url(login_page.url):
                    print("  ✅ 登录成功"); break
            else:
                await login_page.close(); await ctx.close()
                return "", "", 0, 0, start_idx, False, SIGNAL_NEED_LOGIN
        else:
            print("  ✅ 已登录")
        await login_page.close()

        page = await ctx.new_page()
        await page.add_init_script(CANVAS_HOOK)
        print("\n  打开阅读器...")
        await page.goto(f"https://weread.qq.com/web/reader/{book_id}",
                        wait_until="networkidle", timeout=30000)
        await _sleep(5)

        if classify_login_url(page.url):
            await page.close(); await ctx.close()
            return "", "", 0, 0, start_idx, False, SIGNAL_NEED_LOGIN
        page_text = await page.evaluate("() => document.body.innerText || ''")
        if classify_page_text(page_text):
            await page.close(); await ctx.close()
            return "", "", 0, 0, start_idx, False, SIGNAL_BLOCKED

        book_title, book_author = await fetch_book_title(page)
        if goto_first:
            await goto_first_chapter(page, catalog_path)
            last_cat_title = load_last_catalog_title(catalog_path)
            headings = load_heading_titles(catalog_path)

        await _sleep(0.5)

        current_chapter = await _title(page)
        print(f"  📖 {book_title} — {book_author}")
        print(f"  会话开始:「{current_chapter}」\n")

        ch_idx = start_idx
        ch_blocks = []
        seen_grams = set()  # 本章已见内容的 3-gram，用于识别整页重放/交织页
        total_chars = total_imgs = 0
        chapters_this_session = 0
        stale = 0
        page_num = 0

        # 页面级抓取用模块级 capture_current_page / capture_full_chapter

        def save_and_print(blocks, tag=""):
            """保存一章并打印进度"""
            nonlocal total_chars, total_imgs, chapters_this_session
            n, imgs = save_chapter(current_chapter, blocks, ch_idx, md_dir, raw_dir,
                                   headings)
            total_chars += n
            total_imgs += len(imgs)
            note = f" +{len(imgs)}图" if imgs else ""
            print(f"  [{ch_idx:4d}] {current_chapter[:32]:32s} {n:6d}字 "
                  f"({page_num}页){note}{tag}")
            if n or imgs:
                chapters_this_session += 1
            return n, imgs

        def chapter_grams(blocks):
            grams = set()
            for b in blocks:
                if b["type"] == "text":
                    grams.update(text_grams(b["text"]))
            return grams

        # 首页
        await page.evaluate("() => window.__wr_reset()")
        await wait_stable(page, 0)
        await capture_full_chapter(page, ch_blocks, seen_imgs, seen_grams, headings)

        while True:
            await page.evaluate("() => window.__wr_reset()")
            if not await advance_reader(page):
                reached_end = True
                break
            await _sleep(THROTTLE["page_settle_after_turn"])
            await wait_stable(page, 0)

            new_chapter = await _title(page)
            # 标题页切章后，顶栏稍后才会翻到同名标题（还可能只差空格，如 "AI OS" vs "AIOS"），
            # 归一化比较：相等则继续同一章，不再开新章
            if (new_chapter
                    and normalize_title(new_chapter) != normalize_title(current_chapter)):
                # 章节切换：保存上一章
                save_and_print(ch_blocks)
                ch_idx += 1
                current_chapter = new_chapter
                ch_blocks = []
                seen_grams = set()
                page_num = 0
                stale = 0
                await capture_full_chapter(page, ch_blocks, seen_imgs, seen_grams,
                                           headings)
                continue

            got_new, title_hit, page_start = await capture_full_chapter(
                page, ch_blocks, seen_imgs, seen_grams, headings)

            if (title_hit
                    and normalize_title(title_hit) != normalize_title(current_chapter)):
                # 标题页出现 = 新章节从这里开始（顶栏标题此时往往还没更新）
                if page_start:
                    save_and_print(ch_blocks[:page_start])
                    ch_idx += 1
                current_chapter = title_hit
                ch_blocks = ch_blocks[page_start:]
                seen_grams = chapter_grams(ch_blocks)
                page_num = 1
                stale = 0
                continue

            if not got_new:
                stale += 1
                if stale >= 10:
                    tag = ""
                    if (last_cat_title
                            and normalize_title(current_chapter) == normalize_title(last_cat_title)):
                        reached_end = True
                        tag = " [全书末尾]"
                    save_and_print(ch_blocks, tag)
                    break
            else:
                stale = 0
            page_num += 1

        # reached_end 时把最后一章存下
        if reached_end and ch_blocks:
            n, imgs = save_chapter(current_chapter, ch_blocks, ch_idx, md_dir, raw_dir,
                                   headings)
            if n or imgs:
                total_chars += n; total_imgs += len(imgs)
                print(f"  [{ch_idx:4d}] {current_chapter[:32]:32s} {n:6d}字 [末章]")
                chapters_this_session += 1

        await page.close(); await ctx.close()
        return (book_title, book_author, chapters_this_session, total_chars,
                ch_idx, reached_end, "")
