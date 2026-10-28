#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_site — 从 `正文/` 生成静态阅读站，并从章节页转出 EPUB。

设计目标
--------
* 零前端依赖：一份 CSS、一份 JS，离线可读，GitHub Pages 直接托管。
* 排版优先：霞鹜文楷、纸感配色、首行缩进、引用/代码块单独处理。
* 阅读体验：目录抽屉、字号调节、深浅色、进度条、续读、键盘翻页。
* 单一事实来源：正文只改 Markdown，站点和 EPUB 都由脚本生成。

用法
----
    # 本地预览（根路径，产物在 _site/）
    python3 tools/build_site.py --out _site --base /
    python3 -m http.server 8000 -d _site

    # GitHub Pages（/仓库名/ 前缀，产物在 docs/）
    python3 tools/build_site.py --out docs --base /408-novel/ \
        --site-url https://anthealaffy.github.io/408-novel/

    # 不生成 EPUB / 不子集化字体
    python3 tools/build_site.py --no-epub --no-fonts

    # 只重建某一卷（其余卷照常保留）
    python3 tools/build_site.py --only v1
"""

from __future__ import annotations

import argparse
import html
import io
import json
import re
import shutil
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parent
sys.path.insert(0, str(TOOLS))

from novel import (  # noqa: E402
    VOLUME_RE,
    blocks_to_xhtml,
    chapter_title,
    collect_volumes,
    count_chars,
    natural_key,
    parse,
    volume_number,
)

import build_fonts  # noqa: E402
import web2epub  # noqa: E402


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


# ---------------------------------------------------------------- 数据

@dataclass
class Chapter:
    id: str
    num: int
    title: str
    slug: str
    chars: int
    minutes: int
    body: str
    source: Path
    volume: "Volume | None" = field(default=None, repr=False)

    @property
    def label(self) -> str:
        return f"第{self.num}章 {self.title}"

    @property
    def full_label(self) -> str:
        vol = self.volume.label + " · " if self.volume else ""
        return f"{vol}{self.label}"


@dataclass
class Volume:
    num: int
    label: str
    name: str
    slug: str
    source: Path
    chapters: list[Chapter] = field(default_factory=list)

    @property
    def chars(self) -> int:
        return sum(c.chars for c in self.chapters)

    @property
    def latest(self) -> Chapter | None:
        return self.chapters[-1] if self.chapters else None


def load_volumes(src: Path) -> list[Volume]:
    groups = collect_volumes([str(src)], recursive=True, excludes=["进度.md"])
    volumes: list[Volume] = []
    for vdir, files in groups.items():
        match = VOLUME_RE.match(vdir.name)
        num = int(volume_number(vdir.name))
        label = f"第{match.group(1)}卷" if match else f"第{num}卷"
        name = (match.group(2).strip() if match else vdir.name) or vdir.name
        volume = Volume(num=num, label=label, name=name, slug=f"v{num}", source=vdir)
        used: set[str] = set()
        for index, path in enumerate(sorted(files, key=natural_key), 1):
            text = path.read_text(encoding="utf-8-sig")
            blocks = parse(text.replace("\r\n", "\n").split("\n"))
            full_title, rest = chapter_title(blocks, path.stem)
            match_title = re.match(
                r"^第\s*0*(\d+)\s*章[\s　:：、.．·\-]*(.*)$", full_title.strip()
            )
            if match_title:
                number = int(match_title.group(1))
                title = match_title.group(2).strip() or full_title
            else:
                number = index
                title = full_title.strip() or path.stem.split("-", 1)[-1]
            slug = f"{number:02d}"
            while slug in used:
                slug += "x"
            used.add(slug)
            chars = count_chars(blocks)
            chapter = Chapter(
                id=f"{volume.slug}-{slug}",
                num=number,
                title=title,
                slug=slug,
                chars=chars,
                minutes=max(1, round(chars / 450)),
                body=blocks_to_xhtml(rest),
                source=path,
            )
            chapter.volume = volume
            volume.chapters.append(chapter)
        volume.chapters.sort(key=lambda c: (c.num, c.slug))
        volumes.append(volume)
    volumes.sort(key=lambda v: v.num)
    return volumes


# ---------------------------------------------------------------- 站点

class Site:
    def __init__(self, base: str, site_url: str) -> None:
        if not base.startswith("/"):
            base = "/" + base
        if not base.endswith("/"):
            base += "/"
        self.base = base
        self.site_url = site_url.rstrip("/") + "/" if site_url else ""

    def u(self, path: str = "") -> str:
        return self.base + path.lstrip("/")

    def absolute(self, path: str = "") -> str:
        if self.site_url:
            return self.site_url + path.lstrip("/")
        return self.u(path)


ICON_SUN = (
    '<svg class="icon-sun" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
    'stroke-width="1.6" stroke-linecap="round" aria-hidden="true">'
    '<circle cx="12" cy="12" r="4.2"/><path d="M12 2v2.2M12 19.8V22M2 12h2.2'
    'M19.8 12H22M4.9 4.9l1.6 1.6M17.5 17.5l1.6 1.6M19.1 4.9l-1.6 1.6'
    'M6.5 17.5l-1.6 1.6"/></svg>'
)
ICON_MOON = (
    '<svg class="icon-moon" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
    'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<path d="M20.5 14.5A8.5 8.5 0 0 1 9.5 3.5a8.5 8.5 0 1 0 11 11Z"/></svg>'
)
ICON_TOC = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" '
    'stroke-linecap="round" aria-hidden="true"><path d="M4 6.5h16M4 12h11M4 17.5h16"/>'
    "</svg>"
)
ICON_ARROW = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" '
    'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<path d="M4 12h15M13 6l6 6-6 6"/></svg>'
)
ICON_MARK = (
    '<svg viewBox="0 0 32 32" aria-hidden="true">'
    '<path d="M2 26 12 8l6 10 4-6 8 14z" fill="currentColor"/>'
    '<circle cx="24" cy="7" r="2.6" fill="var(--accent)"/></svg>'
)
MOUNTAINS = """
<svg class="mountains" viewBox="0 0 1440 260" preserveAspectRatio="none" aria-hidden="true">
  <path class="m3" d="M0 210 240 96l120 58 150-86 150 96 120-54 180 92 160-72 200 86 120-48v92H0z"/>
  <path class="m1" d="M0 240 180 152l130 46 150-96 170 110 110-40 180 84 170-66 190 96 160-40v54H0z"/>
  <path class="m2" d="M0 258 200 196l160 32 150-64 180 72 120-22 200 52 180-48 250 60v22H0z"/>
