"""回归测试：抓取与文本重建（拆分自 test_navigation_retry.py；11 项，断言逐字保留）。"""
import pytest
from playwright.async_api import async_playwright

import export_precise
from fakes import RepaintPage, StaleCanvasPage, TwoCanvasPage, no_delay


@pytest.mark.anyio
async def test_force_repaint_resets_char_buffer_between_resizes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exactly one clean fillText pass must survive the viewport jiggle."""
    page = RepaintPage()
    monkeypatch.setattr(export_precise.asyncio, "sleep", no_delay)

    await export_precise.force_repaint(page)

    assert page.sizes == [{"width": 1200, "height": 901},
                          {"width": 1200, "height": 900}]
    assert page.events == ["resize:1200x901", "reset", "resize:1200x900"]


@pytest.mark.anyio
async def test_capture_full_chapter_repaints_when_canvas_was_cached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A revisited page yields no fillText until the viewport is nudged."""
    page = StaleCanvasPage()
    blocks: list = []
    monkeypatch.setattr(export_precise.asyncio, "sleep", no_delay)

    captured, _title_hit, _start = await export_precise.capture_full_chapter(
        page, blocks, set())

    assert captured is True
    assert page.repainted is True
    assert blocks == [{"type": "text", "text": "版权", "size": 0}]


def test_merge_positioned_chars_restores_shuffled_virtualized_rows() -> None:
    """Virtualized DOM glyphs must be sorted by coordinates and deduplicated."""
    first_window = [
        {"t": "行", "x": 30, "y": 4200},
        {"t": "一", "x": 20, "y": 4200},
        {"t": "第", "x": 10, "y": 4200},
    ]
    overlapping_window = [
        {"t": "第", "x": 10, "y": 4200},
        {"t": "一", "x": 20, "y": 4200},
        {"t": "行", "x": 30, "y": 4200},
        {"t": "第", "x": 10, "y": 4240},
        {"t": "二", "x": 20, "y": 4240},
        {"t": "行", "x": 30, "y": 4240},
    ]
    lines: dict[int, str] = {}

    export_precise.merge_positioned_chars(lines, first_window, min_y=4100)
    export_precise.merge_positioned_chars(lines, overlapping_window, min_y=4100)

    assert [lines[y] for y in sorted(lines)] == ["第一行", "第二行"]


def test_chars_to_lines_keeps_multi_character_latin_draw_calls() -> None:
    """English name fragments are real fillText calls, not measurement noise."""
    chars = [
        {"t": "Palan", "x": 10, "y": 100},
        {"t": "ti", "x": 60, "y": 100},
        {"t": "r", "x": 80, "y": 100},
    ]

    assert export_precise.chars_to_lines(chars) == [
        {"y": 99, "text": "Palantir", "size": 0},
    ]


@pytest.mark.anyio
async def test_capture_full_chapter_keeps_two_pages_separate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end capture: the title page stays out of the body page's lines."""
    page = TwoCanvasPage()
    blocks: list = []
    monkeypatch.setattr(export_precise.asyncio, "sleep", no_delay)

    await export_precise.capture_full_chapter(page, blocks, set())

    assert [b["text"] for b in blocks] == ["认知生成", "第五章"]


def test_canvas_hook_reports_transformed_page_coordinates() -> None:
    """Text runs drawn in locally translated spaces must land at real positions."""
    async def scenario():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.set_content(
                '<canvas id="c" width="200" height="100" style="position:absolute;'
                'left:50px;top:30px;width:200px;height:100px"></canvas>')
            await page.add_script_tag(content=export_precise.CANVAS_HOOK)
            await page.evaluate("""() => {
                const ctx = document.getElementById('c').getContext('2d');
                ctx.setTransform(1, 0, 0, 1, 10, 20);
                ctx.fillText('标题', 0, 10);
            }""")
            chars = await page.evaluate("() => window.__wr_chars_page()")
            await browser.close()
            return chars

    import asyncio as _asyncio

    chars = _asyncio.run(scenario())

    assert chars == [{"t": "标题", "x": 60, "y": 60, "s": 10}]


