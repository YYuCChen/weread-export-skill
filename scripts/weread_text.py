#!/usr/bin/env python3
"""文本重建与渲染：双页拆分 / 行重建与折叠 / 坐标合并 / 页块组装 / 章节渲染与保存。

依赖：chapter（标题判据）。本模块无等待；渲染出的 `images/` 相对引用与
`raw/<nnnn>.json` 的 images 名单（= 下载名单）在渲染时固化。
"""
import json
import os
import re

from weread_chapter import normalize_title, split_heading_prefix

SENTENCE_END = set("。！？；：」）】》…—")


def split_spread(chars):
    """双页拆分：返回 [左页chars, 右页chars] 或 [单页chars]"""
    if len(chars) < 20:
        return [chars]
    singles = [(i, c) for i, c in enumerate(chars) if len(c["t"]) == 1]
    if len(singles) < 10:
        return [chars]
    for j in range(1, len(singles)):
        if singles[j - 1][1]["y"] > 400 and singles[j][1]["y"] < 200:
            return [chars[:singles[j][0]], chars[singles[j][0]:]]
    return [chars]


def collapse_doubled_line(text):
    """同一行被画两遍（A+A）或逐字叠印（AABB）时折叠回 A。

    实测版权页会被阅读器重绘两次，字符缓冲区里同一行出现两遍；
    两个字符集在坐标上重合，重建时就得到“一段文字 + 同一段文字”。
    """
    n = len(text)
    if n >= 12 and n % 2 == 0:
        half = n // 2
        if text[:half] == text[half:]:
            return text[:half]
        even, odd = text[0::2], text[1::2]
        if even == odd:
            return even
    return text


def chars_to_lines(chars):
    """把单页字符按 y 分行，返回 [{y, text}]（未合并段落）"""
    # These values come from actual fillText calls.  WeRead may draw English
    # words in multi-character fragments (for example `Palan` + `ti` + `r`).
    # Filtering ASCII fragments therefore corrupts names and technical terms.
    real = [c for c in chars if c["t"].strip()]
    if not real:
        return []
    rows = {}
    for c in real:
        y_key = round(c["y"] / 3) * 3
        rows.setdefault(y_key, []).append(c)
    lines = []
    for yk in sorted(rows):
        line = "".join(c["t"] for c in sorted(rows[yk], key=lambda c: c["x"]))
        line = collapse_doubled_line(line.strip())
        if line:
            lines.append({"y": yk, "text": line,
                          "size": max((c.get("s", 0) for c in rows[yk]), default=0)})
    return lines


def merge_positioned_chars(lines_by_y, chars, min_y=0):
    """Merge one virtualized DOM window into a document-positioned line map."""
    for line in chars_to_lines(chars):
        y = round(line["y"] / 3) * 3
        if y < min_y:
            continue
        text = line["text"]
        previous = lines_by_y.get(y, "")
        # Overlapping virtualization windows repeat complete rows.  If a row is
        # caught during a boundary update, prefer the more complete rendering.
        if len(text) > len(previous):
            lines_by_y[y] = text


def normalize_para(text):
    return re.sub(r'\s+', '', text)


def text_grams(text, size=3):
    n = normalize_para(text)
    if len(n) < size:
        return set()
    return {n[i:i + size] for i in range(len(n) - size + 1)}


def text_already_seen(text, seen_grams, threshold=0.85):
    """整页重放产生的文本，几乎与已见内容完全相同（3-gram 重合度 ≈100%）。

    阈值必须取得高：0.6 会把“大部分是旧内容、但含新句子”的边界页也误杀，
    造成正文缺口（实测丢失 “未来AIOS会越来越…” 这类句尾）。
    """
    grams = text_grams(text)
    if len(grams) < 4 or not seen_grams:
        return False
    return len(grams & seen_grams) / len(grams) >= threshold


def build_page_blocks(chars, images, canvas_rects, seen_imgs):
    """把一次渲染(可能双页)拆成有序块列表: [{type:'text'/'img', ...}]
       文字行和图片按屏幕 y 交错；左页整页在前，右页在后。"""
    blocks = []
    pages = split_spread_by_canvas(chars, canvas_rects) or split_spread(chars)

    # 判定左右 canvas
    rects = sorted(canvas_rects, key=lambda r: r["left"])
    left_rect = rects[0] if rects else {"top": 0, "left": 0}
    right_rect = rects[1] if len(rects) > 1 else left_rect
    mid_x = (left_rect["left"] + right_rect["left"]) / 2 + 180 if len(rects) > 1 else 99999

    # 图片按左右分组
    left_imgs = [im for im in images if im["left"] < mid_x]
    right_imgs = [im for im in images if im["left"] >= mid_x]

    def emit_page(page_chars, page_rect, page_imgs):
        lines = chars_to_lines(page_chars)
        items = []
        for ln in lines:
            items.append(("text", page_rect["top"] + ln["y"], ln))
        for im in page_imgs:
            if im["src"] in seen_imgs:
                continue
            items.append(("img", im["top"], im))
        items.sort(key=lambda t: t[1])
        for typ, _y, payload in items:
            if typ == "text":
                blocks.append({"type": "text", "text": payload["text"],
                               "size": payload.get("size", 0)})
            else:
                seen_imgs.add(payload["src"])
                blocks.append({"type": "img", "src": payload["src"],
                                "w": payload["w"], "h": payload["h"]})

    if len(pages) == 2:
        emit_page(pages[0], left_rect, left_imgs)
        emit_page(pages[1], right_rect, right_imgs)
    else:
        # 单页：图片全归这页，仍按 y 排
        emit_page(pages[0], left_rect, left_imgs + right_imgs)
    return blocks


