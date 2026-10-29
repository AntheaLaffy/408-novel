#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""status — 全书状态的唯一计算器：派生所有进度数字，并校验文档一致性。

问题
----
进度数字原本手写在 README、AGENTS、大纲/README、正文/进度.md 和各卷卷纲里，
改一章要同步五处，漏一处就出现“43 章 / 42 章”这种互相矛盾的档案。

事实源（只有这三样，都是人手写的）
--------------------------------
* `大纲/卷纲/*.md` 的章标题 `## 第N章《标题》` —— 章节清单与顺序
* `正文/第X卷/第N章-标题.md` —— 哪些章已经写完、正文首行标题
* `正文/进度.md` —— 章节日志、编辑记录、时间锚点（只写事实，不写统计）

派生（本脚本生成，不要手改）
--------------------------
卷数、章数、已写/待写、下一章、各卷进度行、字数汇总，写进文档里的
`<!-- BEGIN generated:NAME --> … <!-- END generated:NAME -->` 生成块。

用法
----
    python3 tools/status.py            # 打印状态
    python3 tools/status.py --check    # 校验，结构错误或生成块过期时退出码 1
    python3 tools/status.py --write    # 重写全部生成块
"""

from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parent
sys.path.insert(0, str(TOOLS))

from novel import VOLUME_RE, CN_DIGITS, collect_volumes, parse, chapter_title  # noqa: E402
from wordcount import measure  # noqa: E402

OUTLINE = ROOT / "大纲" / "卷纲"
BODY = ROOT / "正文"
PROGRESS = BODY / "进度.md"
README = ROOT / "README.md"
AGENTS = ROOT / "AGENTS.md"
OUTLINE_README = ROOT / "大纲" / "README.md"

VOL_HEAD_RE = re.compile(r"^#\s*第([0-9一二三四五六七八九十百]+)卷《(.+?)》（第(\d+)[—\-](\d+)章")
CHAP_HEAD_RE = re.compile(r"^##\s*第(\d+)章《(.+?)》\s*(?:（(.+?)）)?\s*$")
PERIOD_RE = re.compile(r"\*\*时段\*\*：(.+?)。")
BODY_FILE_RE = re.compile(r"^第(\d+)章[-—](.+)\.md$")
BODY_HEAD_RE = re.compile(r"^#\s*第(\d+)章\s*(.+?)\s*$")

BEGIN = "<!-- BEGIN generated:{name} -->"
END = "<!-- END generated:{name} -->"

CN_NUM = "一二三四五六七八九十"


def cn(n: int) -> str:
    """1 -> 一，10 -> 十，23 -> 二十三（够用到 99）。"""
    if n <= 10:
        return CN_NUM[n - 1]
    tens, ones = divmod(n, 10)
    return ("" if tens == 1 else CN_NUM[tens - 1]) + "十" + (CN_NUM[ones - 1] if ones else "")


def cn_to_int(text: str) -> int:
    digits = {"零": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9}
    if "十" in text:
        tens, _, ones = text.partition("十")
        return (digits.get(tens, 1) if tens else 1) * 10 + (digits.get(ones, 0) if ones else 0)
    return digits.get(text, 0) or CN_DIGITS.get(text, 0)


def span(nums: list[int]) -> str:
    """[38,39,40,41,42] -> 38—42；有洞就逐个列。"""
    nums = sorted(nums)
    if not nums:
        return "—"
    if len(nums) == 1:
        return str(nums[0])
    if nums == list(range(nums[0], nums[-1] + 1)):
        return f"{nums[0]}—{nums[-1]}"
    return "、".join(str(n) for n in nums)


@dataclass
class Chapter:
    num: int
    title: str
    volume: int
    note: str = ""
    written: bool = False
    han: int = 0
    chars: int = 0


@dataclass
class Volume:
    num: int
    name: str
    range: tuple[int, int] | None
    period: str = ""
    chapters: list[Chapter] = field(default_factory=list)

    @property
    def planned(self) -> int:
        return len(self.chapters)

    @property
    def done(self) -> list[Chapter]:
        return [c for c in self.chapters if c.written]

    @property
    def han(self) -> int:
        return sum(c.han for c in self.chapters)


# --------------------------------------------------------------------------
# 解析三处事实源


def load_outline() -> list[Volume]:
    volumes: list[Volume] = []
    for path in sorted(OUTLINE.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        head = VOL_HEAD_RE.search(text)
        if not head:
            raise SystemExit(f"{path}: 首行应为 `# 第X卷《卷名》（第A—B章）`")
        num = cn_to_int(head.group(1))
        volume = Volume(num=num, name=head.group(2),
                        range=(int(head.group(3)), int(head.group(4))))
        period = PERIOD_RE.search(text)
        if period:
            volume.period = period.group(1)
        for line in text.splitlines():
            match = CHAP_HEAD_RE.match(line)
            if match:
                volume.chapters.append(Chapter(
                    num=int(match.group(1)),
                    title=match.group(2),
                    volume=num,
                    note=(match.group(3) or "").strip(),
                ))
        volumes.append(volume)
    volumes.sort(key=lambda v: v.num)
    return volumes


def load_body(volumes: list[Volume]) -> list[str]:
    """把正文文件名与首行标题套到章节上，返回错误信息。"""
    problems: list[str] = []
    by_num = {c.num: c for v in volumes for c in v.chapters}
    by_volume = {v.num: v for v in volumes}

    groups = collect_volumes([str(BODY)], recursive=True, excludes=["进度.md"])
    for vol_dir, files in groups.items():
        match = VOLUME_RE.match(vol_dir.name)
        if not match:
            problems.append(f"正文目录名不合规：{vol_dir.name}")
            continue
        vol_num = cn_to_int(match.group(1))
        volume = by_volume.get(vol_num)
        if volume is None:
            problems.append(f"正文目录 {vol_dir.name} 没有对应的卷纲")
            continue
        plan_empty = not volume.chapters

        for path in files:
            name_match = BODY_FILE_RE.match(path.name)
            if not name_match:
                problems.append(f"文件名不合规：{path.relative_to(ROOT)}")
                continue
            num = int(name_match.group(1))
            text = path.read_text(encoding="utf-8")
            head = BODY_HEAD_RE.match(text.splitlines()[0]) if text.strip() else None
            problem = None
            if not head:
                problem = f"首行应为 `# 第{num}章 标题`：{path.relative_to(ROOT)}"
                title = name_match.group(2)
            elif int(head.group(1)) != num:
                problem = (f"文件名写第{num}章，首行写第{head.group(1)}章："
                           f"{path.relative_to(ROOT)}")
                title = head.group(2)
            else:
                title = head.group(2)
            if problem:
                problems.append(problem)
            han, chars, _punct = measure(text)

            chapter = by_num.get(num)
            if chapter is None:
                chapter = Chapter(num=num, title=title, volume=vol_num)
                if not plan_empty:
                    problems.append(
                        f"第{num}章《{title}》有正文，但卷纲里没有这一章")
                volume.chapters.append(chapter)
                by_num[num] = chapter
            elif chapter.volume != vol_num:
                problems.append(
                    f"第{num}章在卷纲里属于第{chapter.volume}卷，正文却在 {vol_dir.name}")
            elif chapter.title != title:
                problems.append(
                    f"第{num}章标题不一致：卷纲《{chapter.title}》／正文《{title}》")
            if chapter.written:
                problems.append(f"第{num}章重复出现")

            chapter.written = True
            chapter.title = title
            chapter.han = han
            chapter.chars = chars
            chapter.note = chapter.note or ""

    for volume in volumes:
        volume.chapters.sort(key=lambda c: c.num)
    return problems


# --------------------------------------------------------------------------
# 派生文本


def written_volumes(volumes: list[Volume]) -> list[Volume]:
    return [v for v in volumes if v.done]


def all_chapters(volumes: list[Volume]) -> list[Chapter]:
    return [c for v in volumes for c in v.chapters]


def next_chapter(volumes: list[Volume]) -> Chapter | None:
    return next((c for c in all_chapters(volumes) if not c.written), None)


def volume_state(volume: Volume) -> str:
    if volume.planned and len(volume.done) == volume.planned:
        return "完结"
    return "连载中"


def block_readme(volumes: list[Volume]) -> str:
    done = written_volumes(volumes)
    if not done:
        return "当前进度：尚未开始连载。"
    finished = [v for v in done if volume_state(v) == "完结"]
    running = [v for v in done if volume_state(v) != "完结"]
    parts: list[str] = []
    if finished:
        names = "、".join(
            f"第{cn(v.num)}卷《{v.name}》（{len(v.done)} 章）" for v in finished)
        parts.append(f"{names}已完结")
    for v in running:
        parts.append(
            f"第{cn(v.num)}卷《{v.name}》连载中（{len(v.done)}/{v.planned}）")
    total = sum(len(v.done) for v in done)
    return f"当前进度：**{len(done)} 卷 {total} 章**。" + "，".join(parts) + "。"


def block_agents(volumes: list[Volume]) -> str:
    done = written_volumes(volumes)
    total = sum(len(v.done) for v in done)
    han = sum(c.han for c in all_chapters(volumes) if c.written)
    chars = sum(c.chars for c in all_chapters(volumes) if c.written)
    lines = [f"- {len(done)} 卷 {total} 章（已写）；汉字 {han:,}，站点字符 {chars:,}。"]
    nxt = next_chapter(volumes)
    if nxt:
        vol = next(v for v in volumes if v.num == nxt.volume)
        lines.append(
            f"- 下一章：第 {nxt.num} 章《{nxt.title}》（第{cn(vol.num)}卷《{vol.name}》）。")
    else:
        lines.append("- 全部章节已写完。")
    return "\n".join(lines)


def block_outline_readme(volumes: list[Volume]) -> str:
    lines: list[str] = []
    for v in volumes:
        if not v.chapters:
            continue
        if volume_state(v) == "完结":
            state = f"完结，{len(v.done)} 章"
        elif v.done:
            state = f"已写 {span([c.num for c in v.done])}（{len(v.done)}/{v.planned}）"
            nxt = next((c for c in v.chapters if not c.written), None)
            if nxt:
                state += f"，下一章 {nxt.num}《{nxt.title}》"
        else:
            state = f"未写，{v.planned} 章"
        lines.append(f"- 第{cn(v.num)}卷《{v.name}》（{span([c.num for c in v.chapters])}）：{state}")
    written = [c for c in all_chapters(volumes) if c.written]
    lines.append(
        f"- 计划合计 {len(volumes)} 卷 {len(all_chapters(volumes))} 章；"
        f"已写 {len(written)} 章，汉字 {sum(c.han for c in written):,}"
        f"（`tools/wordcount.py` 口径）。")
    return "\n".join(lines)


def block_progress_volumes(volumes: list[Volume]) -> str:
    """`正文/进度.md` 的八卷 checklist。"""
    lines: list[str] = []
    for v in volumes:
        if not v.chapters:
            lines.append(f"- [ ] 第{cn(v.num)}卷《{v.name}》：未排章")
            continue
        rng = span([c.num for c in v.chapters])
        if volume_state(v) == "完结":
            mark, note = "x", f"✅ {len(v.done)} 章，汉字 {v.han:,}"
        elif v.done:
            mark = "~"
            note = (f"**连载中，已写 {span([c.num for c in v.done])}"
                    f"（{len(v.done)}/{v.planned}）**，汉字 {v.han:,}")
        else:
            mark, note = " ", f"{v.planned} 章待写"
        lines.append(f"- [{mark}] 第{cn(v.num)}卷《{v.name}》（第{rng}章）{note}")
    return "\n".join(lines)


def width(text: str) -> int:
    """终端/等宽字体里的显示宽度，CJK 记 2。"""
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def block_outline_map(volumes: list[Volume]) -> str:
    """`大纲/README.md` 文件地图里的卷纲清单，范围由章标题算出。"""
    lines: list[str] = []
    for path in sorted(OUTLINE.glob("*.md")):
        match = re.match(r"^(\d{2})-第(.)卷", path.name)
        if not match:
            continue
        volume = next((v for v in volumes if v.num == cn_to_int(match.group(2))), None)
        if volume is None:
            continue
        rng = span([c.num for c in volume.chapters]) if volume.chapters else "未排章"
        lines.append("    " + path.name + " " * max(2, 36 - width(path.name)) + rng)
    return "\n".join(lines)


def block_volume_progress(volume: Volume) -> str:
    rng = span([c.num for c in volume.chapters])
    if volume_state(volume) == "完结":
        return f"**进度**：{rng} 已写完（{len(volume.done)}/{volume.planned}），本卷完结。"
    if volume.done:
        nxt = next((c for c in volume.chapters if not c.written), None)
        tail = f"，下一章 {nxt.num}《{nxt.title}》" if nxt else ""
        rest = span([c.num for c in volume.chapters if not c.written])
        return (f"**进度**：已写 {span([c.num for c in volume.done])}"
                f"（{len(volume.done)}/{volume.planned}）{tail}；待写 {rest}。")
    return f"**进度**：未写，{volume.planned} 章（{rng}）。"


# --------------------------------------------------------------------------
# 生成块读写


def blocks_for(volumes: list[Volume]) -> list[tuple[Path, str, str]]:
    out = [
        (README, "progress", block_readme(volumes)),
        (AGENTS, "status", block_agents(volumes)),
        (OUTLINE_README, "status", block_outline_readme(volumes)),
        (PROGRESS, "volumes", block_progress_volumes(volumes)),
    ]
    out.append((OUTLINE_README, "map", block_outline_map(volumes)))
    for path in sorted(OUTLINE.glob("*.md")):
        volume = next((v for v in volumes if v.name in path.name), None)
        if volume:
            out.append((path, "progress", block_volume_progress(volume)))
    return out


def block_pattern(name: str) -> re.Pattern[str]:
    return re.compile(
        re.escape(BEGIN.format(name=name)) + r"\n(.*?)" + re.escape(END.format(name=name)),
        re.S)


def sync(write: bool) -> list[str]:
    """返回漂移说明；write=True 时顺便重写。"""
    drift: list[str] = []
    for path, name, body in blocks_for(load_outline_safe()[0]):
        text = path.read_text(encoding="utf-8")
        pattern = block_pattern(name)
        if not pattern.search(text):
            drift.append(f"{path.relative_to(ROOT)}: 缺少 generated:{name} 标记")
            continue
        current = pattern.search(text).group(1).strip()
        if current == body.strip():
            continue
        drift.append(f"{path.relative_to(ROOT)}: generated:{name} 已过期")
        if write:
            filled = (BEGIN.format(name=name) + "\n" + body.strip("\n") + "\n"
                      + END.format(name=name))
            path.write_text(pattern.sub(lambda m: filled, text, count=1), encoding="utf-8")
    return drift


def load_outline_safe() -> tuple[list[Volume], list[str]]:
    volumes = load_outline()
    problems = load_body(volumes)
    return volumes, problems


# --------------------------------------------------------------------------
# 校验


def check(volumes: list[Volume], problems: list[str]) -> list[str]:
    nums = [c.num for v in volumes for c in v.chapters]
    seen: set[int] = set()
    for num in nums:
        if num in seen:
            problems.append(f"第{num}章在卷纲里出现了两次")
        seen.add(num)

    previous = 0
    for volume in volumes:
        if not volume.chapters:
            continue
        first, last = volume.chapters[0].num, volume.chapters[-1].num
        if volume.range and (first, last) != volume.range:
            problems.append(
                f"第{cn(volume.num)}卷标题写 {volume.range[0]}—{volume.range[1]}，"
                f"实际段落 {first}—{last}")
        if previous and first != previous + 1:
            gap = (f"缺第{previous + 1}章"
                   if first > previous + 1 else f"第{first}章与上一卷重叠")
            problems.append(f"第{cn(volume.num)}卷起始章号 {first} 不接上一卷末 {previous}（{gap}）")
        previous = last

    return problems


def report(volumes: list[Volume]) -> None:
    print(f"{'卷':<14}{'章数':>6}{'已写':>6}{'汉字':>9}  区间")
    for v in volumes:
        rng = span([c.num for c in v.chapters])
        print(f"第{cn(v.num)}卷《{v.name}》 {v.planned:>5}{len(v.done):>7}{v.han:>9}  {rng}")
    written = [c for c in all_chapters(volumes) if c.written]
    print(f"\n合计 {len(volumes)} 卷 {len(all_chapters(volumes))} 章；"
          f"已写 {len(written)} 章，汉字 {sum(c.han for c in written):,}，"
          f"字符 {sum(c.chars for c in written):,}")
    nxt = next_chapter(volumes)
    if nxt:
        print(f"下一章：第 {nxt.num} 章《{nxt.title}》")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="全书状态：生成进度数字并校验文档")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--write", action="store_true", help="重写生成块")
    group.add_argument("--check", action="store_true", help="只校验，不写文件")
    args = parser.parse_args(argv[1:])

    volumes, problems = load_outline_safe()
    if args.write:
        drift = sync(write=True)
        for line in drift:
            print(f"已重写 {line}")
        print(f"{len(drift)} 个生成块更新。")
    else:
        problems = check(volumes, problems)
        if args.check:
            problems += sync(write=False)
    report(volumes)

    if problems:
        print("\n问题：")
        for line in problems:
            print(f"- {line}")
        if args.check:
            return 1
    short = [c for c in all_chapters(volumes) if c.written and c.han < 2500]
    if short:
        print(f"提示：{len(short)} 章低于 2500 汉字下限（已发布章节不返工；新章按 2500—4000 写）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