</svg>
"""


def render_header(cfg: dict, site: Site, *, chapter: bool = False) -> str:
    repo = cfg.get("repo", "")
    github = (
        f'<a class="nav-link" href="https://github.com/{esc(repo)}" target="_blank" '
        f'rel="noopener noreferrer">GitHub</a>'
        if repo
        else ""
    )
    if chapter:
        catalog = (
            f'<button class="icon-btn" type="button" data-toc-open aria-label="打开目录" '
            f'title="目录">{ICON_TOC}</button>'
        )
        font_ctl = (
            '<div class="font-ctl" role="group" aria-label="字号">'
            '<button class="icon-btn" type="button" data-font-dec aria-label="减小字号" '
            'title="减小字号">A−</button>'
            '<button class="icon-btn" type="button" data-font-inc aria-label="增大字号" '
            'title="增大字号">A+</button></div>'
        )
    else:
        catalog = f'<a class="nav-link" href="{site.u("#volumes")}">目录</a>'
        font_ctl = ""
    return f"""
<header class="site-header">
  <div class="header-inner">
    <a class="brand" href="{site.u("")}" aria-label="{esc(cfg["title"])} 首页">
      {ICON_MARK}<span>{esc(cfg.get("shortTitle", cfg["title"]))}</span>
      <span class="brand-sub">{esc(cfg.get("subtitle", ""))}</span>
    </a>
    <nav class="header-nav" aria-label="导航">
      {catalog}
      {github}
      {font_ctl}
      <button class="icon-btn" type="button" data-theme-toggle aria-pressed="false"
              aria-label="切换深浅色" title="深浅色">
        {ICON_SUN}{ICON_MOON}
      </button>
    </nav>
  </div>
</header>"""


def render_footer(cfg: dict, site: Site, *, updated: str, epub: str) -> str:
    repo = cfg.get("repo", "")
    year = datetime.now().year
    return f"""
