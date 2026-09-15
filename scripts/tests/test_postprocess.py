"""回归测试：后处理（拆分自 test_navigation_retry.py；8 项，断言逐字保留）。"""
import export_precise


def test_stitch_chapter_boundaries_rejoins_split_sentence(tmp_path) -> None:
    """跨章被切开的句子要合回上一章。"""
    a = tmp_path / "0001.md"
    b = tmp_path / "0002.md"
    a.write_text("# 第一章\n\n前面都写完了。未来领导力的核心就是如何让整个组织持续形\n",
                 encoding="utf-8")
    b.write_text("# 第五章\n\n成高质量认知。这是一个变化非常大的目标。\n",
                 encoding="utf-8")

    fixed = export_precise.stitch_chapter_boundaries(str(tmp_path))

    assert len(fixed) == 1
    assert "持续形成高质量认知。" in a.read_text(encoding="utf-8")
    assert b.read_text(encoding="utf-8").startswith("# 第五章")
    assert "成高质量认知。" not in b.read_text(encoding="utf-8")


def test_stitch_chapter_boundaries_ignores_spilled_heading(tmp_path) -> None:
    """末段整段就是溢出的标题时，不得把下一章首句搬过来。"""
    a = tmp_path / "0001.md"
    b = tmp_path / "0002.md"
    a.write_text("# 第三章\n\n前一节正文。\n\n“千人一面”：标准化的工业时代\n",
                 encoding="utf-8")
    b.write_text("# 从“千人一面”起\n\n工业时代最重要的成就，是人类第一次解决供给问题。\n",
                 encoding="utf-8")

    assert export_precise.stitch_chapter_boundaries(str(tmp_path)) == []
    assert "工业时代最重要的成就" in b.read_text(encoding="utf-8")


def test_stitch_chapter_boundaries_skips_when_next_chapter_has_one_line(tmp_path) -> None:
    """搬完会把下一章抽空时，不动。"""
    a = tmp_path / "0001.md"
    b = tmp_path / "0002.md"
    a.write_text("# 第一章\n\n写完了。下一句没有结尾\n", encoding="utf-8")
    b.write_text("# 第二章\n\n只有这一句。\n", encoding="utf-8")

    assert export_precise.stitch_chapter_boundaries(str(tmp_path)) == []


def test_stitch_chapter_boundaries_moves_whole_head_paragraph(tmp_path) -> None:
    """下一章首段整段都是续句时，整段搬回上一章（下一章还有其它正文）。"""
    a = tmp_path / "0001.md"
    b = tmp_path / "0002.md"
    a.write_text("# 第二章\n\n正文写完了。他们没有复杂层级，没有严格岗位\n",
                 encoding="utf-8")
    b.write_text("# 第三章\n\n边界，甚至不是通过正式流程推动工作。\n\n后续还有正文。\n",
                 encoding="utf-8")

    fixed = export_precise.stitch_chapter_boundaries(str(tmp_path))

    assert len(fixed) == 1
    assert "没有严格岗位边界，甚至不是通过正式流程推动工作。" in a.read_text(encoding="utf-8")
    assert b.read_text(encoding="utf-8").startswith("# 第三章")
    assert "后续还有正文。" in b.read_text(encoding="utf-8")


def test_stitch_chapter_boundaries_allows_plain_unfinished_sentence(tmp_path) -> None:
    """残句整段无句末标点、但不像标题（无“：”）时，仍应合拢。"""
    a = tmp_path / "0001.md"
    b = tmp_path / "0002.md"
    a.write_text("# 第二章\n\n未来领导力的核心就是如何让整个组织持续形\n", encoding="utf-8")
    b.write_text("# 第五章\n\n成高质量认知。这是一个变化非常大的目标。\n", encoding="utf-8")

    fixed = export_precise.stitch_chapter_boundaries(str(tmp_path))

    assert len(fixed) == 1
    assert "持续形成高质量认知。" in a.read_text(encoding="utf-8")


def test_move_trailing_headings_pushes_spilled_title_forward(tmp_path) -> None:
    """上一章末尾溢出的小节标题应移到下一章开头。"""
    a = tmp_path / "0001.md"
    b = tmp_path / "0002.md"
    a.write_text("# 第三章\n\n前一节正文写完了。\n\n“千人一面”：标准化的工业时代\n",
                 encoding="utf-8")
    b.write_text("# 下一节\n\n工业时代最重要的成就，是人类第一次解决了大规模供给问题。机器、能源、标准化生产和现代供应链的出现，让整个社会第一次有能力以极低成本向大众持续提供商品。\n",
                 encoding="utf-8")

    moved = export_precise.move_trailing_headings(str(tmp_path))

    assert len(moved) == 1
    assert "标准化的工业时代" not in a.read_text(encoding="utf-8")
    body = b.read_text(encoding="utf-8")
    assert body.index("标准化的工业时代") < body.index("工业时代最重要的成就")


def test_build_merged_md_keeps_chapter_order(tmp_path) -> None:
    md_dir = tmp_path / "chapters"
    md_dir.mkdir()
    (md_dir / "0001.md").write_text("# 一\n\n甲。\n", encoding="utf-8")
    (md_dir / "0002.md").write_text("# 二\n\n乙。\n", encoding="utf-8")

    merged, count = export_precise.build_merged_md(
        str(tmp_path), str(md_dir), "书名", "作者")
    text = open(merged, encoding="utf-8").read()

    assert count == 2
    assert text.startswith("# 书名\n\n**作者**")
    assert text.index("# 一") < text.index("# 二")


def test_collapse_doubled_line_folds_repeated_text() -> None:
    """同一行被画两遍（A+A）或逐字叠印（AABB）时要折叠回一遍。"""
    once = "版权信息书名：智能：AI时代的商业、组织与战略的本质"

    assert export_precise.collapse_doubled_line(once + once) == once
    assert export_precise.collapse_doubled_line("第第三三章章智智能能战战略略") == "第三章智能战略"
    assert export_precise.collapse_doubled_line(once) == once
    assert export_precise.collapse_doubled_line("短句短句") == "短句短句"
