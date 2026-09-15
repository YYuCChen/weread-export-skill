#!/usr/bin/env python3
"""把合并稿 Markdown 转成自包含的三种格式（图片全部内嵌，不留外部引用）。

  1. Markdown — 图片引用就地替换成 base64 data URI（单文件自包含；大图先压缩）
  2. EPUB     — pandoc（图片内嵌进 EPUB，带目录）
  3. PDF      — pandoc 生成独立 HTML → 本机 Chromium 打印（CJK 字体与图片原生支持）

用法: python3 make_formats.py <合并稿.md> [输出目录]
退出码：0 成功 / 7 构建失败（pandoc 缺失 / 三格式断言失败）/ 6 合并稿不存在。
"""
import base64
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import weread_common as wc

MD_IMG_RE = re.compile(r'!\[([^\]]*)\]\(([^)\s]+)\)')
IMG_SRC_RE = re.compile(r'<img[^>]+src="([^"]+)"')

PRINT_CSS = """
@page { size: A4; margin: 18mm 16mm 20mm; }
body { font-family: "Songti SC", "STSong", "SimSun", serif;
       font-size: 11pt; line-height: 1.8; color: #1a1a1a; }
h1 { font-family: "PingFang SC", "Heiti SC", sans-serif;
     font-size: 17pt; margin: 0 0 1.1em; padding-bottom: .3em;
     border-bottom: 1px solid #ddd; page-break-before: always; }
h1:first-of-type { page-break-before: avoid; }
h2 { font-family: "PingFang SC", "Heiti SC", sans-serif;
     font-size: 13pt; margin: 1.8em 0 .6em; page-break-after: avoid; }
p { margin: 0 0 .6em; text-align: justify; text-justify: inter-ideograph; }
img { max-width: 100%; height: auto; display: block; margin: 1em auto;
      page-break-inside: avoid; }
hr { border: none; border-top: 1px solid #eee; margin: 2em 0; }
blockquote { margin: 1em 2em; color: #444; }
#TOC { page-break-after: always; }
#TOC ul { list-style: none; padding-left: 1em; }
#TOC a { text-decoration: none; color: #333; }
"""

EPUB_CSS = """
body { line-height: 1.75; }
h1 { margin: 1.2em 0 .8em; }
img { max-width: 100%; height: auto; }
"""


def resolve_pandoc() -> str:
    """pandoc 解析：$PANDOC > PATH 上的 pandoc > 报错（退出码 7）。"""
    env = os.environ.get("PANDOC")
    if env and os.path.exists(env):
        return env
    found = shutil.which("pandoc")
    if found:
        return found
    print("⛔ 构建失败：找不到 pandoc。")
    print("   安装：macOS `brew install pandoc`；Ubuntu `sudo apt install pandoc`；")
    print("   Windows `winget install --source winget --exact --id JohnMacFarlane.Pandoc`。")
    print("   或设置环境变量 PANDOC 指向可执行文件。")
    return ""


def run(cmd, **kw):
    print("  $", " ".join(cmd[:6]), "..." if len(cmd) > 6 else "")
    subprocess.run(cmd, check=True, **kw)


def shrink_for_embedding(path, target_bytes=18000):
    """为大图做一个适合内嵌的压缩版。

    Typora 等编辑器对超长 data URI 不友好（实测 269KB 单行会显示为原始文本），
    所以 Markdown 内嵌用的图片先压到几十 KB 以内；EPUB/PDF 仍用原图。
    优先 macOS 的 sips；不可用时回退 Pillow；都不可用或压缩失败 → 用原图 + 警告（不阻断）。
    """
    if os.path.getsize(path) <= target_bytes:
        return path
    out = os.path.join(tempfile.gettempdir(), "embed_" + os.path.basename(path))
    steps = ((560, 80), (480, 70), (400, 60), (320, 50))
    shrunk = False
    if shutil.which("sips"):
        for maxdim, quality in steps:
            try:
                subprocess.run(["sips", "-Z", str(maxdim), "-s", "formatOptions",
                                str(quality), path, "--out", out],
                               check=True, capture_output=True)
            except subprocess.CalledProcessError:
                break  # sips 处理不了该图：回退 Pillow / 原图（不阻断）
            shrunk = True
            if os.path.getsize(out) <= target_bytes:
                break
    if not shrunk:
        try:
            from PIL import Image
            for maxdim, quality in steps:
                with Image.open(path) as im:
                    im = im.convert("RGB")
                    im.thumbnail((maxdim, maxdim))
                    im.save(out, "JPEG", quality=quality, optimize=True)
                shrunk = True
                if os.path.getsize(out) <= target_bytes:
                    break
        except ImportError:
            print("  ⚠️  无可用压缩通道（sips/Pillow），跳过压缩（内嵌图可能偏大）")
            return path
        except Exception as exc:
            print(f"  ⚠️  图片压缩失败（{exc}），改用原图（不阻断）")
            return path
    if not shrunk:
        return path
    return out if os.path.getsize(out) < os.path.getsize(path) else path


