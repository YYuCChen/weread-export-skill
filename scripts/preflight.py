#!/usr/bin/env python3
"""导出前预检：登录态 / 目标书网页可读性 / 端到端冒烟 / 平台逐章字数基线。

用法: python3 preflight.py <book_url_or_id> [--relogin]
只读检查（不抓正文）；只写状态目录（登录态与 <book_dir>/_platform_chapterinfo.json）。
退出码：0 通过 / 2 需登录 / 3 访问受限 / 4 结构信号 / 1 用法错误。
"""
import asyncio
import json
import os
import re
import sys

from playwright.async_api import async_playwright

import export_precise as engine
import weread_common as wc

# 数字 bookId：真机实测——阅读器页面 ld+json 结构化数据的 @Id（兜底：wrepub 资源 URL 段）。
NUMERIC_ID_JS = """() => {
    for (const b of document.querySelectorAll('script[type="application/ld+json"]')) {
        try {
            const d = JSON.parse(b.textContent || '{}');
            if (d && /^\\d{6,}$/.test(String(d['@Id'] || ''))) return String(d['@Id']);
        } catch (e) {}
    }
    const m = document.documentElement.outerHTML.match(/wrepub\\/(?:web\\/)?(?:CB_)?(\\d{6,})/);
    return m ? m[1] : '';
}"""

# readInfo：GET + 数字 bookId（真机实测形态；encode id 会被接口以「参数格式错误」拒绝）。
READINFO_JS = """async (nid) => {
    const base = '/web/book/readInfo?bookId=' + encodeURIComponent(nid);
    const urls = [base + '&finishedBookCount=1&finishedBookIndex=1&finishedDate=1', base];
    for (const url of urls) {
        try {
            const r = await fetch(url, {credentials: 'include'});
            const text = await r.text();
            const data = JSON.parse(text);
            const info = data.bookInfo || {};
            return {status: r.status, bookId: String(info.bookId || data.bookId || ''),
                    snippet: text.slice(0, 200)};
        } catch (e) {}
    }
    return {status: 0, bookId: '', snippet: ''};
}"""

# chapterInfos：POST {"bookIds":[<数字>]}（真机实测成功形态，返回 data[0].updated）。
CHAPTERINFO_JS = """async (nid) => {
    try {
        const r = await fetch('/web/book/chapterInfos', {
            method: 'POST', credentials: 'include',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({bookIds: [nid]})});
        return {ok: r.ok, status: r.status, text: await r.text()};
    } catch (e) { return {ok: false, status: 0, text: String(e)}; }
}"""