<footer class="site-footer">
  <div class="footer-inner">
    <div>
      <p class="footer-brand">{esc(cfg["title"])}</p>
      <p class="footer-note">© {year} {esc(cfg.get("author", ""))} · 文字版权归作者所有<br/>
      正文与 EPUB 由仓库 Markdown 自动生成 · 最近更新 {updated}</p>
    </div>
    <nav class="footer-links" aria-label="页脚">
      <a href="{site.u("#volumes")}">目录</a>
      {f'<a href="{site.u(epub)}" download>下载 EPUB</a>' if epub else ""}
      {f'<a href="https://github.com/{esc(repo)}" target="_blank" rel="noopener noreferrer">GitHub</a>' if repo else ""}
      <a href="{site.u("assets/fonts/OFL.txt")}" target="_blank" rel="noopener">字体授权</a>
    </nav>
  </div>
</footer>"""


def render_page(
    *,
    cfg: dict,
    site: Site,
    path: str,
    title: str,
    description: str,
    body: str,
    body_class: str = "",
    head_extra: str = "",
    chapter: bool = False,
    drawer: str = "",
) -> str:
    author = cfg.get("author", "")
    full_title = f"{title} · {cfg['title']}" if title else cfg["title"]
    canonical = site.absolute(path) if site.site_url else ""
    og_image = site.absolute("assets/cover.jpg")
    repo = cfg.get("repo", "")
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{esc(full_title)}</title>
<meta name="description" content="{esc(description)}"/>
<meta name="author" content="{esc(author)}"/>
<meta name="generator" content="tools/build_site.py"/>
<link rel="icon" href="{site.u("assets/favicon.svg")}" type="image/svg+xml"/>
<link rel="stylesheet" href="{site.u("assets/site.css")}"/>
{f'<link rel="alternate" type="application/epub+zip" href="{site.u(cfg["_epub_href"])}" title="EPUB"/>' if cfg.get("_epub_href") else ""}
{f'<link rel="canonical" href="{esc(canonical)}"/>' if canonical else ""}
<meta property="og:type" content="{"article" if chapter else "website"}"/>
<meta property="og:site_name" content="{esc(cfg["title"])}"/>
<meta property="og:title" content="{esc(full_title)}"/>
<meta property="og:description" content="{esc(description)}"/>
<meta property="og:image" content="{esc(og_image)}"/>
<meta property="og:image:width" content="1086"/>
<meta property="og:image:height" content="1448"/>
<meta property="og:locale" content="zh_CN"/>
{f'<meta property="og:url" content="{esc(canonical)}"/>' if canonical else ""}
<meta name="twitter:card" content="summary_large_image"/>
<meta name="twitter:title" content="{esc(full_title)}"/>
<meta name="twitter:description" content="{esc(description)}"/>
<meta name="twitter:image" content="{esc(og_image)}"/>
<meta name="theme-color" content="#f7f3ea" media="(prefers-color-scheme: light)"/>
<meta name="theme-color" content="#141519" media="(prefers-color-scheme: dark)"/>
<script>try{{var t=localStorage.getItem("novel:theme");if(!t&&matchMedia("(prefers-color-scheme: dark)").matches)t="dark";if(t)document.documentElement.dataset.theme=t;var f=localStorage.getItem("novel:font");if(f)document.documentElement.dataset.font=f}}catch(e){{}}</script>
{head_extra}
</head>
<body class="{esc(body_class)}">
<a class="skip-link" href="#main">跳到正文</a>
{render_header(cfg, site, chapter=chapter)}
<main id="main">
{body}
</main>
{render_footer(cfg, site, updated=cfg.get("_updated", ""), epub=cfg.get("_epub_href", ""))}
{drawer}
<script src="{site.u("assets/site.js")}" defer></script>
</body>
</html>
"""


def volume_status(cfg: dict, volumes: list[Volume], volume: Volume) -> str:
    return cfg.get("status", "连载中") if volume is volumes[-1] else "完结"


def format_wan(chars: int) -> str:
    return f"{chars / 10000:.1f}"


def volume_card(cfg: dict, site: Site, volumes: list[Volume], volume: Volume) -> str:
    latest = volume.latest
    latest_text = (
        f'<b>最新</b>{esc(latest.label)}' if latest else "<b>暂无章节</b>"
    )
    return f"""
<article class="volume-card">
  <a href="{site.u(f"volumes/{volume.slug}.html")}">
    <span class="volume-ghost" aria-hidden="true">{volume.num:02d}</span>
    <p class="volume-label">{esc(volume.label)}</p>
    <h3>{esc(volume.name)}</h3>
    <p class="volume-meta">{len(volume.chapters)} 章 · {format_wan(volume.chars)} 万字 ·
      {esc(volume_status(cfg, volumes, volume))}</p>
    <p class="volume-latest">{latest_text}</p>
    <span class="volume-cta">进入本卷 {ICON_ARROW}</span>
  </a>
</article>"""


