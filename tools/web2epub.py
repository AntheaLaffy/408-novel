#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""web2epub — 用 pandoc 把生成好的阅读页转成 EPUB。

为什么不再手写 EPUB
-------------------
站点已经是最终排版：同一份 HTML、同一套 CSS、同一张封面、同一份字体。
再把 Markdown 单独排一遍，只会得到两个逐渐分叉的版本。

现在的流程是：

    正文 Markdown -> build_site -> 章节页 HTML
                                 -> web2epub（抽取 <article>，pandoc）
                                 -> 与网页同款的 EPUB

抽取时只去掉网页外壳（顶栏、页脚、翻页按钮），保留章节标题、卷名、
字数、正文、引用、代码块和场景分隔符；CSS 直接复用 `assets/site.css`，
再叠一份 `site/epub.css` 修正阅读器不支持的布局。

依赖
----
* pandoc（HTML -> EPUB 3）
* beautifulsoup4（从页面里取正文）

用法
----
    # 单独调试：用某一卷的章节页生成 EPUB
    python3 tools/web2epub.py 第一卷-六战清华哥 \
        --site docs --out /tmp/v1.epub
"""

from __future__ import annotations

import argparse
import html
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    from bs4 import BeautifulSoup
except ImportError:  # 构建前会给出明确提示
    BeautifulSoup = None  # type: ignore[assignment]

SITE_ROOT = Path(__file__).resolve().parent.parent
VOLUME_RE = re.compile(r"^第\s*([0-9]+)\s*章")


def require_tools() -> None:
    if BeautifulSoup is None:
        raise RuntimeError("缺少 beautifulsoup4：pip install -r requirements.txt")
    if shutil.which("pandoc") is None:
        raise RuntimeError("缺少 pandoc：https://pandoc.org/installing.html")


def _clean_meta(text: str) -> str:
    return re.sub(r"^第\s*\d+\s*章\s*[·•]\s*", "", text).strip()


def extract_chapter(page: Path) -> dict:
    """从生成的章节页里取出标题、卷名、字数和正文 HTML。"""
    require_tools()
    soup = BeautifulSoup(page.read_text(encoding="utf-8"), "html.parser")
    article = soup.find("article", class_="chapter")
    if article is None:
        raise RuntimeError(f"{page} 里没有 article.chapter")

    for footer in article.find_all("footer", class_="chapter-foot"):
        footer.decompose()

    body = article.find("div", class_="chapter-body")
    if body is None:
        raise RuntimeError(f"{page} 里没有 chapter-body")

    # 网页内的站内链接在书里没有意义，保留文字即可
    for link in body.find_all("a"):
        link.unwrap()

    title_el = article.find("h1")
    title = title_el.get_text(" ", strip=True) if title_el else page.stem

    label = article.get("data-chapter-label", "")
    match = re.search(r"第\s*(\d+)\s*章", label)
    number = f"第 {match.group(1)} 章" if match else ""

    kicker_el = article.find(class_="chapter-kicker")
    meta_el = article.find(class_="chapter-meta")
    kicker = kicker_el.get_text(" ", strip=True) if kicker_el else ""
    meta = _clean_meta(meta_el.get_text(" ", strip=True)) if meta_el else ""

    return {
        "title": title,
        "number": number,
        "kicker": kicker,
        "meta": meta,
        "body": body.decode_contents(),
    }


def _fragment(chapter: dict) -> str:
    number = (
        f'<span class="num">{html.escape(chapter["number"])}</span>'
        if chapter["number"]
        else ""
    )
    head = ""
    if chapter["kicker"] or chapter["meta"]:
        head = (
            '<div class="chapter-head">'
            f'<p>{html.escape(chapter["kicker"])}</p>'
            f'<p>{html.escape(chapter["meta"])}</p>'
            "</div>"
        )
    return f"""<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" lang="zh-CN">
