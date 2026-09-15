#!/usr/bin/env python3
"""浏览器注入与抓取：JS 钩子（5 常量）/ 捕获通道 / 强制重绘 / 整页判重接收。

依赖：text（行重建与页块组装）、chapter（find_title_line）、common（节流）。
等待一律经 `weread_common._sleep`（晚绑定 asyncio.sleep —— 回归测试的
monkeypatch 目标，禁止导入期别名）。
"""
from playwright.async_api import Page

from weread_chapter import find_title_line
from weread_common import THROTTLE, _sleep
from weread_text import (build_page_blocks, chars_to_lines,
                         merge_positioned_chars, text_already_seen, text_grams)

CANVAS_HOOK = """
(function() {
    window.__wr_chars = [];
    var origFill = CanvasRenderingContext2D.prototype.fillText;
    CanvasRenderingContext2D.prototype.fillText = function(text, x, y) {
        if (text && text.trim()) {
            // 阅读器给不同文本框（标题/正文）用不同的 translate 局部坐标系，
            // 必须套上当前变换矩阵，否则标题与正文会因各自从 (0, y) 起画
            // 而在重建时交织成同一行。
            var m = this.getTransform();
            var fm = /([0-9.]+)px/.exec(this.font || "");
            window.__wr_chars.push({
                t: text,
                x: Math.round((m.a * x + m.c * y + m.e) * 10) / 10,
                y: Math.round((m.b * x + m.d * y + m.f) * 10) / 10,
                s: fm ? parseFloat(fm[1]) : 0,
                c: this.canvas,
            });
        }
        return origFill.apply(this, arguments);
    };
    window.__wr_reset = function() { window.__wr_chars = []; };
    window.__wr_count = function() { return window.__wr_chars.length; };
    // 把 canvas 本地坐标换算成页面坐标（加上画布位置与位图/CSS 尺寸比）
    window.__wr_chars_page = function() {
        var rects = new Map();
        return window.__wr_chars.map(function(c) {
            var r = rects.get(c.c);
            if (!r) { r = c.c.getBoundingClientRect(); rects.set(c.c, r); }
            var sx = r.width / (c.c.width || r.width || 1);
            var sy = r.height / (c.c.height || r.height || 1);
            return {t: c.t, x: r.left + c.x * sx,
                    y: r.top + window.scrollY + c.y * sy, s: c.s};
        });
    };
})();
"""

# 当前视口内可见的书籍插图，带屏幕坐标
VIEWPORT_IMGS_JS = """
() => {
    const H = window.innerHeight, W = window.innerWidth, out = [];
    document.querySelectorAll('img[class*="wr_readerImage"]').forEach(i => {
        const src = i.src || i.getAttribute('data-src') || '';
        if (!src.includes('res.weread.qq.com/wrepub')) return;
        const r = i.getBoundingClientRect();
        if (r.width > 40 && r.height > 40 && r.bottom > 0 && r.top < H &&
            r.right > 0 && r.left < W &&
            getComputedStyle(i).visibility !== 'hidden' &&
            getComputedStyle(i).display !== 'none') {
            out.push({src, top: Math.round(r.top), left: Math.round(r.left),
                      w: i.naturalWidth||i.width, h: i.naturalHeight||i.height});
        }
    });
    return out;
}
"""

# reader 的两个 canvas 的屏幕位置
CANVAS_RECTS_JS = """
() => Array.from(document.querySelectorAll('canvas')).map(c => {
    const r = c.getBoundingClientRect();
    return {top: r.top, left: r.left, w: Math.round(r.width), h: Math.round(r.height)};
}).filter(r => r.h > 300)
"""

# WeRead hybrid-renders long chapters: initial chunks use canvas, while later
# chunks are virtualized as shuffled absolutely-positioned glyph spans.  The
# DOM order is intentionally unusable; document coordinates restore the lines.
POSITIONED_DOM_JS = """
() => Array.from(document.querySelectorAll('.renderTargetContent span.wr_absolute'))
    .filter(el => {
        const text = el.textContent || '';
        const r = el.getBoundingClientRect();
        return text.trim() && r.width > 0 && r.height > 0;
    })
    .map(el => {
        const r = el.getBoundingClientRect();
        return {t: el.textContent || '', x: r.left, y: r.top + window.scrollY};
    })
"""

CANVAS_BOTTOM_JS = """() => {
    const bottoms = Array.from(document.querySelectorAll('canvas'))
        .map(canvas => {
            const rect = canvas.getBoundingClientRect();
            return rect.bottom + window.scrollY;
        });
    return bottoms.length ? Math.max(...bottoms) : 0;
}"""