def render_home(cfg: dict, site: Site, volumes: list[Volume], chapters: list[Chapter]) -> str:
    first = chapters[0]
    latest = chapters[-1]
    total_chars = sum(v.chars for v in volumes)
    eyebrow = cfg.get("eyebrow", "现实主义 · 计算机考研 · AI 时代")
    cards = "\n".join(volume_card(cfg, site, volumes, v) for v in volumes)
    chips = "".join(f"<li>{esc(k)}</li>" for k in cfg.get("keywords", []))
    status = cfg.get("status", "连载中")

    book_ld = {
        "@context": "https://schema.org",
        "@type": "Book",
        "name": cfg["title"],
        "author": {"@type": "Person", "name": cfg.get("author", "")},
        "description": cfg.get("description", ""),
        "image": site.absolute("assets/cover.jpg"),
        "url": site.absolute(""),
        "inLanguage": "zh-CN",
        "bookFormat": "https://schema.org/EBook",
        "genre": cfg.get("keywords", []),
    }
    head_extra = (
        '<script type="application/ld+json">'
        + json.dumps(book_ld, ensure_ascii=False)
        + "</script>"
    )

    body = f"""
<section class="hero">
  <span class="hero-sun" aria-hidden="true"></span>
  <div class="hero-inner">
    <div class="hero-text">
      <p class="eyebrow">{esc(eyebrow)}</p>
      <h1 class="hero-title"><span>{esc(cfg.get("shortTitle", cfg["title"]))}</span><span class="accent">{esc(cfg.get("subtitle", ""))}</span></h1>
      <p class="hero-lede">{esc(cfg.get("tagline", ""))}</p>
      <p class="hero-meta">{esc(cfg.get("author", ""))} 著 · {len(volumes)} 卷 {len(chapters)} 章 · {esc(status)}</p>
      <p class="hero-resume" data-continue-wrap hidden><a data-continue href="{site.u(f"read/{first.volume.slug}/{first.slug}.html")}">继续上次阅读 · <span data-continue-label></span> →</a></p>
      <div class="hero-actions">
        <a class="btn" href="{site.u(f"read/{first.volume.slug}/{first.slug}.html")}">开始阅读 {ICON_ARROW}</a>
        {f'<a class="btn ghost" href="{site.u(cfg["_epub_href"])}" download>下载 EPUB</a>' if cfg.get("_epub_href") else ""}
      </div>
      <div class="hero-stats">
        <span><b>{len(volumes)}</b> 卷</span>
        <span><b>{len(chapters)}</b> 章</span>
        <span><b>{format_wan(total_chars)}</b> 万字</span>
        <span class="live"><i aria-hidden="true"></i>{esc(status)}</span>
      </div>
    </div>
    <div class="hero-cover">
      <div class="cover-frame">
        <img src="{site.u("assets/cover-sm.jpg")}" width="760" height="1013"
             alt="{esc(cfg["title"])} 封面" fetchpriority="high"/>
      </div>
      <span class="seal">{esc(status)}</span>
      <p class="cover-caption">一本关于重新开始的小说</p>
    </div>
  </div>
  {MOUNTAINS}
</section>

<div class="latest-strip">
  <span class="latest-tag">最新章节</span>
  <a href="{site.u(f"read/{latest.volume.slug}/{latest.slug}.html")}">{esc(latest.full_label)} <span aria-hidden="true">↗</span></a>
  <time datetime="{cfg.get('_updated', '')}">{cfg.get('_updated', '')}</time>
</div>
<section class="section" id="volumes">
  <div class="section-head">
    <h2><span class="section-index">01 /</span> 翻开故事</h2>
    <p>{len(volumes)} 卷 · {format_wan(total_chars)} 万字 · 最近更新 {cfg.get("_updated", "")}</p>
  </div>
  <div class="volume-grid">
{cards}
  </div>
</section>

<section class="section about" id="about">
  <div>
    <p class="eyebrow">关于本书</p>
    <h2>{esc(cfg.get("lede", ""))}</h2>
  </div>
  <div class="about-body">
    <p>{esc(cfg.get("description", ""))}</p>
    <p>{esc(cfg.get("blurb", ""))}</p>
    <ul class="chips">{chips}</ul>
  </div>
</section>"""
    return render_page(
        cfg=cfg,
        site=site,
        path="",
        title="",
        description=cfg.get("description", ""),
        body=body,
        body_class="page-home",
        head_extra=head_extra,
    )


