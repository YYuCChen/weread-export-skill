"""图片包回归：安全展开 TAR，并把有序图片引用写回漫画章节。"""
import io
import json
import tarfile

import download_images


def make_tar():
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w") as archive:
        for name, content in (("../unsafe/2.png", b"\x89PNG\r\n\x1a\n" + b"b" * 700),
                              ("pages/1.jpg", b"\xff\xd8\xff" + b"a" * 700)):
            info = tarfile.TarInfo(name)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
    return out.getvalue()


def test_extract_archive_ignores_member_paths_and_keeps_order(tmp_path):
    files = download_images.extract_image_archive(
        make_tar(), "ch0003_arc001", str(tmp_path))

    assert files == ["ch0003_arc001_img0001.png", "ch0003_arc001_img0002.jpg"]
    assert sorted(p.name for p in tmp_path.iterdir() if not p.name.startswith(".")) == sorted(files)
    assert not (tmp_path.parent / "unsafe").exists()


def test_download_archive_replaces_chapter_marker(monkeypatch, tmp_path):
    raw_dir = tmp_path / "raw"
    chapters = tmp_path / "chapters"
    images = tmp_path / "images"
    raw_dir.mkdir(); chapters.mkdir(); images.mkdir()
    record = {"kind": "archive", "url": "https://example.com/pages.tar",
              "archive_id": "ch0001_arc001"}
    raw_file = raw_dir / "0001.json"
    raw_file.write_text(json.dumps({"images": [record]}), encoding="utf-8")
    chapter = chapters / "0001.md"
    chapter.write_text("# 漫画\n\n<!-- WEREAD_IMAGE_ARCHIVE:ch0001_arc001 -->\n",
                       encoding="utf-8")
    monkeypatch.setattr(download_images, "_read_url_limited",
                        lambda *_args, **_kwargs: make_tar())

    status, count = download_images.download_archive(
        record, str(raw_file), str(images), timeout=1)

    assert (status, count) == ("ok", 2)
    text = chapter.read_text(encoding="utf-8")
    assert "WEREAD_IMAGE_ARCHIVE" not in text
    assert "![图](images/ch0001_arc001_img0001.png)" in text
    assert "![图](images/ch0001_arc001_img0002.jpg)" in text
