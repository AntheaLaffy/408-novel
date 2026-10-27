#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_fonts — 把霞鹜文楷按站点实际用字子集化成 woff2。

为什么要在构建时子集化
----------------------
中文字体动辄 20MB。站点只需要正文和界面里出现过的字，按语料子集后
通常不到 1MB，既能保证「生僻字也不缺字」，又不会让每个读者下载整包。

字体来源
--------
* 优先用本机已安装的 LXGWWenKai-Regular.ttf；
* 否则从 GitHub Release 下载固定版本到 `.cache/fonts/`；
* Medium 用于标题和粗体，找不到就退回 Regular 合成。

许可证
------
霞鹜文楷以 SIL OFL 1.1 发布，允许子集化后在网页上分发。
本站把许可证放在 `assets/fonts/OFL.txt`，页脚有署名。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

VERSION = "v1.530"
RELEASE = "https://github.com/lxgw/LxgwWenKai/releases/download/%s/" % VERSION
FONTS = {
    "regular": ("LXGWWenKai-Regular.ttf", "lxgw-regular.woff2"),
    "medium": ("LXGWWenKai-Medium.ttf", "lxgw-medium.woff2"),
}

# 除了正文，补齐 ASCII、标点和常用符号，避免代码块、数字、箭头缺字。
BASE_CHARS = "".join(chr(c) for c in range(0x20, 0x7F)) + (
    "\u3000\u3001\u3002\uff0c\uff0e\u00b7\uff1b\uff1a\uff1f\uff01"
    "\u2026\u2014\u2013\uff5e\u300a\u300b\u3008\u3009\u201c\u201d\u2018\u2019"
    "\uff08\uff09\u3010\u3011\u3014\u3015\u3016\u3017\uff05\uff0b\uff0d"
    "\u00d7\u00f7\uff1d\uffe5\uff0f\u2190\u2192\u2191\u2193\u2022\u00a9"
    "\uff10\uff11\uff12\uff13\uff14\uff15\uff16\uff17\uff18\uff19\uff0a"
)


def _local_candidates(name: str) -> list[Path]:
    home = Path.home()
    return [
        home / ".local/share/fonts" / name,
        home / ".fonts" / name,
        Path("/usr/share/fonts/truetype/lxgw-wenkai") / name,
        Path("/usr/share/fonts/TTF") / name,
        Path("/usr/local/share/fonts") / name,
    ]


def _source_font(name: str, cache: Path) -> Path:
    for candidate in _local_candidates(name):
        if candidate.is_file():
            return candidate
    cached = cache / name
    if cached.is_file() and cached.stat().st_size > 1_000_000:
        return cached
    cache.mkdir(parents=True, exist_ok=True)
    url = RELEASE + name
    print(f"  ↓ 下载字体 {name}（{VERSION}）")
    req = urllib.request.Request(url, headers={"User-Agent": "novel-site-builder/1.0"})
    with urllib.request.urlopen(req, timeout=180) as resp, open(cached, "wb") as fh:
        shutil.copyfileobj(resp, fh)
    if cached.stat().st_size < 1_000_000:
        raise RuntimeError(f"字体下载不完整：{cached}")
    return cached


def _subset(src: Path, corpus: Path, out: Path) -> None:
    tmp = out.with_suffix(".tmp.woff2")
    cmd = [
        sys.executable,
        "-m",
        "fontTools.subset",
        str(src),
        f"--text-file={corpus}",
        f"--output-file={tmp}",
        "--flavor=woff2",
        "--layout-features=*",
        "--no-hinting",
        "--desubroutinize",
        "--drop-tables+=DSIG",
    ]
    proc = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or "pyftsubset 失败").strip()[-800:])
    tmp.replace(out)


def build(corpus: str, out_dir: Path, project_root: Path, *, quiet: bool = False) -> bool:
    """生成 regular/medium 两个 woff2；失败返回 False，站点自动退回系统字体。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    cache = project_root / ".cache" / "fonts"
    text = BASE_CHARS + corpus

    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".txt", delete=False) as fh:
            fh.write(text)
            corpus_path = Path(fh.name)
    except OSError as exc:
        print(f"⚠ 字体语料写入失败：{exc}", file=sys.stderr)
        return False

    ok = True
    try:
        for key, (filename, target) in FONTS.items():
            try:
                src = _source_font(filename, cache)
                dest = out_dir / target
                _subset(src, corpus_path, dest)
                if not quiet:
                    print(f"  · {target}  {dest.stat().st_size / 1024 / 1024:.1f} MB")
            except Exception as exc:  # 字体问题不该拖垮整站构建
                print(f"⚠ {key} 字体生成失败：{exc}", file=sys.stderr)
                ok = False
    finally:
        corpus_path.unlink(missing_ok=True)

    license_src = project_root / "licenses" / "LXGW-WenKai-OFL.txt"
    if license_src.is_file():
        shutil.copyfile(license_src, out_dir / "OFL.txt")

    return ok


def main(argv: list[str]) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="按语料子集化霞鹜文楷")
    ap.add_argument("corpus", help="包含所有用字的文本文件")
    ap.add_argument("-o", "--out", default="_site/assets/fonts", help="输出目录")
    args = ap.parse_args(argv)

    root = Path(__file__).resolve().parent.parent
    text = Path(args.corpus).read_text(encoding="utf-8")
    return 0 if build(text, Path(args.out), root) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