def render_volume(cfg: dict, site: Site, volumes: list[Volume], volume: Volume) -> str:
    index = volumes.index(volume)
    prev_volume = volumes[index - 1] if index > 0 else None
    next_volume = volumes[index + 1] if index + 1 < len(volumes) else None
    rows = []
    for c in volume.chapters:
        rows.append(
            f"""<li data-chapter-id="{c.id}">
  <a href="{site.u(f"read/{volume.slug}/{c.slug}.html")}">
    <span class="num">{c.num:02d}</span>
    <span class="ttl">{esc(c.title)}</span>
    <span class="meta">{c.chars:,} 字 · {c.minutes} 分钟</span>
    <span class="arrow" aria-hidden="true">→</span>
  </a>
</li>"""
        )
    first = volume.chapters[0] if volume.chapters else None
    nav_links = []
    if prev_volume:
        nav_links.append(
            f'<a href="{site.u(f"volumes/{prev_volume.slug}.html")}">← {esc(prev_volume.label)} · {esc(prev_volume.name)}</a>'
        )
    if next_volume:
        nav_links.append(
            f'<a href="{site.u(f"volumes/{next_volume.slug}.html")}">{esc(next_volume.label)} · {esc(next_volume.name)} →</a>'
        )
    body = f"""
<section class="volume-hero">
  <span class="volume-ghost" aria-hidden="true">{volume.num:02d}</span>
  <nav class="crumbs" aria-label="面包屑">
    <a href="{site.u("")}">首页</a><span aria-hidden="true">/</span>
    <a href="{site.u("#volumes")}">分卷</a><span aria-hidden="true">/</span>
    <span>{esc(volume.label)}</span>
  </nav>
  <p class="eyebrow">{esc(volume.label)}</p>
  <h1>{esc(volume.name)}</h1>
  <p class="volume-meta">{len(volume.chapters)} 章 · {volume.chars:,} 字 · {format_wan(volume.chars)} 万字 · {esc(volume_status(cfg, volumes, volume))}</p>
  <div class="volume-actions">
    {f'<a class="btn" href="{site.u(f"read/{volume.slug}/{first.slug}.html")}">从头阅读 {ICON_ARROW}</a>' if first else ""}
    <a class="btn ghost" href="{site.u(f"download/{volume.slug}.epub")}" download>下载本卷 EPUB</a>
  </div>
</section>

<section class="toc-block" data-search-scope>
  <div class="catalog-toolbar">
    <h2 class="toc-title">章节目录</h2>
    <label class="chapter-search"><span>查找章节</span><input type="search" data-chapter-search placeholder="输入章名或序号" autocomplete="off"/></label>
  </div>
  <p class="search-status" data-search-status role="status" hidden></p>
  <ol class="chapter-list">
{chr(10).join(rows)}
  </ol>
</section>

<nav class="volume-nav" aria-label="分卷导航">
  {nav_links[0] if nav_links else "<span></span>"}
  {nav_links[1] if len(nav_links) > 1 else "<span></span>"}
</nav>"""
    return render_page(
        cfg=cfg,
        site=site,
        path=f"volumes/{volume.slug}.html",
        title=f"{volume.label} {volume.name}",
        description=f"{cfg['title']} {volume.label}《{volume.name}》章节目录。{cfg.get('description', '')}",
        body=body,
        body_class="page-volume",
    )