def embed_images_in_markdown(md_path, out_path):
    """把 ![](images/x.png) 换成 ![data:image/png;base64,…]，产出单文件自包含 Markdown。"""
    base_dir = os.path.dirname(os.path.abspath(md_path))
    text = open(md_path, encoding="utf-8").read()
    stats = {"embedded": 0, "missing": [], "external": 0, "shrunk": 0,
             "max_b64": 0}

    def repl(match):
        alt, target = match.group(1), match.group(2)
        if target.startswith(("data:", "http://", "https://")):
            stats["external"] += 1
            return match.group(0)
        path = os.path.join(base_dir, target)
        if not os.path.exists(path):
            stats["missing"].append(target)
            return match.group(0)
        embedded_path = shrink_for_embedding(path)
        if embedded_path != path:
            stats["shrunk"] += 1
        mime = mimetypes.guess_type(embedded_path)[0] or "image/jpeg"
        with open(embedded_path, "rb") as f:
            payload = base64.b64encode(f.read()).decode("ascii")
        stats["embedded"] += 1
        stats["max_b64"] = max(stats["max_b64"], len(payload))
        return f"![{alt}](data:{mime};base64,{payload})"

    out = MD_IMG_RE.sub(repl, text)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(out)
    return stats


def make_epub(md_path, title, author, out_dir, tmp_dir, pandoc):
    epub_css = os.path.join(tmp_dir, "epub.css")
    with open(epub_css, "w", encoding="utf-8") as f:
        f.write(EPUB_CSS)
    out = os.path.join(out_dir, f"{title}.epub")
    run([pandoc, md_path, "-o", out, "--toc", "--toc-depth=2",
         "--css", epub_css,
         "--metadata", f"title={title}", "--metadata", f"author={author}",
         "--metadata", "lang=zh-CN"], cwd=os.path.dirname(md_path))
    return out


def make_html(md_path, title, out_html, tmp_dir, pandoc):
    css = os.path.join(tmp_dir, "print.css")
    with open(css, "w", encoding="utf-8") as f:
        f.write(PRINT_CSS)
    run([pandoc, md_path, "-s", "-t", "html5", "--embed-resources",
         "--toc", "--toc-depth=2", "--css", css,
         "--metadata", f"title={title}", "--metadata", "lang=zh-CN",
         "-o", out_html], cwd=os.path.dirname(md_path))
    return out_html


def make_pdf(html_path, out_pdf):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(Path(html_path).resolve().as_uri(), wait_until="load")
        page.wait_for_timeout(1500)
        page.pdf(path=out_pdf, format="A4", print_background=True,
                 margin={"top": "18mm", "bottom": "20mm",
                         "left": "16mm", "right": "16mm"},
                 display_header_footer=True,
                 header_template="<div></div>",
                 footer_template=(
                     '<div style="font-size:9px;width:100%;text-align:center;'
                     'color:#777;font-family:sans-serif;">'
                     '<span class="pageNumber"></span></div>'))
        browser.close()
    return out_pdf


def check_embedded_md(out_text, stats) -> list:
    """① 内嵌 md 外部引用 0、缺失 0。"""
    problems = []
    external = [t for _alt, t in MD_IMG_RE.findall(out_text)
                if not t.startswith("data:")]
    if external:
        problems.append(f"内嵌 md 仍有外部引用 {len(external)} 处")
    if stats["missing"]:
        problems.append(f"缺失图片 {stats['missing']}")
    return problems