def split_spread_by_canvas(chars, canvas_rects):
    """按 canvas 的 x 区间把字符分到各页（比 y 重置点更稳）。

    双页布局是两个并排 canvas；标题页与正文页同屏时，y 重置点检测会失效，
    两页字符会被按 x 交织进同一行（标题字被织进正文）。用 canvas 的物理区间
    切分可避免这种错行。
    """
    rects = sorted(canvas_rects, key=lambda r: r["left"])
    if len(rects) < 2:
        return None
    centers = [r["left"] + r["w"] / 2 for r in rects]
    buckets = [[] for _ in rects]
    for c in chars:
        idx = min(range(len(rects)), key=lambda i: abs(c["x"] - centers[i]))
        buckets[idx].append(c)
    if not all(buckets):
        return None
    return buckets


def img_filename(url, ch_idx, seq):
    ext = "jpg"
    m = re.search(r'\.(jpg|jpeg|png|gif|webp)', url.lower())
    if m:
        ext = m.group(1).replace("jpeg", "jpg")
    return f"ch{ch_idx:04d}_img{seq:02d}.{ext}"


COVER_SIZE_PREFIX_RE = re.compile(r'^(?:t\d+|s)_')


def best_cover_url(url):
    """封面高清变体：DOM 里是 t6_（250×359），t9_ 是 428×614。"""
    if not url:
        return ""
    path, _, name = url.rpartition('/')
    if not path:
        return url
    return f"{path}/t9_{COVER_SIZE_PREFIX_RE.sub('', name)}"


def render_chapter_md(ch_title, blocks, ch_idx, headings=()):
    """把有序块渲染成 Markdown：文字行合并成段落，图片就地插入

    目录里已知的标题行单独成段（加粗）：canvas 行没有句末标点，不识别的话
    会被段落合并逻辑吞进后文，造成“标题粘在段首”。
    """
    out = [f"# {ch_title}\n"]
    para = []
    img_records = []
    img_seq = 0
    self_key = normalize_title(ch_title)

    def flush_para():
        nonlocal para
        if not para:
            return
        # 合并 canvas 断行为自然段：上一行不以句末标点结尾则接续
        merged = []  # [(text, is_heading)]
        prev_heading = False
        for line in para:
            text = line.strip()
            if not text:
                continue
            key = normalize_title(text)
            if key == self_key:
                continue  # 章节标题已作为一级标题输出
            is_heading = key in headings and len(text) <= 40
            if (merged and not is_heading and not prev_heading
                    and merged[-1][0][-1] not in SENTENCE_END):
                merged[-1][0] += text
            else:
                merged.append([text, is_heading])
            prev_heading = is_heading
        for text, is_heading in merged:
            text = text.strip()
            if not text:
                continue
            head, rest = split_heading_prefix(text, headings)
            if head is None and normalize_title(text) in headings:
                head, rest = text, ""
            if head is not None:
                # 章节标题已作为一级标题输出，不再重复
                if normalize_title(head) != self_key:
                    out.append(f"**{head}**")
                if rest.strip() and normalize_title(rest) != self_key:
                    out.append(rest.strip())
                continue
            if normalize_title(text) == self_key:
                continue
            out.append(text)
        para = []

    for b in blocks:
        if b["type"] == "text":
            para.append(b["text"])
        else:
            flush_para()
            img_seq += 1
            fname = img_filename(b["src"], ch_idx, img_seq)
            out.append(f"![图](images/{fname})")
            img_records.append({"url": b["src"], "file": fname})
    flush_para()

    body = "\n\n".join(out) + "\n"
    return body, img_records


def save_chapter(ch_title, blocks, ch_idx, md_dir, raw_dir, headings=()):
    body, img_records = render_chapter_md(ch_title, blocks, ch_idx, headings)
    text_len = sum(len(b["text"]) for b in blocks if b["type"] == "text")
    with open(os.path.join(md_dir, f"{ch_idx:04d}.md"), "w") as f:
        f.write(body)
    with open(os.path.join(raw_dir, f"{ch_idx:04d}.json"), "w") as f:
        json.dump({"title": ch_title, "images": img_records, "text_len": text_len},
                  f, ensure_ascii=False)
    return text_len, img_records
