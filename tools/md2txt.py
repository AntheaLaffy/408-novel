#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""md2txt — 把小说 Markdown 稿转成番茄小说能直接用的纯文本。

番茄的编辑器/导入只认纯文本，不渲染 Markdown，所以转换的目标是：
去掉所有标记语法，只留下正文、段落、分隔符，并让每章适合单独粘贴/导入。

处理规则
--------
* `# 第01章 标题` / `## 小节`   -> 保留文字，当作一行标题（`--strip-h1` 可丢掉一级标题）
* `**加粗**` `*斜体*` `~~删除线~~` -> 去掉标记，保留文字
* `` `代码` ``                     -> 去掉反引号，保留内容
* ```代码块```（含 `> ``` ` 嵌套）  -> 原样保留内容，前后留空行
* `> 引用` / 聊天记录 / 页边批注    -> 去掉 `>`，逐行保留（不合并，避免聊天消息粘成一段）
* `---` `***`                     -> 换成场景分隔符（默认 `＊　＊　＊`，`--hr` 可改）
* `- 列表` `1. 列表`               -> 去掉列表符号
* `| 表格 |`                       -> 单元格用全角空格连接，丢掉分隔行
* `[文字](链接)` `![图](链接)`      -> 只留文字
* HTML 标签、HTML 实体             -> 去掉 / 还原
* 段落                             -> 一行一段，默认行首两个全角空格（`--no-indent` 关掉）

用法
----
    默认每章一个 txt：一卷一个目录（`dist/卷名/章名.txt`），方便逐章导入平台。

    # 单章看效果（打到屏幕）
    python3 tools/md2txt.py 正文/第一卷-六战清华哥/第01章-空指针.md

    # 单章存文件
    python3 tools/md2txt.py 正文/第一卷-六战清华哥/第01章-空指针.md -o 第01章.txt

    # 单卷、每章一个 txt；-o 直接当作这一卷的输出目录
    python3 tools/md2txt.py 正文/第一卷-六战清华哥 -o dist/第一卷

    # 递归处理 正文/ 下所有卷、每章一个 txt：dist/卷名/章名.txt
    python3 tools/md2txt.py 正文 -r

    # 递归时排除旧稿、进度等非正文文件（可多次给 --exclude）
    python3 tools/md2txt.py 正文 -r --exclude '旧稿*' --exclude '进度.md'

    # 单卷合并成一个 txt，输出到 dist/第一卷-六战清华哥.txt
    python3 tools/md2txt.py 正文/第一卷-六战清华哥 --merge

    # 递归处理 正文/ 下所有卷、每卷一个合并 txt：dist/卷名.txt
    python3 tools/md2txt.py 正文 -r --merge

    # 想核对每章字数
    python3 tools/md2txt.py 正文/第一卷-六战清华哥 --stats

默认不加空行（网文 txt 的常见排版）；想段落之间空一行就加 `--blank-lines`。
需要给 Windows 编辑器加 BOM 就加 `--bom`。
"""

from __future__ import annotations

import argparse
import fnmatch
import html
import re
import sys
from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------- 正则

FENCE_RE = re.compile(r"^ {0,3}```(.*)$")
HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.*?)[ \t]*#*$")
HR_RE = re.compile(r"^(?:[ \t]*[-*_]){3,}[ \t]*$")
ULIST_RE = re.compile(r"^[ \t]*[-*+][ \t]+(.*)$")
OLIST_RE = re.compile(r"^[ \t]*\d+[.)][ \t]+(.*)$")
QUOTE_RE = re.compile(r"^[ \t]*>[ \t]?(.*)$")
TABLE_SEP_RE = re.compile(r"^[ \t]*\|?[ \t]*:?-{2,}:?[ \t]*(\|[ \t]*:?-{2,}:?[ \t]*)*\|?[ \t]*$")

INLINE_CODE_RE = re.compile(r"`([^`\n]*)`")
IMAGE_RE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
STRIKE_RE = re.compile(r"~~(.+?)~~")
BOLD_RE = re.compile(r"(\*\*|__)(.+?)\1")
ITALIC_RE = re.compile(r"(?<![*\w])([*_])(?![\s*_])(.+?)(?<![\s*_])\1(?![*\w])")
HTML_RE = re.compile(r"</?[A-Za-z][^>]*>")
ESCAPE_RE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!>~|])")
PLACEHOLDER_RE = re.compile(r"\x00(\d+)\x00")
INVISIBLE_RE = re.compile(r"[\u200b-\u200f\u202a-\u202e\ufeff]")