def render_drawer(volumes: list[Volume], site: Site, current: Chapter) -> str:
    groups = []
    for volume in volumes:
        links = []
        for c in volume.chapters:
            current_cls = ' class="is-current" aria-current="page"' if c.id == current.id else ""
            links.append(
                f'<li><a href="{site.u(f"read/{volume.slug}/{c.slug}.html")}"{current_cls}>'
                f'<span class="num">{c.num:02d}</span><span>{esc(c.title)}</span></a></li>'
            )
        groups.append(
            f'<p class="drawer-vol">{esc(volume.label)} <span>{esc(volume.name)}</span></p>'
            f'<ul class="drawer-list">{"".join(links)}</ul>'
        )
    return f"""
<div class="drawer" id="toc-drawer" aria-hidden="true">
  <div class="drawer-backdrop" data-close></div>
  <aside class="drawer-panel" role="dialog" aria-modal="true" aria-labelledby="toc-title">
    <div class="drawer-head">
      <h2 id="toc-title">目录</h2>
      <button class="icon-btn" type="button" data-close aria-label="关闭目录" title="关闭">×</button>
    </div>
    <nav class="drawer-body" aria-label="章节目录" data-search-scope>
      <label class="chapter-search"><span>查找章节</span><input type="search" data-chapter-search placeholder="输入章名或序号" autocomplete="off"/></label>
      <p class="search-status" data-search-status role="status" hidden></p>
      {"".join(groups)}
    </nav>
  </aside>
</div>"""


def render_chapter(
    cfg: dict, site: Site, volumes: list[Volume], chapters: list[Chapter], chapter: Chapter
) -> str:
    index = chapters.index(chapter)
    prev = chapters[index - 1] if index > 0 else None
    nxt = chapters[index + 1] if index + 1 < len(chapters) else None

    if prev:
        prev_html = (
            f'<a class="prev" href="{site.u(f"read/{prev.volume.slug}/{prev.slug}.html")}" '
            f'data-prev-chapter><span class="lbl">← 上一章</span>'
            f'<span class="ttl">{esc(prev.label)}</span></a>'
        )
    else:
        prev_html = '<span class="spacer" aria-hidden="true"></span>'

    if nxt:
        next_html = (
            f'<a class="next" href="{site.u(f"read/{nxt.volume.slug}/{nxt.slug}.html")}" '
            f'data-next-chapter><span class="lbl">下一章 →</span>'
            f'<span class="ttl">{esc(nxt.label)}</span></a>'
        )
    else:
        next_html = '<span class="spacer" aria-hidden="true"></span>'

    volume = chapter.volume
    body = f"""
<div class="progress" aria-hidden="true"><span></span></div>
<div class="reader-tools" aria-label="阅读工具">
  <button type="button" data-toc-open>章节目录</button>
  <span data-reading-percent>已读 0%</span>
  <a href="#main" aria-label="回到本章顶部">↑ 回顶部</a>
</div>
<div class="reading">
  <article class="chapter" data-chapter-id="{chapter.id}"
           data-chapter-url="{site.u(f"read/{volume.slug}/{chapter.slug}.html")}"
           data-chapter-label="{esc(chapter.full_label)}">
    <header class="chapter-head">
      <p class="chapter-kicker"><a href="{site.u(f"volumes/{volume.slug}.html")}">{esc(volume.label)} · {esc(volume.name)}</a></p>
      <h1>{esc(chapter.title)}</h1>
      <p class="chapter-meta">第 {chapter.num} 章 · {chapter.chars:,} 字 · 约 {chapter.minutes} 分钟</p>
    </header>
    <div class="chapter-body">
{chapter.body}
    </div>
    <footer class="chapter-foot">
      <nav class="pager" aria-label="章节翻页">
        {prev_html}
        {next_html}
      </nav>
      <div class="chapter-actions">
        <a href="{site.u(f"volumes/{volume.slug}.html")}">本卷目录</a>
        <a href="{site.u("#volumes")}">全部目录</a>
        <a href="{site.u(f"download/{volume.slug}.epub")}" download>下载 EPUB</a>
      </div>
    </footer>
  </article>
</div>"""
    return render_page(
        cfg=cfg,
        site=site,
        path=f"read/{volume.slug}/{chapter.slug}.html",
        title=chapter.label,
        description=f"{chapter.full_label}，{cfg['title']}。{chapter.title}——{cfg.get('tagline', '')}",
        body=body,
        body_class="page-chapter",
        chapter=True,
        drawer=render_drawer(volumes, site, chapter),
    )


def render_404(cfg: dict, site: Site) -> str:
    body = f"""
<section class="notfound">
  <p class="code">404</p>
  <h1>这一页没有被写进书里</h1>
  <p>链接可能已经移动，或者章节还没有更新。</p>
  <p><a class="btn" href="{site.u("")}">回到首页 {ICON_ARROW}</a></p>
</section>"""
    return render_page(
        cfg=cfg,
        site=site,
        path="404.html",
        title="页面不存在",
        description="页面不存在。",
        body=body,
        body_class="page-404",
    )


