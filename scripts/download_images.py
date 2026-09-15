#!/usr/bin/env python3
"""独立图片下载器：强制 IPv4 + 并发 + 重试。读 raw/*.json 里的图片 URL 下到 images/。

可重复运行：已下载的图片自动跳过（失败补齐用同一条命令）。
并发参数取自 weread_common.THROTTLE（单一来源）；socket IPv4 补丁仅在
下载期间生效（入口打补丁、finally 还原——无 import 副作用）。
"""
import glob
import json
import os
import socket
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager

import weread_common as wc

HEADERS = {"Referer": "https://weread.qq.com/", "User-Agent": "Mozilla/5.0"}


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
    tasks = []
    for jf in sorted(glob.glob(os.path.join(raw_dir, "*.json"))):
        with open(jf) as f:
            for im in json.load(f).get("images", []):
                tasks.append((im["url"], os.path.join(img_dir, im["file"])))
    return tasks


def download_all(raw_dir, img_dir, workers=wc.THROTTLE["download_workers"],
                 retries=wc.THROTTLE["download_retries"],
                 timeout=wc.THROTTLE["download_timeout"]) -> dict:
    """并发下载 raw/ 名单里的全部图片；返回 {'ok','skip','fail','failures','elapsed'}。"""
    os.makedirs(img_dir, exist_ok=True)
    tasks = collect_tasks(raw_dir, img_dir)
    stats = {"ok": 0, "skip": 0, "fail": 0, "failures": [], "elapsed": 0.0}
    if not tasks:
        print("  (无图片)")
        return stats
    print(f"  {len(tasks)} 张图片，{workers} 线程并发下载（强制IPv4）...")
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