async def wait_login(page, seconds=180):
    """未登录时等待人工扫码。返回是否已登录。"""
    if not wc.classify_login_url(page.url):
        return True
    print("  ⚠️  未登录：请在浏览器窗口扫码（最多等待 %d 秒）..." % seconds)
    for _ in range(seconds // 3):
        await wc._sleep(3)
        if not wc.classify_login_url(page.url):
            return True
    return False


async def page_channels(page, patience=4) -> dict:
    """两条渲染通道的字符数（canvas 缓冲 / 虚拟 DOM span）；为空时隔 0.5s 再看，最多 patience 次。"""
    for _ in range(patience):
        canvas = await page.evaluate(
            "() => (typeof window.__wr_count === 'function') ? window.__wr_count() : -1")
        dom = await page.evaluate(
            "() => document.querySelectorAll('.renderTargetContent span.wr_absolute').length")
        if canvas > 0 or dom > 0:
            break
        await wc._sleep(0.5)
    return {"canvas_chars": canvas, "virtual_spans": dom,
            "chars": canvas if canvas > 0 else dom}


async def smoke_once(page) -> dict:
    """冒烟一轮：跳目录首项 → 断言抓到字符 → 翻页（最多 3 页，跳过无字符页）→ 再次断言。"""
    await engine.goto_first_chapter(page)
    await engine.wait_stable(page, 0)
    first = await page_channels(page)
    if first["chars"] <= 0:
        await engine.force_repaint(page)
        first = await page_channels(page)
    turned = False
    second = {"canvas_chars": 0, "virtual_spans": 0, "chars": 0}
    for _turn in range(3):
        await page.evaluate("() => window.__wr_reset()")
        if not await engine.advance_reader(page):
            break
        turned = True
        await wc._sleep(wc.THROTTLE["page_settle_after_turn"])
        await engine.wait_stable(page, 0)
        second = await page_channels(page)
        if second["chars"] > 0:
            break
        # 命中位图缓存 / 空白页时强制重绘后再确认
        await engine.force_repaint(page)
        second = await page_channels(page)
        if second["chars"] > 0:
            break
    return {"first_page_chars": first["chars"], "after_turn_chars": second["chars"],
            "first": first, "second": second, "turned": turned}


async def smoke(page, attempts=2) -> dict:
    """有限次数重试后的冒烟结果；两条断言全过才算通过。"""
    result = {}
    for attempt in range(1, attempts + 1):
        result = await smoke_once(page)
        result["attempts"] = attempt
        if (result["turned"] and result["first_page_chars"] > 0
                and result["after_turn_chars"] > 0):
            result["ok"] = True
            return result
        await wc._sleep(2)
    result["ok"] = False
    return result


async def fetch_baseline(page, book_dir):
    """经已登录会话拉平台逐章字数基线；返回 (是否成功, 说明)。

    真机实测链（实施期技术校验点校准）：页面 ld+json 的 @Id = 数字 bookId
    → `readInfo?bookId=<数字>` 确认（bookInfo.bookId 回显）
    → `POST chapterInfos {"bookIds":[<数字>]}` → 原样保存。
    """
    try:
        numeric_id = str(await page.evaluate(NUMERIC_ID_JS) or "").strip()
        print(f"  页面数字 bookId（ld+json @Id）: {numeric_id or '(未取到)'}")
        if not numeric_id:
            return False, "页面未暴露数字 bookId（页面结构疑似变化）"
        info = await page.evaluate(READINFO_JS, numeric_id)
        confirmed = str((info or {}).get("bookId") or "").strip()
        print(f"  readInfo: status={(info or {}).get('status')} "
              f"bookInfo.bookId={confirmed or '(未取到)'}")
        print(f"  readInfo 响应片段（形态校准用）: "
              f"{str((info or {}).get('snippet', ''))[:160]}")
        if not confirmed:
            return False, "readInfo 未取到数字 ID（响应形态疑似变化）"
        res = await page.evaluate(CHAPTERINFO_JS, confirmed)
        if not (res or {}).get("ok"):
            return False, f"chapterInfos HTTP {(res or {}).get('status')}"
        text = (res or {}).get("text", "")
        chapters = json.loads(text)["data"][0]["updated"]
        if not chapters:
            return False, "chapterInfos 章节列表为空"
    except Exception as exc:
        return False, f"基线拉取异常: {exc}"
    os.makedirs(book_dir, exist_ok=True)
    with open(os.path.join(book_dir, "_platform_chapterinfo.json"), "w",
              encoding="utf-8") as f:
        f.write(text)
    return True, f"{len(chapters)} 章"


async def main(book_id, relogin=False) -> int:
    book_dir = wc.book_dir(book_id)
    result = {"book_id": book_id}
    if relogin:
        try:
            backup = wc.backup_profile()
        except OSError as exc:
            print(f"  ⛔ 无法备份旧登录态：{exc}")
            print("  请先关闭仍在运行的微信读书导出浏览器，再重试。")
            return wc.EXIT_USAGE
        print(f"  已备份旧登录态：{backup}" if backup else "  没有旧登录态，将重新登录。")
    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            wc.profile_dir(), headless=False, viewport={"width": 1200, "height": 900},
            args=["--disable-blink-features=AutomationControlled"])

        # 1) 登录态
        shelf = await ctx.new_page()
        await shelf.goto("https://weread.qq.com/web/shelf", timeout=30000)
        await wc._sleep(3)
        result["logged_in"] = await wait_login(shelf)
        print("  ✅ 登录有效" if result["logged_in"] else "  ❌ 登录失败（超时）")
        await shelf.close()
        if not result["logged_in"]:
            await ctx.close()
            print("\n  ⛔ 登录失效（请重跑预检并扫码登录）")
            return wc.EXIT_NEED_LOGIN

        # 2) 书籍详情页：书名 / 标称字数（记录用）
        detail = await ctx.new_page()
        try:
            await detail.goto(f"https://weread.qq.com/web/bookDetail/{book_id}",
                              timeout=30000)
            await wc._sleep(4)
            text = await detail.evaluate("() => document.body.innerText || ''")
            m = re.search(r"(?:共)?\s*(\d+(?:\.\d+)?)\s*万字", text)
            result["nominal_words"] = m.group(0).strip() if m else ""
            result["detail_head"] = [ln.strip() for ln in text.splitlines() if ln.strip()][:20]
            result["detail_restricted"] = wc.classify_page_text(text)
        except Exception as exc:
            result["detail_error"] = str(exc)
        await detail.close()

        # 3) 阅读器：渲染通道统计 + 冒烟 + 平台基线
        page = await ctx.new_page()
        await page.add_init_script(engine.CANVAS_HOOK)
        await page.goto(f"https://weread.qq.com/web/reader/{book_id}",
                        wait_until="networkidle", timeout=30000)
        await wc._sleep(6)
        result["reader_url"] = page.url
        if wc.classify_login_url(page.url):
            await ctx.close()
            print("\n  ⛔ 登录失效（阅读器被重定向到登录页）")
            return wc.EXIT_NEED_LOGIN
        page_text = await page.evaluate("() => document.body.innerText || ''")
        if wc.classify_page_text(page_text):
            await ctx.close()
            print("\n  ⛔ 访问受限（页面出现受限提示，如「去 App 阅读」）")
            return wc.EXIT_BLOCKED
        result["reader"] = await page.evaluate("""() => {
            const top = document.querySelector(
                '.readerTopBar_title_chapter, .renderTargetPageInfo_header_chapterTitle');
            const link = document.querySelector('.readerTopBar_title_link');
            const canvases = Array.from(document.querySelectorAll('canvas'))
                .map(c => c.getBoundingClientRect()).filter(r => r.height > 300);
            return {chapter: top ? top.textContent.trim() : '',
                    bookTitle: link ? link.textContent.trim() : '',
                    canvasCount: canvases.length};
        }""")

        smoke_result = await smoke(page)
        result["smoke"] = smoke_result
        print(f"  {'✅' if smoke_result['ok'] else '❌'} 冒烟：抓到首页字符 "
              f"{smoke_result['first_page_chars']} 个，翻页后 "
              f"{smoke_result['after_turn_chars']} 个"
              f"（翻页控件可用: {smoke_result['turned']}）")

        baseline_ok, baseline_detail = False, ""
        for _attempt in range(2):
            baseline_ok, baseline_detail = await fetch_baseline(page, book_dir)
            if baseline_ok:
                break
            await wc._sleep(2)
        result["baseline"] = {"ok": baseline_ok, "detail": baseline_detail}
        if baseline_ok:
            print(f"  ✅ 平台基线已保存: "
                  f"{os.path.join(book_dir, '_platform_chapterinfo.json')}"
                  f"（{baseline_detail}）")
        else:
            print(f"  ❌ 平台基线失败: {baseline_detail}")
        await page.close()
        await ctx.close()

    print("\n" + "=" * 60)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("=" * 60)
    if not smoke_result["ok"]:
        print("\n  ⛔ 页面结构可能变化（冒烟未通过：翻页控件或渲染通道异常）")
        return wc.EXIT_STRUCTURE
    if not baseline_ok:
        print(f"\n  ⛔ 页面结构可能变化（平台基线接口异常：{baseline_detail}）")
        return wc.EXIT_STRUCTURE
    print("\n  ✅ 预检通过")
    return wc.EXIT_OK


if __name__ == "__main__":
    args = [arg for arg in sys.argv[1:] if arg != "--relogin"]
    if len(args) != 1:
        print("用法: python3 preflight.py <book_url_or_id> [--relogin]")
        sys.exit(wc.EXIT_USAGE)
    try:
        sys.exit(asyncio.run(main(wc.parse_book_id(args[0]), "--relogin" in sys.argv[1:])))
    except KeyboardInterrupt:
        print("\n  已中断（重跑同命令即可；登录态不受影响）")
        sys.exit(wc.EXIT_USAGE)
    except Exception as exc:
        print(f"\n  ⛔ 未分类异常: {exc}")
        print("  建议：确认网络可用后重跑同命令；若复现，保留现场并按 playbook 排查。")
        sys.exit(wc.EXIT_USAGE)
