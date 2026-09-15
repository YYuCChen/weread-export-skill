#!/usr/bin/env python3
"""阅读器导航：选择器 / 稳定等待 / 翻页 / 目录跳转 / 标题读取 / 续传锚点。

依赖：common（节流）、chapter（目录标题清洗）。等待一律经 `_sleep`。
选择器只在本模块定义一次（页面结构变化时的唯一改动点）。
"""
import json
import os

from playwright.async_api import Error as PlaywrightError

from weread_chapter import clean_catalog_title
from weread_common import THROTTLE, _sleep

# 新版网页阅读器把翻页控件换成了 button.renderTarget_pager_button（“下一页”）；
# 旧版是 button.readerFooter_button。只点显式翻页按钮，绝不用点击中央/方向键。
NEXT_PAGE_SELECTORS = (
    "button.renderTarget_pager_button:has-text('下一页')",
    "button.readerFooter_button:has-text('下一页')",
)
READER_PAGER_SELECTOR = ("button.renderTarget_pager_button, "
                         "button.readerFooter_button")
LEGACY_FOOTER_SELECTOR = "button.readerFooter_button"


async def wait_stable(page, prev_count, timeout=THROTTLE["stable_timeout"]):
    """等页面渲染稳定，返回稳定后的字符数"""
    last = -1
    for _ in range(int(timeout / THROTTLE["stable_poll"])):
        try:
            c = await page.evaluate("() => window.__wr_count()")
        except PlaywrightError as exc:
            if "Execution context was destroyed" not in str(exc):
                raise
            await _sleep(THROTTLE["stable_poll"])
            continue
        if c == last:
            return c
        last = c
        await _sleep(THROTTLE["stable_poll"])
    return last


async def advance_reader(page) -> bool:
    """Advance through the reader's explicit next-page control."""
    for selector in NEXT_PAGE_SELECTORS:
        button = page.locator(selector)
        if await button.count() > 0:
            try:
                await button.first.click(timeout=10_000)
                return True
            except PlaywrightError:
                continue
    # 兼容旧版底栏（按钮无文本时取最右一个，即“下一页”）
    legacy = page.locator(LEGACY_FOOTER_SELECTOR)
    if await legacy.count() > 0:
        try:
            await legacy.last.click(timeout=10_000)
            return True
        except PlaywrightError:
            return False
    return False


async def close_catalog_if_reader_hidden(page) -> bool:
    """Close a catalog that stayed open after selecting its current item."""
    if await page.locator(READER_PAGER_SELECTOR).count() > 0:
        return False
    catalog = page.locator("button.readerControls_item.catalog")
    if await catalog.count() == 0:
        return False
    await catalog.click(timeout=10_000)
    await _sleep(0.5)
    return True


def get_last_chapter_title(md_dir):
    if not os.path.exists(md_dir):
        return None, 0
    files = sorted(f for f in os.listdir(md_dir) if f.endswith(".md"))
    if not files:
        return None, 0
    idx = int(files[-1].replace(".md", ""))
    with open(os.path.join(md_dir, files[-1])) as f:
        title = f.readline().strip().replace("# ", "")
    return title, idx


def load_last_catalog_title(catalog_path):
    try:
        with open(catalog_path) as f:
            titles = json.load(f)
        return clean_catalog_title(titles[-1]) if titles else ""
    except Exception:
        return ""


async def _title(page):
    return await page.evaluate(
        "() => document.querySelector('.readerTopBar_title_chapter, .renderTargetPageInfo_header_chapterTitle')?.textContent?.trim() || ''")


async def fetch_book_title(page):
    info = await page.evaluate("""() => {
        const title = document.querySelector('.readerCatalog_bookInfo_title_txt, .bookInfo_right_header_title')
            ?.textContent?.trim() || document.title.replace(/-.*$/, '').trim();
        const author = document.querySelector('.readerCatalog_bookInfo_author, .bookInfo_author a')
            ?.textContent?.trim() || '';
        return {title, author};
    }""")
    return info.get("title", "未知"), info.get("author", "")


async def goto_first_chapter(page, catalog_path=None):
    first_title = ""
    try:
        await page.click("button.readerControls_item.catalog", timeout=5000)
        await _sleep(1.5)
        titles = await page.evaluate("""() => Array.from(
            document.querySelectorAll('.readerCatalog_list_item')).map(el => el.textContent.trim())""")
        if titles and catalog_path:
            with open(catalog_path, "w") as f:
                json.dump(titles, f, ensure_ascii=False)
        await page.evaluate("""() => {
            const sc = document.querySelector('.readerCatalog_list_scroll_area, [class*="readerCatalog_list_scroll"]');
            if (sc) sc.scrollTop = 0;
        }""")
        await _sleep(1)
        item = page.locator(".readerCatalog_list_item").first
        first_title = (await item.text_content() or "").strip()
        await item.click(timeout=4000)
        await _sleep(3)
        if catalog_path:
            cover = await page.evaluate("""() => {
                const img = document.querySelector('.wr_bookCover_img, [class*="bookCover"] img');
                return img ? (img.src || '') : '';
            }""")
            if cover:
                with open(os.path.join(os.path.dirname(catalog_path), "_meta.json"),
                          "w") as f:
                    json.dump({"cover": cover}, f, ensure_ascii=False)
        await close_catalog_if_reader_hidden(page)
        await _sleep(2)
    except Exception as e:
        print(f"  ⚠️  目录跳转异常: {e}")
    print(f"  ✅ 已跳到全书开头，当前:「{await _title(page)}」(点击首项「{first_title}」)")
