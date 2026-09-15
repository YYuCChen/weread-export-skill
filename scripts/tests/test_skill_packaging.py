"""包级回归：包结构 / 路径解析 / 节流常量 / 退出码 / 报告行为（拆分见设计档 §2.2）。"""
import json
import os
import re
from pathlib import Path

import export_precise
import weread_common as wc

SKILL_DIR = Path(__file__).resolve().parent.parent.parent
DESC_LINE = "把微信读书的一本书导出为 Markdown / EPUB / PDF 三种格式，并附一份可复核的核验报告。"


def test_pack_has_four_parts():
    for part in ("SKILL.md", "README.md", "references/playbook.md", "scripts"):
        assert (SKILL_DIR / part).exists(), part


def test_skill_md_description_line_is_loadable():
    """加载器模拟：前 400 字符内的首个非标题行 = 设计档 §2.3 文案。"""
    head = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")[:400]
    lines = [ln.strip() for ln in head.splitlines()]
    desc = next((ln for ln in lines if ln and not ln.startswith("#")), "")
    assert desc == DESC_LINE
    assert 0 < len(desc) <= 120


def test_parse_book_id_strips_chapter_token_suffix():
    url = "https://weread.qq.com/web/reader/dcc32bc0813abbeeag013b75k1c2d3e4f5"
    assert wc.parse_book_id(url) == "dcc32bc0813abbeeag013b75"
    assert wc.parse_book_id("dcc32bc0813abbeeag013b75") == "dcc32bc0813abbeeag013b75"
    assert wc.parse_book_id("legacy-id-42") == "legacy-id-42"


def test_throttle_matches_design_defaults():
    """节流单点 = weread_common.THROTTLE，且逐项等于设计档 §2.8（不加严）。"""
    assert wc.THROTTLE == {
        "page_settle_after_turn": 1.0,
        "stable_poll": 0.5,
        "stable_timeout": 8.0,
        "page_capture_settle": 0.3,
        "dom_scroll_settle": 0.2,
        "repaint_settle": 0.5,
        "session_restart_wait": 3.0,
        "download_workers": 8,
        "download_retries": 3,
        "download_timeout": 20,
    }
    assert export_precise.THROTTLE is wc.THROTTLE


def test_sleep_scale_defaults_to_one(monkeypatch):
    monkeypatch.delenv("WEREAD_SLEEP_SCALE", raising=False)
    assert wc.sleep_scale() == 1.0


def test_state_root_layout_under_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("WEREAD_EXPORT_HOME", str(tmp_path))
    assert wc.profile_dir() == os.path.join(str(tmp_path), "profile")
    assert wc.book_dir("abc") == os.path.join(str(tmp_path), "books", "abc")
    assert wc.runs_log() == os.path.join(str(tmp_path), "runs.log")


def test_log_run_writes_tsv_line(monkeypatch, tmp_path):
    """runs.log 行格式 = ISO8601 时间戳 + event + book_id + note。"""
    monkeypatch.setenv("WEREAD_EXPORT_HOME", str(tmp_path))
    wc.log_run("start", "b1", "note")
    line = (tmp_path / "runs.log").read_text(encoding="utf-8").strip()
    stamp, event, book_id, note = line.split("\t")
    assert (event, book_id, note) == ("start", "b1", "note")
    assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", stamp)


def test_exit_codes_and_signal_markers():
    """退出码六态全序 0–7；阻断信号 →（标记行, 退出码）映射稳定。"""
    assert [wc.EXIT_OK, wc.EXIT_USAGE, wc.EXIT_NEED_LOGIN, wc.EXIT_BLOCKED,
            wc.EXIT_STRUCTURE, wc.EXIT_VERIFY_FAIL, wc.EXIT_MISSING_INPUT,
            wc.EXIT_BUILD_FAIL] == list(range(8))
    assert wc.SIGNAL_MARKERS[wc.SIGNAL_NEED_LOGIN] == ("⛔ 登录失效", 2)
    assert wc.SIGNAL_MARKERS[wc.SIGNAL_BLOCKED] == ("⛔ 访问受限", 3)


def test_classify_login_url_and_page_text():
    """受阻信号分类函数（登录失效 / 受限页 fixture）。"""
    assert wc.classify_login_url("https://weread.qq.com/web/login") is True
    assert wc.classify_login_url("https://weread.qq.com/web/reader/x") is False
    assert wc.classify_page_text("抱歉，本书需要去 App 阅读") is True
    assert wc.classify_page_text("正常的一页正文") is False


def _write_product(tmp_path, chapters, platform):
    book_dir = tmp_path / "books" / "dcc32bc0813abbeeag013b75"
    (book_dir / "chapters").mkdir(parents=True)
    for name, body in chapters.items():
        (book_dir / "chapters" / name).write_text(body, encoding="utf-8")
    (book_dir / "_platform_chapterinfo.json").write_text(
        json.dumps({"data": [{"updated": platform}]}, ensure_ascii=False),
        encoding="utf-8")
    return book_dir


def test_verify_report_flags_missing_chapter(monkeypatch, tmp_path):
    """平台有、本地缺 → 报告记录缺章 + 未达标 + 退出码 5。"""
    monkeypatch.setenv("WEREAD_EXPORT_HOME", str(tmp_path))
    book_dir = _write_product(
        tmp_path,
        {"0001.md": "# 第一章\n\n正文。\n"},
        [{"chapterIdx": 1, "title": "第一章", "wordCount": 3, "level": 1},
         {"chapterIdx": 2, "title": "第二章", "wordCount": 5, "level": 1}])
    import verify_export

    code = verify_export.main("dcc32bc0813abbeeag013b75")

    assert code == wc.EXIT_VERIFY_FAIL
    report = (book_dir / "_verify_report.txt").read_text(encoding="utf-8")
    assert "平台有、本地缺的章节: ['第二章']" in report
    assert "核验结论: ⛔ 未达标" in report


def test_verify_report_passes_clean_product(monkeypatch, tmp_path):
    """干净产物 → 四字段达标 + 结论行 + 退出码 0 + --report-out 副本。"""
    monkeypatch.setenv("WEREAD_EXPORT_HOME", str(tmp_path))
    book_dir = _write_product(
        tmp_path,
        {"0001.md": "# 第一章\n\n正文。\n"},
        [{"chapterIdx": 1, "title": "第一章", "wordCount": 3, "level": 1}])
    report_out = tmp_path / "delivery" / "核验报告.txt"
    import verify_export

    code = verify_export.main("dcc32bc0813abbeeag013b75", None, str(report_out))

    assert code == wc.EXIT_OK
    report = report_out.read_text(encoding="utf-8")
    for field in ("平台有、本地缺的章节: 无", "重复段落组数: 0",
                  "内容完全重复的章节: 无", "失效引用: 无", "核验结论: ✅ 达标"):
        assert field in report


def test_verify_report_missing_product_dir(monkeypatch, tmp_path):
    """产物目录不存在 → 明确提示 + 退出码 6。"""
    monkeypatch.setenv("WEREAD_EXPORT_HOME", str(tmp_path / "nowhere"))
    import verify_export

    assert verify_export.main("dcc32bc0813abbeeag013b75") == wc.EXIT_MISSING_INPUT