# ---------------------------------------------------------------- 资源

def write_cover(src: Path, out: Path) -> dict:
    assets = out / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    try:
        from PIL import Image

        with Image.open(src) as image:
            image = image.convert("RGB")
            full = image.copy()
            full.thumbnail((1600, 1600), Image.LANCZOS)
            full.save(assets / "cover.jpg", "JPEG", quality=90, optimize=True, progressive=True)
            thumb = image.copy()
            thumb.thumbnail((760, 760), Image.LANCZOS)
            thumb.save(
                assets / "cover-sm.jpg", "JPEG", quality=88, optimize=True, progressive=True
            )
            return {
                "file": "cover.jpg",
                "thumb": "cover-sm.jpg",
                "width": thumb.width,
                "height": thumb.height,
            }
    except ImportError:
        suffix = src.suffix.lower() or ".png"
        shutil.copyfile(src, assets / f"cover{suffix}")
        return {
            "file": f"cover{suffix}",
            "thumb": f"cover{suffix}",
            "width": 0,
            "height": 0,
        }


def font_corpus(cfg: dict, volumes: list[Volume]) -> str:
    parts = [json.dumps(cfg, ensure_ascii=False)]
    for path in (
        ROOT / "site" / "style.css",
        ROOT / "site" / "epub.css",
        ROOT / "site" / "site.js",
        ROOT / "tools" / "web2epub.py",
        Path(__file__).resolve(),
    ):
        if path.is_file():
            parts.append(path.read_text(encoding="utf-8"))
    for volume in volumes:
        for chapter in volume.chapters:
            parts.append(chapter.source.read_text(encoding="utf-8-sig"))
    return "\n".join(parts)


def strip_font_face(css: str) -> str:
    return re.sub(r"@font-face\s*\{[^}]*\}\s*", "", css)


# ---------------------------------------------------------------- 构建

