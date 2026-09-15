"""回归测试：切章与渲染（拆分自 test_navigation_retry.py；8 项，断言逐字保留）。"""
import export_precise


def test_render_chapter_md_keeps_catalog_heading_separate() -> None:
    """A known section title must not be glued onto the paragraph after it."""
    blocks = [
        {"type": "text", "text": "AI发展第一阶段：基础设施建设基本成形"},
        {"type": "text", "text": "工业时代最重要的基础设施，是能源；"},
    ]
    headings = {export_precise.normalize_title("AI发展第一阶段：基础设施建设基本成形")}

    body, _imgs = export_precise.render_chapter_md("第一章", blocks, 3, headings)

    assert "**AI发展第一阶段：基础设施建设基本成形**" in body
    assert "**AI发展第一阶段：基础设施建设基本成形**工业时代" not in body
    assert "\n\n工业时代最重要的基础设施，是能源；\n" in body


def test_render_chapter_md_drops_chapter_title_with_spacing_difference() -> None:
    """Canvas draws the chapter title without the header's spacing."""
    blocks = [
        {"type": "text", "text": "第三章一人千面：智能商业范式大变革"},
        {"type": "text", "text": "这几年，很多人都在感叹，做企业变得越来越难了。"},
    ]

    body, _imgs = export_precise.render_chapter_md(
        "第三章 一人千面：智能商业范式大变革", blocks, 14)

    assert body.count("第三章") == 1
    assert "**" not in body
    assert "这几年，很多人都在感叹" in body


def test_best_cover_url_upgrades_size_prefix() -> None:
    url = "https://cdn.weread.qq.com/weread/cover/64/hash/t6_hash123.jpg"

    assert export_precise.best_cover_url(url) == \
        "https://cdn.weread.qq.com/weread/cover/64/hash/t9_hash123.jpg"
    assert export_precise.best_cover_url("") == ""


def test_find_title_line_uses_font_size_and_catalog_titles() -> None:
    """标题页的字号明显大于正文，且文本能前缀匹配目录标题。"""
    headings = {export_precise.normalize_title("第五章 智能战略：愿景驱动的战略生成")}
    blocks = [
        {"type": "text", "text": "新领导力这一整章的最后一段正文，字号是 18px。", "size": 18},
        {"type": "text", "text": "第五章", "size": 28.8},
        {"type": "text", "text": "智能战略：愿景驱动的战略生成", "size": 28.8},
        {"type": "text", "text": "这也许正是AI时代组织演化最深刻的地方。", "size": 18},
    ]

    hit = export_precise.find_title_line(blocks, headings)

    assert hit == "第五章智能战略：愿景驱动的战略生成"
    assert export_precise.find_title_line(blocks, set()) is None
    # 字号不突出时不应误判
    flat = [{"type": "text", "text": b["text"], "size": 18} for b in blocks]
    assert export_precise.find_title_line(flat, headings) is None


def test_chars_to_lines_carries_font_size() -> None:
    chars = [{"t": "标", "x": 10, "y": 100, "s": 28.8},
             {"t": "题", "x": 40, "y": 100, "s": 28.8},
             {"t": "正", "x": 10, "y": 140, "s": 18}]

    lines = export_precise.chars_to_lines(chars)

    assert [(ln["text"], ln["size"]) for ln in lines] == [("标题", 28.8), ("正", 18)]


def test_split_heading_prefix_separates_wrapped_title_from_body() -> None:
    headings = {export_precise.normalize_title("第三章 一人千面：智能商业范式大变革")}
    text = "第三章一人千面：智能商业范式大变革这几年，很多人都在感叹。"

    head, rest = export_precise.split_heading_prefix(text, headings)

    assert head == "第三章一人千面：智能商业范式大变革"
    assert rest == "这几年，很多人都在感叹。"
    assert export_precise.split_heading_prefix("毫不相关的正文开头。", headings) == (
        None, "毫不相关的正文开头。")


def test_render_chapter_md_drops_wrapped_chapter_title() -> None:
    """标题换行成两行后与正文粘在一起，仍要拆开并丢掉重复的章节标题。"""
    blocks = [
        {"type": "text", "text": "第三章"},
        {"type": "text", "text": "一人千面：智能商业范式大变革"},
        {"type": "text", "text": "这几年，很多人都在感叹，做企业变得越来越难了。"},
    ]
    headings = {export_precise.normalize_title("第三章 一人千面：智能商业范式大变革")}

    body, _imgs = export_precise.render_chapter_md(
        "第三章 一人千面：智能商业范式大变革", blocks, 14, headings)

    assert body.count("第三章") == 1  # 只剩一级标题
    assert "这几年，很多人都在感叹" in body
    assert "**" not in body


def test_save_chapter_keeps_empty_catalog_page(tmp_path) -> None:
    """A title-only copyright page must still be represented in the export."""
    markdown_dir = tmp_path / "chapters"
    raw_dir = tmp_path / "raw"
    markdown_dir.mkdir()
    raw_dir.mkdir()

    text_len, images = export_precise.save_chapter(
        "版权信息",
        [],
        1,
        str(markdown_dir),
        str(raw_dir),
    )

    assert text_len == 0
    assert images == []
    assert (markdown_dir / "0001.md").read_text(encoding="utf-8") == "# 版权信息\n\n"
    assert (raw_dir / "0001.json").exists()