async def capture_positioned_dom(page, min_y, seen_imgs):
    """Scroll a long chapter and reconstruct its non-canvas virtualized text."""
    metrics = await page.evaluate(
        "() => ({height: Math.max(document.body.scrollHeight, "
        "document.documentElement.scrollHeight), viewport: window.innerHeight})",
    )
    max_scroll = max(0, int(metrics["height"] - metrics["viewport"]))
    step = max(500, int(metrics["viewport"] * 0.8))
    targets = list(range(0, max_scroll + 1, step))
    if not targets or targets[-1] != max_scroll:
        targets.append(max_scroll)

    lines_by_y = {}
    images_by_src = {}
    for target in targets:
        await page.evaluate("y => window.scrollTo(0, y)", target)
        await _sleep(THROTTLE["dom_scroll_settle"])
        chars = await page.evaluate(POSITIONED_DOM_JS)
        merge_positioned_chars(lines_by_y, chars, min_y=min_y)

        scroll_y = await page.evaluate("() => window.scrollY")
        for image in await page.evaluate(VIEWPORT_IMGS_JS):
            doc_y = image["top"] + scroll_y
            if doc_y >= min_y and image["src"] not in seen_imgs:
                image = dict(image)
                image["doc_y"] = doc_y
                images_by_src[image["src"]] = image

    await page.evaluate("() => window.scrollTo(0, 0)")
    await _sleep(THROTTLE["dom_scroll_settle"])

    items = [(y, {"type": "text", "text": text})
             for y, text in lines_by_y.items()]
    items.extend((image["doc_y"], {"type": "img", "src": image["src"],
                                    "w": image["w"], "h": image["h"]})
                 for image in images_by_src.values())
    items.sort(key=lambda item: item[0])
    blocks = []
    for _y, block in items:
        if block["type"] == "img":
            if block["src"] in seen_imgs:
                continue
            seen_imgs.add(block["src"])
        blocks.append(block)
    return blocks


async def force_repaint(page: Page) -> None:
    """Force one complete fillText pass for a page restored from the paint cache.

    回访读过的页时，阅读器直接复用已绘制好的 canvas 位图：不产生新的
    fillText 调用，字符缓冲区为空。抖动一次视口可触发完整重绘，两次
    resize 之间清空缓冲区，保证最终只留下一遍干净的字符数据。
    """
    size = page.viewport_size or {"width": 1200, "height": 900}
    await page.set_viewport_size(
        {"width": size["width"], "height": size["height"] + 1})
    await _sleep(THROTTLE["repaint_settle"])
    await page.evaluate("() => window.__wr_reset()")
    await page.set_viewport_size(size)
    await _sleep(THROTTLE["repaint_settle"])


def accept_blocks(new_blocks, ch_blocks, seen_grams=None):
    """把新块并入章节：去相邻重复，去与已见内容高度重冒的整页重放。"""
    added = 0
    for b in new_blocks:
        if b["type"] == "text":
            text = b["text"].strip()
            if not text:
                continue
            if seen_grams is not None:
                if text_already_seen(text, seen_grams):
                    continue
                seen_grams.update(text_grams(text))
            if (ch_blocks and ch_blocks[-1].get("type") == "text"
                    and ch_blocks[-1]["text"] == b["text"]):
                continue
        ch_blocks.append(b)
        added += 1
    return added


async def capture_current_page(page: Page, ch_blocks, seen_imgs, seen_grams=None) -> bool:
    """抓当前页的有序块，累加到 ch_blocks；返回是否有新内容"""
    await _sleep(THROTTLE["page_capture_settle"])
    chars = await page.evaluate("() => window.__wr_chars_page()")
    rects = await page.evaluate(CANVAS_RECTS_JS)
    imgs = await page.evaluate(VIEWPORT_IMGS_JS)
    new_blocks = build_page_blocks(chars, imgs, rects, seen_imgs)
    return accept_blocks(new_blocks, ch_blocks, seen_grams) > 0


async def capture_full_chapter(page: Page, ch_blocks, seen_imgs, seen_grams=None,
                               headings=()) -> tuple:
    """Capture initial canvases, then the virtualized lower DOM text.

    返回 (本页是否有新内容, 本页命中的标题页标题或 None, 本页开始时的块数)。
    """
    before = len(ch_blocks)
    await capture_current_page(page, ch_blocks, seen_imgs, seen_grams)
    canvas_end = await page.evaluate(CANVAS_BOTTOM_JS)
    dom_blocks = await capture_positioned_dom(page, canvas_end, seen_imgs)
    accept_blocks(dom_blocks, ch_blocks, seen_grams)
    if len(ch_blocks) == before and not dom_blocks:
        # 两条通道都没内容：多半是命中位图缓存（没有新的 fillText），
        # 强制重绘后再抓一次；DOM 通道有内容说明本就是虚拟 DOM 页，不重绘。
        if not await page.evaluate("() => window.__wr_count()"):
            await force_repaint(page)
            await capture_current_page(page, ch_blocks, seen_imgs, seen_grams)
    title_hit = find_title_line(ch_blocks[before:], headings)
    return len(ch_blocks) > before, title_hit, before