def build(config: dict, args: argparse.Namespace) -> None:
    source = ROOT / config.get("sourceDir", "正文")
    if not source.is_dir():
        sys.exit(f"找不到正文目录：{source}")

    out = Path(args.out).resolve()
    if out.exists():
        if not out.is_dir():
            sys.exit(f"输出路径不是目录：{out}")
        if any(out.iterdir()) and not (out / ".build-site").is_file():
            sys.exit(f"{out} 非空且不是本站生成，拒绝覆盖（换一个 --out 或先手动清空）")
        shutil.rmtree(out)
    out.mkdir(parents=True)
    (out / ".build-site").write_text("generated by tools/build_site.py\n", encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")
    (out / "assets").mkdir()
    (out / "download").mkdir()

    if not args.no_epub:
        try:
            web2epub.require_tools()
        except RuntimeError as exc:
            sys.exit(str(exc))

    volumes = load_volumes(source)
    if not volumes:
        sys.exit("没有找到任何章节")
    if args.only:
        wanted = {item.strip() for item in args.only.split(",") if item.strip()}
        volumes = [v for v in volumes if v.slug in wanted or v.label in wanted]
        if not volumes:
            sys.exit("--only 没有匹配到分卷")

    chapters = [c for v in volumes for c in v.chapters]
    updated = datetime.fromtimestamp(
        max(c.source.stat().st_mtime for c in chapters)
    ).strftime("%Y-%m-%d")
    config = dict(config)
    config["_updated"] = updated
    config.setdefault("author", "佚名")
    config["_epub_href"] = (
        f"download/{volumes[0].slug}.epub"
        if (not args.no_epub and volumes)
        else ""
    )

    base = args.base or urlparse(config.get("siteUrl", "")).path or "/"
    site = Site(base, args.site_url or config.get("siteUrl", ""))

    cover_src = ROOT / config.get("cover", "")
    cover = write_cover(cover_src, out) if cover_src.is_file() else None
    cover_file = out / "assets" / cover["file"] if cover else None
    if not cover:
        print("⚠ 没有找到封面，站点将不显示封面图", file=sys.stderr)

    # 站点静态资源
    for name in ("style.css", "site.js", "favicon.svg"):
        shutil.copyfile(ROOT / "site" / name, out / "assets" / name)

    font_ok = False
    if not args.no_fonts:
        if not args.quiet:
            print("· 子集化霞鹜文楷")
        font_ok = build_fonts.build(
            font_corpus(config, volumes), out / "assets" / "fonts", ROOT, quiet=args.quiet
        )
        if not font_ok:
            print("⚠ 字体子集化失败，改用系统字体", file=sys.stderr)
    css_path = out / "assets" / "site.css"
    css = (ROOT / "site" / "style.css").read_text(encoding="utf-8")
    if not font_ok:
        css = strip_font_face(css)
    css_path.write_text(css, encoding="utf-8")

    # 页面
    pages: dict[str, str] = {}
    pages["index.html"] = render_home(config, site, volumes, chapters)
    for volume in volumes:
        pages[f"volumes/{volume.slug}.html"] = render_volume(config, site, volumes, volume)
    for chapter in chapters:
        pages[f"read/{chapter.volume.slug}/{chapter.slug}.html"] = render_chapter(
            config, site, volumes, chapters, chapter
        )
    pages["404.html"] = render_404(config, site)

    for rel, content in pages.items():
        target = out / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    # EPUB：不再单独排版，直接把刚生成的章节页交给 pandoc
    if not args.no_epub:
        fonts = [
            out / "assets" / "fonts" / name
            for name in ("lxgw-regular.woff2", "lxgw-medium.woff2")
        ]
        for volume in volumes:
            if not args.quiet:
                print(f"· 从网页生成 EPUB {volume.label}")
            web2epub.build(
                out / "download" / f"{volume.slug}.epub",
                pages=[
                    out / "read" / volume.slug / f"{chapter.slug}.html"
                    for chapter in volume.chapters
                ],
                title=f"{volume.name}（{volume.label}）",
                author=config["author"],
                description=config.get("description", ""),
                subjects=config.get("keywords", []),
                date=updated,
                cover=cover_file,
                site_css=out / "assets" / "site.css",
                epub_css=ROOT / "site" / "epub.css",
                fonts=fonts if font_ok else [],
                quiet=args.quiet,
            )

    # robots / sitemap
    if site.site_url:
        urls = [(site.absolute(""), updated)]
        for volume in volumes:
            urls.append((site.absolute(f"volumes/{volume.slug}.html"), updated))
            for chapter in volume.chapters:
                date = datetime.fromtimestamp(chapter.source.stat().st_mtime).strftime("%Y-%m-%d")
                urls.append((site.absolute(f"read/{volume.slug}/{chapter.slug}.html"), date))
        sitemap = ['<?xml version="1.0" encoding="UTF-8"?>',
                   '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
        for url, lastmod in urls:
            sitemap.append(
                f"  <url><loc>{esc(url)}</loc><lastmod>{lastmod}</lastmod></url>"
            )
        sitemap.append("</urlset>")
        (out / "sitemap.xml").write_text("\n".join(sitemap) + "\n", encoding="utf-8")
        (out / "robots.txt").write_text(
            "User-agent: *\nAllow: /\nSitemap: "
            + site.absolute("sitemap.xml")
            + "\n",
            encoding="utf-8",
        )

    total = sum(c.chars for c in chapters)
    if not args.quiet:
        print(
            f"✓ {out}  {len(volumes)} 卷 / {len(chapters)} 章 / {total:,} 字"
            f"{'  + 字体' if font_ok else ''}"
        )
        print(f"  预览：python3 -m http.server 8000 -d {out}")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        description="从 Markdown 生成小说阅读站（含 EPUB）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("--config", default=str(ROOT / "site.json"), help="站点配置")
    ap.add_argument("--out", default=str(ROOT / "_site"), help="输出目录")
    ap.add_argument("--base", help="URL 前缀，默认从 siteUrl 推断，如 /408-novel/")
    ap.add_argument("--site-url", help="站点完整地址，用于 canonical / sitemap")
    ap.add_argument("--only", help="只构建指定卷（slug 或卷名，逗号分隔）")
    ap.add_argument("--no-epub", action="store_true", help="不生成 EPUB")
    ap.add_argument("--no-fonts", action="store_true", help="不子集化字体")
    ap.add_argument("-q", "--quiet", action="store_true")
    args = ap.parse_args(argv)

    config_path = Path(args.config)
    if not config_path.is_file():
        sys.exit(f"找不到配置：{config_path}")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    build(config, args)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
