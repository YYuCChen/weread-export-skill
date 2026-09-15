"""共享假页对象（8 类）与 no_delay —— 回归测试的公共夹具（拆分自 test_navigation_retry.py）。"""
from playwright.async_api import Error as PlaywrightError

import export_precise


class NavigatingPage:
    """Page fake that loses its first JavaScript execution context."""

    def __init__(self) -> None:
        self.calls = 0

    async def evaluate(self, expression: str) -> int:
        assert expression == "() => window.__wr_count()"
        self.calls += 1
        if self.calls == 1:
            raise PlaywrightError(
                "Execution context was destroyed, most likely because of a navigation",
            )
        return 42


class ControlRecorder:
    """Record explicit navigation-control clicks."""

    def __init__(self, count: int = 1) -> None:
        self.available = count
        self.clicks = 0
        self.used_last = False
        self.used_first = False

    async def count(self) -> int:
        return self.available

    async def click(self, timeout: int) -> None:
        assert timeout == 10_000
        self.clicks += 1

    @property
    def first(self) -> "ControlRecorder":
        self.used_first = True
        return self

    @property
    def last(self) -> "ControlRecorder":
        self.used_last = True
        return self


class PagerPage:
    """Reader fake for the current build: the pager control carries the text."""

    def __init__(self, pager_count: int = 1) -> None:
        self.pager = ControlRecorder(pager_count)
        self.queried: list[str] = []

    def locator(self, selector: str) -> ControlRecorder:
        self.queried.append(selector)
        if selector == export_precise.NEXT_PAGE_SELECTORS[0]:
            return self.pager
        return ControlRecorder(0)


class LegacyFooterPage:
    """Reader fake for the older build: only the footer control exists."""

    def __init__(self, footer_count: int = 1) -> None:
        self.footer = ControlRecorder(footer_count)

    def locator(self, selector: str) -> ControlRecorder:
        if selector == export_precise.LEGACY_FOOTER_SELECTOR:
            return self.footer
        return ControlRecorder(0)


class CatalogOverlayPage:
    """Reader fake where choosing the selected catalog item leaves it open."""

    def __init__(self) -> None:
        self.catalog = ControlRecorder(count=1)

    def locator(self, selector: str) -> ControlRecorder:
        if selector == "button.readerControls_item.catalog":
            return self.catalog
        return ControlRecorder(0)


class RepaintPage:
    """Reader fake recording viewport resizes and buffer resets."""

    def __init__(self) -> None:
        self.viewport_size = {"width": 1200, "height": 900}
        self.sizes: list[dict] = []
        self.events: list[str] = []

    async def set_viewport_size(self, size: dict) -> None:
        self.sizes.append(dict(size))
        self.events.append(f"resize:{size['width']}x{size['height']}")

    async def evaluate(self, expression: str) -> None:
        assert expression == "() => window.__wr_reset()"
        self.events.append("reset")


class StaleCanvasPage:
    """Reader fake whose canvas is restored from cache without new fillText."""

    def __init__(self) -> None:
        self.viewport_size = {"width": 1200, "height": 900}
        self.repainted = False

    async def evaluate(self, expression: str, arg=None):
        if expression == "() => window.__wr_chars_page()":
            if self.repainted:
                return [{"t": "版", "x": 10, "y": 100},
                        {"t": "权", "x": 30, "y": 100}]
            return []
        if expression == export_precise.CANVAS_RECTS_JS:
            return []
        if expression == export_precise.VIEWPORT_IMGS_JS:
            return []
        if expression == export_precise.ARCHIVE_URLS_JS:
            return []
        if expression == export_precise.CANVAS_BOTTOM_JS:
            return 0
        if expression == "() => window.__wr_count()":
            return 2 if self.repainted else 0
        if expression == "() => window.__wr_reset()":
            return None
        if expression == export_precise.POSITIONED_DOM_JS:
            return []
        if "scrollTo" in expression:
            return None
        if "scrollHeight" in expression:
            return {"height": 900, "viewport": 900}
        if expression == "() => window.scrollY":
            return 0
        raise AssertionError(expression)

    async def set_viewport_size(self, size: dict) -> None:
        # A resize forces the reader to paint the page again.
        self.viewport_size = dict(size)
        self.repainted = True


class TwoCanvasPage:
    """Reader fake whose spread mixes a title page with body text rows."""

    def __init__(self) -> None:
        self.viewport_size = {"width": 1200, "height": 900}

    async def evaluate(self, expression: str, arg=None):
        if expression == "() => window.__wr_chars_page()":
            return [
                # 左页正文
                {"t": "认", "x": 130, "y": 300},
                {"t": "知", "x": 150, "y": 300},
                {"t": "生", "x": 170, "y": 300},
                {"t": "成", "x": 190, "y": 300},
                # 右页标题（与左页正文同一 y）
                {"t": "第", "x": 700, "y": 300},
                {"t": "五", "x": 720, "y": 300},
                {"t": "章", "x": 740, "y": 300},
            ]
        if expression == export_precise.CANVAS_RECTS_JS:
            return [{"left": 120, "top": 60, "w": 361, "h": 770},
                    {"left": 670, "top": 60, "w": 361, "h": 770}]
        if expression == export_precise.VIEWPORT_IMGS_JS:
            return []
        if expression == export_precise.ARCHIVE_URLS_JS:
            return []
        if expression == export_precise.CANVAS_BOTTOM_JS:
            return 0
        if expression == "() => window.__wr_count()":
            return 7
        if expression == export_precise.POSITIONED_DOM_JS:
            return []
        if "scrollTo" in expression:
            return None
        if "scrollHeight" in expression:
            return {"height": 900, "viewport": 900}
        if expression == "() => window.scrollY":
            return 0
        raise AssertionError(expression)


async def no_delay(_seconds: float) -> None:
    """Avoid wall-clock waits in the stabilization unit test."""
