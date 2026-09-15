#!/usr/bin/env python3
"""独立图片下载器：强制 IPv4 + 并发 + 重试。读 raw/*.json 里的图片 URL 下到 images/。

可重复运行：已下载的图片自动跳过（失败补齐用同一条命令）。
并发参数取自 weread_common.THROTTLE（单一来源）；socket IPv4 补丁仅在
下载期间生效（入口打补丁、finally 还原——无 import 副作用）。
"""
import glob
import io
import json
import os
import socket
import sys
import tarfile
import tempfile
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from pathlib import Path

import weread_common as wc

HEADERS = {"Referer": "https://weread.qq.com/", "User-Agent": "Mozilla/5.0"}
MAX_ARCHIVE_BYTES = 200 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 5000
MAX_ARCHIVE_IMAGE_BYTES = 50 * 1024 * 1024
MAX_ARCHIVE_UNPACKED_BYTES = 1024 * 1024 * 1024
IMAGE_MAGIC = ((b'\xff\xd8\xff', '.jpg'), (b'\x89PNG\r\n\x1a\n', '.png'),
               (b'GIF8', '.gif'), (b'RIFF', '.webp'))


@contextmanager
def ipv4_only():
    """下载期间强制 IPv4（macOS 上 IPv6 路由不通会导致每次连接卡 ~120s）。"""
    orig = socket.getaddrinfo
    socket.getaddrinfo = lambda *a, **k: [x for x in orig(*a, **k)
                                         if x[0] == socket.AF_INET]
    try:
        yield
    finally:
        socket.getaddrinfo = orig


def download_one(url, fpath, retries=wc.THROTTLE["download_retries"],
                 timeout=wc.THROTTLE["download_timeout"]):
    if os.path.exists(fpath) and os.path.getsize(fpath) > 1000:
        return "skip"
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            raw = urllib.request.urlopen(req, timeout=timeout).read()
            if len(raw) > 500:
                with open(fpath, "wb") as f:
                    f.write(raw)
                return "ok"
        except Exception as e:
            if attempt == retries - 1:
                return f"fail:{e}"
            time.sleep(1.5)
    return "fail:empty"


def collect_tasks(raw_dir, img_dir):
    tasks, archives = [], []
    for jf in sorted(glob.glob(os.path.join(raw_dir, "*.json"))):
        with open(jf, encoding="utf-8") as f:
            for im in json.load(f).get("images", []):
                if im.get("kind") == "archive":
                    archives.append((im, jf))
                else:
                    tasks.append((im["url"], os.path.join(img_dir, im["file"])))
    return tasks, archives


def _read_url_limited(url, limit, timeout):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        length = response.headers.get("Content-Length")
        if length and int(length) > limit:
            raise ValueError(f"压缩包超过上限 {limit // 1024 // 1024}MB")
        raw = response.read(limit + 1)
    if len(raw) > limit:
        raise ValueError(f"压缩包超过上限 {limit // 1024 // 1024}MB")
    return raw


def _image_extension(raw):
    for signature, extension in IMAGE_MAGIC:
        if raw.startswith(signature):
            return extension
    return ""


def extract_image_archive(raw, archive_id, img_dir):
    """安全展开 TAR：不使用成员路径，只按顺序写入已识别的图片字节。"""
    files, total = [], 0
    with tempfile.TemporaryDirectory(prefix=f".{archive_id}-", dir=img_dir) as temp_dir:
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:*") as archive:
            members = archive.getmembers()
            if len(members) > MAX_ARCHIVE_MEMBERS:
                raise ValueError(f"压缩包文件数超过上限 {MAX_ARCHIVE_MEMBERS}")
            for member in members:
                if not member.isfile() or member.size <= 0:
                    continue
                if member.size > MAX_ARCHIVE_IMAGE_BYTES:
                    raise ValueError("压缩包内单张图片超过 50MB")
                total += member.size
                if total > MAX_ARCHIVE_UNPACKED_BYTES:
                    raise ValueError("压缩包解压后超过 1GB")
                stream = archive.extractfile(member)
                content = stream.read(MAX_ARCHIVE_IMAGE_BYTES + 1) if stream else b""
                ext = _image_extension(content)
                if not ext:
                    continue
                name = f"{archive_id}_img{len(files) + 1:04d}{ext}"
                with open(os.path.join(temp_dir, name), "wb") as f:
                    f.write(content)
                files.append(name)
        if not files:
            raise ValueError("压缩包内没有可识别的图片")
        for name in files:
            os.replace(os.path.join(temp_dir, name), os.path.join(img_dir, name))
        manifest = os.path.join(img_dir, f".{archive_id}.json")
        with open(manifest, "w", encoding="utf-8") as f:
            json.dump(files, f, ensure_ascii=False)
    return files