def test_canvas_hook_discovers_tar_image_package() -> None:
    async def scenario():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.set_content(
                '<div data-package="https://cdn.example.com/comic.tar?a=1&amp;b=2"></div>')
            await page.add_script_tag(content=export_precise.CANVAS_HOOK)
            archives = await page.evaluate(export_precise.ARCHIVE_URLS_JS)
            await browser.close()
            return archives

    import asyncio as _asyncio

    assert _asyncio.run(scenario()) == [
        "https://cdn.example.com/comic.tar?a=1&b=2"]


def test_split_spread_by_canvas_keeps_pages_apart_on_shared_rows() -> None:
    """A title page drawn beside body text must not interleave into its lines."""
    chars = [
        {"t": "认", "x": 130, "y": 300}, {"t": "知", "x": 150, "y": 300},
        {"t": "第", "x": 700, "y": 300}, {"t": "五", "x": 720, "y": 300},
        {"t": "章", "x": 740, "y": 300},
    ]
    rects = [{"left": 120, "top": 60, "w": 361, "h": 770},
             {"left": 670, "top": 60, "w": 361, "h": 770}]

    pages = export_precise.split_spread_by_canvas(chars, rects)

    assert pages is not None
    assert ["".join(c["t"] for c in page) for page in pages] == ["认知", "第五章"]


def test_build_page_blocks_does_not_interleave_title_into_body() -> None:
    chars = [
        {"t": "认", "x": 130, "y": 300}, {"t": "知", "x": 150, "y": 300},
        {"t": "第", "x": 700, "y": 300}, {"t": "五", "x": 720, "y": 300},
        {"t": "章", "x": 740, "y": 300},
    ]
    rects = [{"left": 120, "top": 60, "w": 361, "h": 770},
             {"left": 670, "top": 60, "w": 361, "h": 770}]

    blocks = export_precise.build_page_blocks(chars, [], rects, set())

    assert [b["text"] for b in blocks] == ["认知", "第五章"]


def test_split_spread_by_canvas_falls_back_for_single_canvas() -> None:
    chars = [{"t": "认", "x": 130, "y": 300}]

    assert export_precise.split_spread_by_canvas(chars, []) is None


def test_text_already_seen_flags_replayed_pages_only() -> None:
    """只丢弃“几乎完全是已见内容”的整页重放；含新句子的页面必须保留。

    阈值定在 0.85：0.6 会把“大部分抄上一页、但末尾有新句子”的边界页误杀，
    实测曾导致“未来AIOS会越来越…”这类句尾消失。
    """
    seen = set()
    first = "从表面上看，条件似乎已经具备。大量企业已经部署AI系统。"
    seen.update(export_precise.text_grams(first))

    assert export_precise.text_already_seen(first, seen) is True
    assert export_precise.text_already_seen(first[:12], seen) is True
    assert export_precise.text_already_seen(
        "这是一段从未出现过的新段落，讲的是完全不同的主题与人物。", seen) is False
    # 旧内容占大头、但带着新句尾的边界页：保留
    borderline = ("从表面上看，条件似乎已经具备。大量企业已经部署AI系统。"
                  "但真正的变化发生在最后一个季度。")
    assert export_precise.text_already_seen(borderline, seen) is False


def test_capture_current_page_drops_replayed_page(monkeypatch) -> None:
    """The same page captured twice must not double the chapter text."""
    class ReplayPage:
        async def evaluate(self, expression, arg=None):
            if expression == "() => window.__wr_chars_page()":
                return [{"t": "从", "x": 10, "y": 100},
                        {"t": "表", "x": 28, "y": 100},
                        {"t": "面", "x": 46, "y": 100},
                        {"t": "上", "x": 64, "y": 100},
                        {"t": "看", "x": 82, "y": 100}]
            if expression == export_precise.CANVAS_RECTS_JS:
                return []
            if expression == export_precise.VIEWPORT_IMGS_JS:
                return []
            if expression == export_precise.ARCHIVE_URLS_JS:
                return []
            raise AssertionError(expression)

    async def run():
        page = ReplayPage()
        blocks: list = []
        grams: set = set()
        monkeypatch.setattr(export_precise.asyncio, "sleep", no_delay)
        first = await export_precise.capture_current_page(page, blocks, set(), grams)
        second = await export_precise.capture_current_page(page, blocks, set(), grams)
        return first, second, blocks

    import asyncio as _asyncio

    first, second, blocks = _asyncio.run(run())

    assert first is True
    assert second is False
    assert [b["text"] for b in blocks] == ["从表面上看"]
