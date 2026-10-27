#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""novel — 小说 Markdown 的解析与收集。

站点生成、EPUB 转换和纯文本导出都只依赖这一份解析规则，
避免“网页一种断行、电子书另一种断行”。
"""

from __future__ import annotations

import fnmatch
import html
import json
import re
import sys
from pathlib import Path

VOLUME_RE = re.compile(r"^第([0-9一二三四五六七八九十百]+)卷[-—_ ]*(.*)$")

HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.*?)[ \t]*#*$")
HR_RE = re.compile(r"^(?:[ \t]*[-*_]){3,}[ \t]*$")
FENCE_RE = re.compile(r"^ {0,3}```(.*)$")
QUOTE_RE = re.compile(r"^[ \t]*>[ \t]?(.*)$")
ULIST_RE = re.compile(r"^[ \t]*[-*+][ \t]+(.*)$")
OLIST_RE = re.compile(r"^[ \t]*\d+[.)][ \t]+(.*)$")
TABLE_SEP_RE = re.compile(r"^[ \t]*\|?[ \t]*:?-{2,}:?[ \t]*(\|[ \t]*:?-{2,}:?[ \t]*)*\|?[ \t]*$")

INLINE_CODE_RE = re.compile(r"`([^`\n]*)`")
IMAGE_RE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
STRIKE_RE = re.compile(r"~~(.+?)~~")
BOLD_RE = re.compile(r"(\*\*|__)(.+?)\1")
ITALIC_RE = re.compile(r"(?<![*\w])([*_])(?![\s*_])(.+?)(?<![\s*_])\1(?![*\w])")
PLACEHOLDER_RE = re.compile(r"\x00(\d+)\x00")
INVISIBLE_RE = re.compile(r"[\u200b-\u200f\u202a-\u202e\ufeff]")

CN_DIGITS = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}


def inline(text: str) -> str:
    """Markdown 行内语法 -> XHTML 片段。"""
    codes: list[str] = []

    def stash(m: re.Match[str]) -> str:
        codes.append(m.group(1))
        return "\x00%d\x00" % (len(codes) - 1)

    out = INLINE_CODE_RE.sub(stash, text)
    out = IMAGE_RE.sub(lambda m: m.group(1), out)
    out = LINK_RE.sub(lambda m: m.group(1), out)
    out = html.escape(out, quote=False)
    out = STRIKE_RE.sub(lambda m: "<del>%s</del>" % m.group(1), out)
    out = BOLD_RE.sub(lambda m: "<strong>%s</strong>" % m.group(2), out)
    out = ITALIC_RE.sub(lambda m: "<em>%s</em>" % m.group(2), out)
    out = INVISIBLE_RE.sub("", out).replace("\u00a0", " ")
    out = PLACEHOLDER_RE.sub(
        lambda m: "<code>%s</code>" % html.escape(codes[int(m.group(1))], quote=False),
        out,
    )
    return out.strip()


def parse(lines: list[str], *, in_quote: bool = False) -> list[tuple]:
    """Markdown 行 -> 块。

    kind: heading / para / code / quote / hr
    """
    blocks: list[tuple] = []
    para: list[str] = []
    code_buf: list[str] = []
    quote_buf: list[str] = []
    in_code = False

    def flush_para() -> None:
        nonlocal para
        if para:
            blocks.append(("para", para))
            para = []

    def flush_quote() -> None:
        nonlocal quote_buf
        if quote_buf:
            blocks.append(("quote", parse(quote_buf, in_quote=True)))
            quote_buf = []

    for raw in lines:
        line = raw.replace("\t", "    ").rstrip()

        quote_stripped = line
        depth = 0
        while True:
            m = QUOTE_RE.match(quote_stripped)
            if not m:
                break
            depth += 1
            quote_stripped = m.group(1)
        if depth:
            if not quote_stripped.strip():
                continue
            flush_para()
            quote_buf.append(quote_stripped)
            continue
        if quote_buf:
            flush_quote()

        fence = FENCE_RE.match(line)
        if fence:
            if in_code:
                blocks.append(("code", code_buf))
                code_buf, in_code = [], False
            else:
                flush_para()
                in_code = True
            continue
        if in_code:
            code_buf.append(line)
            continue

        if not line.strip():
            flush_para()
            continue

        stripped = line.strip()

        if HR_RE.match(stripped):
            flush_para()
            blocks.append(("hr", None))
            continue

        heading = HEADING_RE.match(stripped)
        if heading:
            flush_para()
            blocks.append(("heading", (len(heading.group(1)), heading.group(2).strip())))
            continue

        if stripped.startswith("|") and TABLE_SEP_RE.match(stripped):
            continue
        if stripped.startswith("|"):
            flush_para()
            cells = [c.strip() for c in stripped.strip("|").split("|") if c.strip()]
            blocks.append(("para", ["\u3000".join(cells)]))
            continue

        for pattern in (ULIST_RE, OLIST_RE):
            m = pattern.match(line)
            if m:
                line = m.group(1)
                break

        para.append(line.strip())

    if in_code and code_buf:
        blocks.append(("code", code_buf))
    flush_para()
    if quote_buf:
        flush_quote()
    return blocks


def blocks_to_xhtml(blocks: list[tuple], *, quote: bool = False) -> str:
    out: list[str] = []
    for kind, payload in blocks:
        if kind == "heading":
            level, text = payload
            tag = "h2" if level >= 2 else "h1"
            cls = "section" if level >= 2 else "chapter"
            out.append('<%s class="%s">%s</%s>' % (tag, cls, inline(text), tag))
        elif kind == "para":
            body = "<br/>\n".join(inline(line) for line in payload)
            out.append("<p>%s</p>" % body)
        elif kind == "code":
            out.append(
                "<pre><code>%s</code></pre>"
                % html.escape("\n".join(payload), quote=False)
            )
        elif kind == "hr":
            out.append('<div class="sep">\uff0a\u3000\uff0a\u3000\uff0a</div>')
        elif kind == "quote":
            out.append(
                '<blockquote class="quote">\n%s\n</blockquote>'
                % blocks_to_xhtml(payload, quote=True)
            )
    return "\n".join(out)


def chapter_title(blocks: list[tuple], fallback: str) -> tuple[str, list[tuple]]:
    for index, (kind, payload) in enumerate(blocks):
        if kind == "heading" and payload[0] == 1:
            return payload[1], blocks[:index] + blocks[index + 1:]
    return fallback, blocks


def count_chars(blocks: list[tuple]) -> int:
    total = 0
    for kind, payload in blocks:
        if kind in ("para", "code"):
            total += sum(len(re.sub(r"\s", "", line)) for line in payload)
        elif kind == "heading":
            total += len(payload[1])
        elif kind == "quote":
            total += count_chars(payload)
    return total


def natural_key(path: Path):
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", path.name)]


def collect_volumes(
    inputs: list[str], recursive: bool, excludes: list[str] | None = None
) -> dict[Path, list[Path]]:
    """{卷目录: [章节 md]}；递归时跳过卷根目录下的散装文件。"""
    excludes = excludes or []
    volumes: dict[Path, list[Path]] = {}
    for item in inputs:
        path = Path(item)
        if path.is_file():
            volumes.setdefault(path.parent, []).append(path)
            continue
        if not path.is_dir():
            sys.exit(f"找不到这个路径：{item}")
        pattern = "**/*.md" if recursive else "*.md"
        for file in path.glob(pattern):
            if not file.is_file():
                continue
            rel = file.relative_to(path)
            if recursive and len(rel.parts) == 1 and not VOLUME_RE.match(path.name):
                continue
            if any(
                fnmatch.fnmatch(file.name, pat) or fnmatch.fnmatch(str(rel), pat)
                for pat in excludes
            ):
                continue
            volume = path / rel.parts[0] if len(rel.parts) > 1 else path
            volumes.setdefault(volume, []).append(file)
    for files in volumes.values():
        files.sort(key=natural_key)
    return volumes


def volume_number(volume_title: str) -> str:
    """把“第一卷”“第12卷”换成能排序的阿拉伯数字。"""
    m = re.search(r"第\s*([0-9]+|[一二三四五六七八九十]+)\s*卷", volume_title)
    if not m:
        return "1"
    token = m.group(1)
    if token.isdigit():
        return token
    if token == "十":
        return "10"
    if len(token) == 2 and token[0] == "十":
        return str(10 + CN_DIGITS.get(token[1], 0))
    if len(token) == 2 and token[1] == "十":
        return str(CN_DIGITS.get(token[0], 1) * 10)
    return str(CN_DIGITS.get(token[0], 1))


def load_config() -> dict:
    for parent in [Path.cwd(), *Path.cwd().parents]:
        file = parent / "site.json"
        if file.is_file():
            try:
                return json.loads(file.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                print(f"⚠ {file} 解析失败：{exc}", file=sys.stderr)
                return {}
    return {}
