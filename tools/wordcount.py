#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""wordcount — 按不同口径统计正文体量。

三个口径差得很远，报数时必须说清用的是哪一个：

* 汉字：只数 Unihan 汉字，不含标点、数字、英文与 Markdown 符号。最保守。
* 字符：含标点、数字、英文和 `**` 之类的标记，与站点页脚显示的“字”
  同一口径（直接调用 novel.count_chars，不含空行、分隔线与代码围栏）。
* 中文标点：单独给出，方便把前两者对上账。

用法：
    python3 tools/wordcount.py            # 全部卷
    python3 tools/wordcount.py 第二卷     # 只统计匹配的卷
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parent
sys.path.insert(0, str(TOOLS))

from novel import collect_volumes, count_chars, parse  # noqa: E402

HAN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
PUNCT_RE = re.compile(r"[，。！？；：、“”‘’（）《》〈〉…—～·]")


def measure(text: str) -> tuple[int, int, int]:
    """返回（汉字，站点口径字符，中文标点）。"""
    han = len(HAN_RE.findall(text))
    chars = count_chars(parse(text.splitlines()))
    punct = len(PUNCT_RE.findall(text))
    return han, chars, punct


def main(argv: list[str]) -> int:
    volumes = collect_volumes([str(ROOT / "正文")], recursive=True)
    patterns = argv[1:]
    grand = [0, 0, 0]

    for vol_dir, files in sorted(volumes.items()):
        if patterns and not any(p in vol_dir.name for p in patterns):
            continue
        subtotal = [0, 0, 0]
        print(f"\n{vol_dir.name}")
        print(f"{'章':>4} {'汉字':>7} {'字符':>7} {'标点':>6}  标题")
        for path in files:
            text = path.read_text(encoding="utf-8")
            title = text.splitlines()[0].lstrip("# ").strip()
            han, chars, punct = measure(text)
            subtotal = [a + b for a, b in zip(subtotal, (han, chars, punct))]
            num = re.match(r"第(\d+)章", path.name)
            print(f"{int(num.group(1)) if num else 0:>4} {han:>7} {chars:>7} {punct:>6}  {title}")
        print(f"{'小计':>4} {subtotal[0]:>7} {subtotal[1]:>7} {subtotal[2]:>6}")
        grand = [a + b for a, b in zip(grand, subtotal)]

    print(f"\n合计  汉字 {grand[0]}  字符（不计空白）{grand[1]}  其中中文标点 {grand[2]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