def check_epub(epub_path, ref_count) -> list:
    """② EPUB 内嵌图数 == 引用图数（zipfile 计数）。"""
    with zipfile.ZipFile(epub_path) as zf:
        images = [n for n in zf.namelist()
                  if n.lower().endswith((".jpg", ".jpeg", ".png", ".gif", ".webp"))]
    if len(images) != ref_count:
        return [f"EPUB 内嵌图 {len(images)} != 引用图 {ref_count}"]
    return []


def check_html(html_text) -> list:
    """③ PDF 用 HTML 无外部资源引用（<img> 的 src 必须全是 data URI）。"""
    bad = [src for src in IMG_SRC_RE.findall(html_text) if not src.startswith("data:")]
    if bad:
        return [f"打印 HTML 仍有外部图片引用 {len(bad)} 处（前三个: {bad[:3]}）"]
    return []


def main(md_path, out_dir=None) -> int:
    pandoc = resolve_pandoc()
    if not pandoc:
        return wc.EXIT_BUILD_FAIL
    md_path = os.path.abspath(md_path)
    if not os.path.exists(md_path):
        print(f"⛔ 输入缺失：找不到合并稿 {md_path}")
        return wc.EXIT_MISSING_INPUT
    out_dir = os.path.abspath(out_dir or os.path.dirname(md_path))
    os.makedirs(out_dir, exist_ok=True)
    tmp_dir = tempfile.gettempdir()

    text = open(md_path, encoding="utf-8").read()
    title = text.splitlines()[0].lstrip("# ").strip()
    safe_title = wc.safe_filename(title)
    m = re.search(r'^\*\*(.+?)\*\*\s*$', text, re.MULTILINE)
    author = m.group(1).strip() if m else ""

    print(f"书名: {title}\n作者: {author}\n源: {md_path}\n输出: {out_dir}")

    print("\n[1/3] Markdown（图片内嵌 base64，大图先压缩）...")
    embedded_md = os.path.join(out_dir, f"{safe_title}.md")
    stats = embed_images_in_markdown(md_path, embedded_md)
    out_text = open(embedded_md, encoding="utf-8").read()
    print(f"  ✅ 内嵌 {stats['embedded']} 张图（其中压缩 {stats['shrunk']} 张，"
          f"最大 base64 行 {stats['max_b64']:,} 字符；"
          f"原始外部引用 {stats['external']}，缺失 {stats['missing'] or '无'}）")
    print(f"  ✅ {embedded_md}  ({os.path.getsize(embedded_md):,} bytes)")

    print("\n[2/3] EPUB（用原图）...")
    epub = make_epub(md_path, safe_title, author, out_dir, tmp_dir, pandoc)
    print(f"  ✅ {epub}  ({os.path.getsize(epub):,} bytes)")

    print("\n[3/3] PDF（Chromium 打印，用原图）...")
    html = make_html(md_path, safe_title,
                     os.path.join(tmp_dir, "book_print.html"), tmp_dir, pandoc)
    pdf = make_pdf(html, os.path.join(out_dir, f"{safe_title}.pdf"))
    print(f"  ✅ {pdf}  ({os.path.getsize(pdf):,} bytes)")

    print("\n[断言] 三格式自包含检查...")
    problems = []
    problems += check_embedded_md(out_text, stats)
    problems += check_epub(epub, len(MD_IMG_RE.findall(text)))
    problems += check_html(open(html, encoding="utf-8").read())
    if problems:
        for p in problems:
            print(f"  ⛔ {p}")
        print("  ⛔ 构建断言失败")
        return wc.EXIT_BUILD_FAIL
    print("  ✅ 构建断言通过（内嵌 md 外部引用 0 / 缺失 0；"
          "EPUB 内嵌图数 == 引用图数；打印 HTML 无外部资源引用）")
    return wc.EXIT_OK


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python3 make_formats.py <合并稿.md> [输出目录]")
        sys.exit(wc.EXIT_USAGE)
    sys.exit(main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None))
