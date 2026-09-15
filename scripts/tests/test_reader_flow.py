"""回归测试：阅读器导航（拆分自 test_navigation_retry.py；7 项，断言逐字保留）。"""
import pytest
from playwright.async_api import async_playwright

import export_precise
from fakes import (CatalogOverlayPage, LegacyFooterPage, NavigatingPage,
                   PagerPage, no_delay)


@pytest.mark.anyio
async def test_wait_stable_retries_when_navigation_destroys_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = NavigatingPage()
    monkeypatch.setattr(export_precise.asyncio, "sleep", no_delay)

    result = await export_precise.wait_stable(page, prev_count=0, timeout=2)

    assert result == 42
    assert page.calls == 3


@pytest.mark.anyio
async def test_title_reads_current_chapter_header_selector() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(
            '<div class="renderTargetPageInfo_header_chapterTitle">版权信息</div>',
        )
        assert await export_precise._title(page) == "版权信息"

        await page.set_content(
            '<span class="readerTopBar_title_chapter">自序 一封泛黄的信</span>',
        )
        assert await export_precise._title(page) == "自序 一封泛黄的信"
        await browser.close()


@pytest.mark.anyio
async def test_advance_reader_clicks_current_pager_control_once() -> None:
    page = PagerPage()

    advanced = await export_precise.advance_reader(page)

    assert advanced is True
    assert page.pager.clicks == 1
    assert page.pager.used_first is True
    assert page.queried[0] == export_precise.NEXT_PAGE_SELECTORS[0]


@pytest.mark.anyio
async def test_advance_reader_falls_back_to_legacy_footer_control() -> None:
    """The older build exposes only button.readerFooter_button."""
    page = LegacyFooterPage()

    advanced = await export_precise.advance_reader(page)

    assert advanced is True
    assert page.footer.clicks == 1
    assert page.footer.used_last is True


@pytest.mark.anyio
async def test_advance_reader_returns_end_when_no_control_is_present() -> None:
    page = LegacyFooterPage(footer_count=0)

    advanced = await export_precise.advance_reader(page)

    assert advanced is False
    assert page.footer.clicks == 0


@pytest.mark.anyio
async def test_close_catalog_when_selected_first_item_keeps_overlay_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = CatalogOverlayPage()
    monkeypatch.setattr(export_precise.asyncio, "sleep", no_delay)

    closed = await export_precise.close_catalog_if_reader_hidden(page)

    assert closed is True
    assert page.catalog.clicks == 1


@pytest.mark.anyio
async def test_close_catalog_leaves_a_visible_reader_alone() -> None:
    """While the pager is on screen the catalog is already closed."""
    page = PagerPage()

    closed = await export_precise.close_catalog_if_reader_hidden(page)

    assert closed is False
    assert page.pager.clicks == 0