def _install_archive_references(record, raw_json_path, filenames):
    chapter_path = os.path.join(os.path.dirname(os.path.dirname(raw_json_path)),
                                "chapters", Path(raw_json_path).stem + ".md")
    marker = f"<!-- WEREAD_IMAGE_ARCHIVE:{record['archive_id']} -->"
    if not os.path.exists(chapter_path):
        raise ValueError(f"找不到章节文件 {chapter_path}")
    text = Path(chapter_path).read_text(encoding="utf-8")
    replacement = "\n\n".join(f"![图](images/{name})" for name in filenames)
    if marker in text:
        Path(chapter_path).write_text(text.replace(marker, replacement), encoding="utf-8")


def download_archive(record, raw_json_path, img_dir, timeout):
    manifest = os.path.join(img_dir, f".{record['archive_id']}.json")
    try:
        existing = json.loads(Path(manifest).read_text(encoding="utf-8"))
    except Exception:
        existing = []
    if existing and all(os.path.isfile(os.path.join(img_dir, name)) for name in existing):
        _install_archive_references(record, raw_json_path, existing)
        return "skip", len(existing)
    raw = _read_url_limited(record["url"], MAX_ARCHIVE_BYTES, timeout)
    files = extract_image_archive(raw, record["archive_id"], img_dir)
    _install_archive_references(record, raw_json_path, files)
    return "ok", len(files)


def download_all(raw_dir, img_dir, workers=wc.THROTTLE["download_workers"],
                 retries=wc.THROTTLE["download_retries"],
                 timeout=wc.THROTTLE["download_timeout"]) -> dict:
    """并发下载 raw/ 名单里的全部图片；返回 {'ok','skip','fail','failures','elapsed'}。"""
    os.makedirs(img_dir, exist_ok=True)
    tasks, archives = collect_tasks(raw_dir, img_dir)
    stats = {"ok": 0, "skip": 0, "fail": 0, "failures": [], "archives": 0,
             "archive_images": 0, "elapsed": 0.0}
    if not tasks and not archives:
        print("  (无图片)")
        return stats
    print(f"  {len(tasks)} 张普通图片，{len(archives)} 个图片包，"
          f"{workers} 线程并发下载（强制IPv4）...")
    t0 = time.time()
    with ipv4_only():
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(download_one, u, f, retries, timeout): f
                    for u, f in tasks}
            done = 0
            for fut in as_completed(futs):
                result = fut.result()
                done += 1
                name = os.path.basename(futs[fut])
                if result == "ok":
                    stats["ok"] += 1
                elif result == "skip":
                    stats["skip"] += 1
                else:
                    stats["fail"] += 1
                    stats["failures"].append(f"{name}: {result}")
                    print(f"  ⚠️  {name}: {result}")
                if done % 50 == 0:
                    print(f"  {done}/{len(tasks)}  (ok={stats['ok']} "
                          f"skip={stats['skip']} fail={stats['fail']})  "
                          f"{time.time() - t0:.0f}s")
        for record, raw_json_path in archives:
            try:
                status, count = download_archive(record, raw_json_path, img_dir, timeout)
                stats[status] += 1
                stats["archives"] += 1
                stats["archive_images"] += count
            except Exception as exc:
                stats["fail"] += 1
                stats["failures"].append(
                    f"{record.get('archive_id', '图片包')}: fail:{exc}")
    stats["elapsed"] = time.time() - t0
    return stats


def main(book_id) -> int:
    raw_dir = os.path.join(wc.book_dir(book_id), "raw")
    img_dir = os.path.join(wc.book_dir(book_id), "images")
    if not os.path.isdir(raw_dir):
        print(f"⛔ 输入缺失：找不到 {raw_dir}（先跑预检 / 导出）")
        return wc.EXIT_MISSING_INPUT
    stats = download_all(raw_dir, img_dir)
    print(f"\n✅ 完成: ok={stats['ok']} skip={stats['skip']} "
          f"fail={stats['fail']}  耗时 {stats['elapsed']:.0f}s")
    if stats["archives"]:
        print(f"   图片包: {stats['archives']} 个，共展开 {stats['archive_images']} 张图")
    print(f"   图片目录: {img_dir}")
    if stats["failures"]:
        print("  ⛔ 下载失败清单:")
        for item in stats["failures"]:
            print(f"    {item}")
        return wc.EXIT_USAGE
    return wc.EXIT_OK


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python3 download_images.py <book_url_or_id>")
        sys.exit(wc.EXIT_USAGE)
    sys.exit(main(wc.parse_book_id(sys.argv[1])))
