#!/usr/bin/env python3
"""章节切分判据：标题归一化与目录清洗、标题前缀拆分、字号识别（find_title_line）。

纯函数模块——不依赖其他内部模块（依赖方向见设计档 §2.4.8）。
"""
import json
import re

TITLE_NOISE_RE = re.compile(r'[\s：:，,。、·—\-]+')


def normalize_title(text: str) -> str:
    """标题判据：忽略空白与标点差异（canvas 行与目录项的空格可能不同）。"""
    return TITLE_NOISE_RE.sub('', text).strip()


def index_after_normalized(text: str, count: int) -> int:
    """返回原文中前 count 个规范化字符之后的下标（用于切出标题部分）。"""
    seen = 0
    for i, ch in enumerate(text):
        if not TITLE_NOISE_RE.match(ch):
            seen += 1
            if seen == count:
                return i + 1
    return len(text)


def split_heading_prefix(text: str, headings):
    """拆开“标题+正文”粘在一起的段首；返回 (标题, 余下正文)，无匹配则 (None, text)。

    标题页的标题字号大、会换行成多行，段落合并后与正文首句连在一起，
    按行判断已经无能为力，只能在合并结果上做前缀匹配。
    """
    norm = normalize_title(text)
    for key in headings:
        if len(key) >= 6 and len(key) <= len(norm) and norm.startswith(key):
            cut = index_after_normalized(text, len(key))
            return text[:cut], text[cut:]
    return None, text


def clean_catalog_title(text: str) -> str:
    """目录项文本可能带 '+书签'、'当前读到 3%' 等装饰，去掉后再作为标题判据。"""
    cleaned = re.sub(r'[+＋]?\s*(书签|想法|笔记|已读完)\s*$', '', text.strip())
    cleaned = re.sub(r'当前读到\s*\d+(?:\.\d+)?\s*%\s*$', '', cleaned).strip()
    return cleaned.strip()


def load_heading_titles(catalog_path: str) -> set:
    """从目录缓存读已知标题（归一化），用于识别正文里的标题行。"""
    try:
        with open(catalog_path) as f:
            titles = json.load(f)
    except Exception:
        return set()
    return {normalize_title(clean_catalog_title(t)) for t in titles if t.strip()}


def find_title_line(blocks, headings, ratio=1.25):
    """标题页检测：本页字号明显大于正文的行 = 标题，命中目录标题则认定新章节扉页。

    阅读器的顶栏标题会滞后一页更新：章节扉页出现时它往往还显示旧章节名，
    于是扉页内容会被算进上一章。这里用字号（实测标题 28.8px vs 正文 18px）
    直接识别扉页，作为章节切分点。
    """
    if not headings:
        return None
    text_blocks = [b for b in blocks
                   if b["type"] == "text" and b.get("text", "").strip()]
    if not text_blocks:
        return None
    weight = {}
    for b in text_blocks:
        size = b.get("size") or 0
        if size:
            weight[size] = weight.get(size, 0) + len(b["text"])
    if not weight:
        return None
    # 正文字号 = 字符数最多的字号（正文占绝大多数；页眉小字不会拉低基准）
    body_size = max(weight, key=weight.get)
    if max(weight) > body_size * ratio:
        candidate = "".join(b["text"].strip() for b in text_blocks
                            if (b.get("size") or 0) >= body_size * ratio)
    else:
        # 整页字号一致：可能整页就是标题（或全是正文），用目录匹配兜底
        candidate = "".join(b["text"].strip() for b in text_blocks)
    if not candidate:
        return None
    norm_run = normalize_title(candidate)
    for key in headings:
        if len(key) >= 6 and norm_run.startswith(key):
            return candidate[:index_after_normalized(candidate, len(key))].strip()
    return None