# 转完后仍出现这些，基本就是漏掉的 Markdown
LEFTOVER_PATTERNS = [
    (re.compile(r"\*\*|__"), "加粗标记"),
    (re.compile(r"`"), "反引号"),
    (re.compile(r"\]\("), "链接语法"),
    (re.compile(r"^\s{0,3}#{1,6}\s"), "标题标记"),
    (re.compile(r"^\s*>"), "引用标记"),
]


# ---------------------------------------------------------------- 行内清洗

def strip_inline(text: str) -> str:
    """去掉一行里的行内 Markdown，代码片段先抠出来避免被误伤。"""
    codes: list[str] = []

    def stash(m: re.Match[str]) -> str:
        codes.append(m.group(1))
        return "\x00%d\x00" % (len(codes) - 1)

    out = INLINE_CODE_RE.sub(stash, text)
    out = IMAGE_RE.sub(lambda m: m.group(1), out)
    out = LINK_RE.sub(lambda m: m.group(1), out)
    out = STRIKE_RE.sub(lambda m: m.group(1), out)
    out = BOLD_RE.sub(lambda m: m.group(2), out)
    out = ITALIC_RE.sub(lambda m: m.group(2), out)
    out = HTML_RE.sub("", out)
    out = ESCAPE_RE.sub(lambda m: m.group(1), out)
    out = PLACEHOLDER_RE.sub(lambda m: codes[int(m.group(1))], out)
    out = html.unescape(out)
    out = INVISIBLE_RE.sub("", out)
    out = out.replace("\u00a0", " ")
    return out.strip().strip("\u3000").strip()


def clean_line(line: str) -> str:
    """处理一行普通正文：去掉多余的连续空格，但保留中文排版用的单个空格。"""
    line = strip_inline(line)
    line = re.sub(r"[ \t]{2,}", " ", line)
    return line.strip()


# ---------------------------------------------------------------- 解析

def parse_blocks(text: str, *, strip_h1: bool, hr_text: str):
    """把 Markdown 拆成 [(kind, payload, lineno)]。

    kind: para / heading / code / hr
    """
    blocks: list[tuple[str, object, int]] = []
    code_buf: list[str] = []
    code_lang = ""
    in_code = False

    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")

    for lineno, raw in enumerate(lines, 1):
        line = raw.replace("\t", "    ").rstrip()

        # 引用可以嵌套，逐层剥掉（`> > x`）
        while True:
            m = QUOTE_RE.match(line)
            if not m:
                break
            line = m.group(1)

        # 代码围栏（包括 `> ``` ` 这种嵌在引用里的）
        fence = FENCE_RE.match(line)
        if fence:
            if in_code:
                blocks.append(("code", (code_lang, code_buf), lineno))
                code_buf, code_lang, in_code = [], "", False
            else:
                in_code, code_lang = True, fence.group(1).strip()
            continue

        if in_code:
            code_buf.append(line)
            continue

        if not line.strip():
            continue

        stripped = line.strip()

        if HR_RE.match(stripped):
            if hr_text:
                blocks.append(("hr", hr_text, lineno))
            continue

        heading = HEADING_RE.match(stripped)
        if heading:
            level = len(heading.group(1))
            title = clean_line(heading.group(2))
            if title and not (level == 1 and strip_h1):
                blocks.append(("heading", (title, level), lineno))
            continue

        table_sep = TABLE_SEP_RE.match(stripped)
        if stripped.startswith("|") and not table_sep:
            cells = [clean_line(c) for c in stripped.strip("|").split("|")]
            blocks.append(("para", "\u3000".join(c for c in cells if c), lineno))
            continue
        if table_sep:
            continue

        for pattern in (ULIST_RE, OLIST_RE):
            m = pattern.match(line)
            if m:
                line = m.group(1)
                break

        body = clean_line(line)
        if body:
            blocks.append(("para", body, lineno))

    if in_code and code_buf:  # 没闭合的代码块也别丢内容
        blocks.append(("code", (code_lang, code_buf), len(lines)))

    return blocks


# ---------------------------------------------------------------- 渲染

def render(blocks, *, indent: str, blank_lines: bool) -> str:
    out: list[str] = []

    def ensure_gap() -> None:
        if out and out[-1] != "":
            out.append("")

    for kind, payload, _lineno in blocks:
        if kind == "heading":
            title, _level = payload  # type: ignore[misc]
            ensure_gap()
            out.append(title)
            out.append("")

        elif kind == "code":
            _lang, code_lines = payload  # type: ignore[misc]
            ensure_gap()
            out.extend(str(l).rstrip() for l in code_lines)
            out.append("")

        elif kind == "hr":
            out.append(str(payload))

        else:  # para
            out.append(indent + str(payload))
            if blank_lines:
                out.append("")

    # 收尾：去掉尾部空行
    while out and out[-1] == "":
        out.pop()
    return "\n".join(out) + "\n"