<head><meta charset="utf-8"/><title>{html.escape(chapter["title"])}</title></head>
<body>
<h1 class="chapter-title">{number} <span class="name">{html.escape(chapter["title"])}</span></h1>
{head}
<div class="chapter-body">
{chapter["body"]}
</div>
</body>
</html>
"""


def _metadata(*, description: str, subjects: list[str], date: str) -> str:
    """pandoc 的 epub-metadata 读取的是并列的 DC 元素，不认 <metadata> 包装。"""
    ns = 'xmlns:dc="http://purl.org/dc/elements/1.1/"'
    parts: list[str] = []
    if description:
        parts.append(f'<dc:description {ns}>{html.escape(description)}</dc:description>')
    for subject in subjects:
        if subject:
            parts.append(f'<dc:subject {ns}>{html.escape(subject)}</dc:subject>')
    if date:
        parts.append(f'<dc:date {ns}>{html.escape(date)}</dc:date>')
    return "\n".join(parts) + "\n"


def _epub_css(site_css: Path, epub_css: Path, target: Path) -> list[Path]:
    """复制站点 CSS，并把字体路径改到 pandoc 的 fonts/ 目录。"""
    css = site_css.read_text(encoding="utf-8")
    css = css.replace('url("fonts/', 'url("../fonts/')
    (target / "site.css").write_text(css, encoding="utf-8")
    shutil.copyfile(epub_css, target / "epub.css")
    return [target / "site.css", target / "epub.css"]


def build(
    out: Path,
    *,
    pages: list[Path],
    title: str,
    author: str,
    lang: str = "zh-CN",
    description: str = "",
    subjects: list[str] | None = None,
    date: str = "",
    cover: Path | None = None,
    site_css: Path,
    epub_css: Path,
    fonts: list[Path] | None = None,
    quiet: bool = False,
) -> None:
    """把一组章节页转成一本书；输出路径由调用方决定。"""
    require_tools()
    if not pages:
        raise RuntimeError("没有章节页可供转换")

    fonts = [f for f in (fonts or []) if f.is_file()]
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="web2epub-") as tmp:
        work = Path(tmp)
        fragments = []
        for index, page in enumerate(pages):
            chapter = extract_chapter(page)
            path = work / f"ch{index:03d}.html"
            path.write_text(_fragment(chapter), encoding="utf-8")
            fragments.append(path)

        metadata = work / "metadata.xml"
        metadata.write_text(
            _metadata(
                description=description,
                subjects=subjects or [],
                date=date,
            ),
            encoding="utf-8",
        )
        css_files = _epub_css(site_css, epub_css, work)

        cmd = [
            "pandoc",
            *[str(path) for path in fragments],
            "--from=html",
            "--to=epub3",
            "--output",
            str(out),
            "--toc",
            "--toc-depth=1",
            "--split-level=1",
            "--epub-metadata",
            str(metadata),
            "-M",
            f"title={title}",
            "-M",
            f"author={author}",
            "-M",
            f"lang={lang}",
        ]
        for css in css_files:
            cmd += ["--css", str(css)]
        for font in fonts:
            cmd += ["--epub-embed-font", str(font)]
        if cover is not None and cover.is_file():
            cmd += ["--epub-cover-image", str(cover)]
        proc = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "pandoc 转换失败").strip()
            raise RuntimeError(detail[-1200:])


def _find_pages(site: Path, volume: str) -> list[Path]:
    volume_dir = site / "read" / volume
    if not volume_dir.is_dir():
        raise SystemExit(f"找不到卷目录：{volume_dir}")
    return sorted(volume_dir.glob("*.html"))


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="用 pandoc 把章节页转成 EPUB")
    ap.add_argument("volume", help="read/ 下的卷 slug，如 v1")
    ap.add_argument("--site", default="docs", help="站点目录")
    ap.add_argument("--out", default="book.epub", help="输出 EPUB")
    ap.add_argument("--title", default="未命名", help="书名")
    ap.add_argument("--author", default="佚名", help="作者")
    ap.add_argument("--description", default="", help="简介")
    ap.add_argument("--date", default="", help="dc:date，如 2026-10-27")
    ap.add_argument("-q", "--quiet", action="store_true")
    args = ap.parse_args(argv)

    site = Path(args.site).resolve()
    build(
        Path(args.out),
        pages=_find_pages(site, args.volume),
        title=args.title,
        author=args.author,
        description=args.description,
        date=args.date,
        cover=site / "assets" / "cover.jpg",
        site_css=site / "assets" / "site.css",
        epub_css=SITE_ROOT / "site" / "epub.css",
        fonts=[
            site / "assets" / "fonts" / "lxgw-regular.woff2",
            site / "assets" / "fonts" / "lxgw-medium.woff2",
        ],
        quiet=args.quiet,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