def convert(path: Path, *, indent: str, blank_lines: bool, hr_text: str, strip_h1: bool):
    text = path.read_text(encoding="utf-8-sig")
    blocks = parse_blocks(text, strip_h1=strip_h1, hr_text=hr_text)
    return render(blocks, indent=indent, blank_lines=blank_lines), blocks


# ---------------------------------------------------------------- 检查

def leftovers(path: Path, blocks) -> list[str]:
    warnings: list[str] = []
    for kind, payload, lineno in blocks:
        if kind == "heading":
            samples = [payload[0]]  # type: ignore[index]
        elif kind == "code":
            continue  # 代码块里的符号是内容，不算残留
        elif kind == "hr":
            continue
        else:
            samples = [payload]
        for sample in samples:
            for pattern, label in LEFTOVER_PATTERNS:
                if pattern.search(str(sample)):
                    warnings.append(f"{path}:{lineno}: 疑似残留{label}：{str(sample)[:60]}")
                    break
    return warnings


def chapter_stats(text: str) -> tuple[int, int]:
    """返回 (行数, 字数)；字数按非空白字符数算，接近平台口径。"""
    body = [l for l in text.splitlines() if l.strip()]
    chars = sum(len(re.sub(r"\s", "", l)) for l in body)
    return len(body), chars


# ---------------------------------------------------------------- 文件收集

def natural_key(path: Path):
    return [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", path.name)]


@dataclass
class Entry:
    path: Path          # md 文件
    root: Path          # 用户传入的目录（或文件所在目录）
    volume: Path        # 所属卷目录：正文/第一卷-六战清华哥

    @property
    def is_volume_root(self) -> bool:
        """用户是不是直接把这个卷目录传进来了。"""
        return self.root == self.volume


def collect_entries(inputs: list[str], recursive: bool, excludes: list[str] | None = None) -> list[Entry]:
    excludes = excludes or []
    entries: list[Entry] = []
    for item in inputs:
        p = Path(item)
        if p.is_dir():
            pattern = "**/*.md" if recursive else "*.md"
            found = [f for f in p.glob(pattern) if f.is_file()]
            for f in sorted(found, key=natural_key):
                rel = f.relative_to(p)
                if any(fnmatch.fnmatch(f.name, pat) or fnmatch.fnmatch(str(rel), pat) for pat in excludes):
                    continue
                # 正文/第一卷-六战清华哥/第01章.md -> 卷 = 正文/第一卷-六战清华哥
                # 正文/第一卷-六战清华哥/第01章.md 且 p 就是卷目录 -> 卷 = p
                volume = p / rel.parts[0] if len(rel.parts) > 1 else p
                entries.append(Entry(f, p, volume))
        elif p.is_file():
            entries.append(Entry(p, p.parent, p.parent))
        else:
            sys.exit(f"找不到这个路径：{item}")
    # 去重但保持顺序
    seen: set[Path] = set()
    unique: list[Entry] = []
    for e in entries:
        rp = e.path.resolve()
        if rp not in seen:
            seen.add(rp)
            unique.append(e)
    return unique


def group_entries(entries: list[Entry]) -> dict[Path, list[Entry]]:
    """按卷分组，并保证每卷内部按章节号自然排序。"""
    groups: dict[Path, list[Entry]] = {}
    for e in entries:
        groups.setdefault(e.volume, []).append(e)
    for volume in groups:
        groups[volume].sort(key=lambda x: natural_key(x.path))
    return groups


# ---------------------------------------------------------------- 入口

def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        prog="md2txt.py",
        description="小说 Markdown → 番茄小说可用的纯文本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("inputs", nargs="+", help="Markdown 文件或目录")
    ap.add_argument("-o", "--output", help="输出根目录（多卷时按卷分文件/子目录）；单文件合并时可给 .txt 文件")
    ap.add_argument("-m", "--merge", action="store_true",
                    help="每卷合并成一个 txt（默认是每章一个 txt）")
    ap.add_argument("--each", action="store_true",
                    help="每章一个 txt（已是默认行为，保留只为兼容旧命令）")
    ap.add_argument("--exclude", action="append", default=[], metavar="GLOB",
                    help="递归时排除匹配的文件名或相对路径，可多次给，如 --exclude '旧稿*' --exclude '进度.md'")
    ap.add_argument("-r", "--recursive", action="store_true", help="目录输入时递归查找子目录")
    ap.add_argument("--indent", default="\u3000\u3000", help="段落开头缩进，默认两个全角空格")
    ap.add_argument("--no-indent", action="store_true", help="段落不缩进")
    ap.add_argument("--blank-lines", action="store_true", help="段落之间空一行")
    ap.add_argument("--hr", default="\uff0a\u3000\uff0a\u3000\uff0a", help="场景分隔符，默认 ＊　＊　＊；给空字符串则删除")
    ap.add_argument("--strip-h1", action="store_true", help="丢掉一级标题（章节名放到平台标题栏时用）")
    ap.add_argument("--bom", action="store_true", help="输出 UTF-8 BOM（Windows 记事本友好）")
    ap.add_argument("--stats", action="store_true", help="输出每章行数和字数")
    ap.add_argument("-q", "--quiet", action="store_true", help="不打印转换进度")
    args = ap.parse_args(argv)

    if args.merge and args.each:
        sys.exit("--each 和 --merge 只能选一个")

    indent = "" if args.no_indent else args.indent
    entries = collect_entries(args.inputs, args.recursive, args.exclude)
    if not entries:
        sys.exit("没找到任何 .md 文件")

    groups = group_entries(entries)
    has_dir = any(Path(i).is_dir() for i in args.inputs)
    file_only = not has_dir
    # 用户是不是直接把某一个卷目录传进来了，例如 正文/第一卷-六战清华哥
    explicit_volume = len(groups) == 1 and all(e.is_volume_root for e in entries)
    out_arg = Path(args.output) if args.output else None
    default_root = Path("dist") if has_dir else Path(".")

    encoding = "utf-8-sig" if args.bom else "utf-8"
    all_warnings: list[str] = []

    def write(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding=encoding)
        if not args.quiet:
            print(f"→ {path}")

    def render_entry(e: Entry) -> str:
        content, blocks = convert(
            e.path, indent=indent, blank_lines=args.blank_lines,
            hr_text=args.hr, strip_h1=args.strip_h1,
        )
        all_warnings.extend(leftovers(e.path, blocks))
        if args.stats:
            rows, chars = chapter_stats(content)
            print(f"  {e.path.name}: {rows} 段 / {chars} 字", file=sys.stderr)
        return content

    def merge(es: list[Entry], target: Path) -> None:
        body = "\n\n".join(render_entry(e).rstrip("\n") for e in es) + "\n"
        write(target, body)

    def as_file_target(target: Path, stem: str) -> Path:
        """-o 给的是目录时，自动补一个文件名。"""
        if target.suffix.lower() == ".txt":
            return target
        return target / (stem + ".txt")

    # ---- 每章一个文件（默认）
    if not args.merge:
        if file_only and len(entries) == 1 and out_arg is None:
            # 单章看效果：打到屏幕，不落盘
            sys.stdout.write(render_entry(entries[0]))
        elif out_arg is not None and out_arg.suffix.lower() == ".txt":
            if len(entries) != 1:
                sys.exit("-o 是 .txt，但每章一个文件需要输出目录；请给目录，或加 --merge")
            write(out_arg, render_entry(entries[0]))
        else:
            for volume, es in groups.items():
                for e in es:
                    rel = e.path.relative_to(volume).with_suffix(".txt")
                    if file_only:
                        base = out_arg if out_arg is not None else default_root
                        target = base / rel.name
                    elif out_arg is not None and explicit_volume:
                        # 旧的单卷用法：-o dist/第一卷 -> dist/第一卷/第01章.txt
                        target = out_arg / rel
                    else:
                        # 多卷：-o dist -> dist/卷名/第01章.txt
                        base = (out_arg / volume.name) if out_arg is not None else (default_root / volume.name)
                        target = base / rel
                    write(target, render_entry(e))

    # ---- 合并成一个文件；单文件且没给 -o 就打到屏幕
    else:
        if file_only:
            # 直接传 md 文件：保持旧行为，全部合并成一个
            if len(entries) == 1 and out_arg is None:
                sys.stdout.write(render_entry(entries[0]))
            else:
                target = out_arg if out_arg is not None else Path("合并.txt")
                merge(entries, as_file_target(target, "合并"))
        elif explicit_volume:
            # 单卷：默认 dist/卷名.txt
            volume = next(iter(groups))
            es = groups[volume]
            if out_arg is not None:
                target = as_file_target(out_arg, volume.name)
            else:
                target = default_root / (volume.name + ".txt")
            merge(es, target)
        else:
            # 多卷：每卷一个 txt
            if out_arg is not None and out_arg.suffix.lower() == ".txt":
                sys.exit("-o 是 .txt，但输入包含多个分卷；请给目录")
            for volume, es in groups.items():
                target = (out_arg if out_arg is not None else default_root) / (volume.name + ".txt")
                merge(es, target)

    if all_warnings:
        print("\n⚠ 下面这些地方可能还留着 Markdown 标记，请人工确认：", file=sys.stderr)
        for w in all_warnings:
            print("  " + w, file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
