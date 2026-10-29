<div align="center">

<img src="./408改考：最高的山！.png" alt="《408改考：最高的山！》封面" width="300">

# 408改考：最高的山！

**一个考了六次的人，和一次真正的开始。**

[在线阅读](https://anthealaffy.github.io/408-novel/) ·
[第一卷 EPUB](https://anthealaffy.github.io/408-novel/download/v1.epub) ·
[章节目录](https://anthealaffy.github.io/408-novel/#volumes)

</div>

---

## 关于本书

一个靠励志文案自我感动了六次的失败考生，在 AI 夺走他的工作之后，借助 AI 重新学会学习，最终走进计算机系统研究的故事。

2026 年 3 月，陆沉二十四岁，被裁员，存款四万。他决定再考一次。

## 在线阅读

站点由仓库里的 Markdown 自动生成，纯静态、无外部依赖，支持：

- 霞鹜文楷排版、深浅色、字号调节
- 目录抽屉、阅读进度、续读、键盘翻页
- 手机 / 平板 / 桌面自适应
- 每卷 EPUB 3 下载（由章节页经 pandoc 转换，已通过 epubcheck）

<!-- BEGIN generated:progress -->
当前进度：**3 卷 46 章**。第一卷《六战清华哥》（14 章）、第二卷《明天一定》（23 章）已完结，第三卷《学校不是名字》连载中（9/23）。
<!-- END generated:progress -->

## 仓库结构

```text
正文/
  第一卷-六战清华哥/      # 正文，一章一个 Markdown
  第二卷-明天一定/
408改考：最高的山！.png    # 封面原图
site/
  style.css              # 站点与 EPUB 共用的阅读排版
  epub.css               # 只修正阅读器不支持的部分
  site.js                # 阅读增强（目录、进度、续读）
  favicon.svg
docs/                    # 构建产物，GitHub Pages 从这里发布
tools/
  novel.py               # Markdown 解析与分卷（唯一排版规则）
  build_site.py          # Markdown -> 静态站 -> 章节页
  web2epub.py            # 章节页 HTML/CSS -> EPUB 3（pandoc）
  build_fonts.py         # 按站点用字子集化霞鹜文楷
  md2txt.py              # Markdown -> 平台导入用纯文本
  wordcount.py           # 汉字 / 字符 / 标点分口径统计
  status.py              # 卷/章/下一章/字数的唯一计算器，并校验文档一致性
  publish.sh             # 重建 docs/、提交并推送
site.json                # 书名、作者、简介、仓库地址
```

大纲、调研资料、旧稿和写作进度属于创作内部资料，不随本站发布。

## 本地构建

需要 Python 3.10+、[pandoc](https://pandoc.org/installing.html)，以及：

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

```bash
# 本地预览（根路径，产物在 _site/）
python3 tools/build_site.py --out _site --base /
python3 -m http.server 8000 -d _site

# 发到 GitHub Pages 的版本（/408-novel/ 子路径，产物在 docs/）
python3 tools/build_site.py \
  --out docs \
  --base /408-novel/ \
  --site-url https://anthealaffy.github.io/408-novel/
```

`build_site.py` 先生成章节页，再由 `web2epub.py` 从这些页面抽取正文、复用
同一套 CSS 和字体，用 pandoc 打包 EPUB。网页和书不会各排一遍。

单独导出：

```bash
# 从已构建的章节页单独生成某一卷 EPUB
python3 tools/web2epub.py v1 --site docs --out /tmp/v1.epub \
  --title "六战清华哥（第一卷）" --author "如月风铃"

# 番茄小说等平台可用的纯文本
python3 tools/md2txt.py 正文/第一卷-六战清华哥
```

## 发布

修改 `正文/` 下的 Markdown 后，一条命令重建并发布：

```bash
tools/publish.sh "更新第二卷"
```

脚本会构建 `docs/`、提交改动并推送到 `main`。GitHub Pages 从 `main` 的
`/docs` 目录直接发布——生成的 HTML、CSS、字体和 EPUB 就是线上内容，
不依赖 Actions 构建。

## 排版与字体

正文与界面使用 [霞鹜文楷（LXGW WenKai）](https://github.com/lxgw/LxgwWenKai)。构建时按站点实际用字子集化为 WOFF2，通常每份不到 0.5 MB；网页和 EPUB 使用同一份子集字体与同一套阅读 CSS。字体以 SIL Open Font License 1.1 分发，许可证见 [licenses/LXGW-WenKai-OFL.txt](licenses/LXGW-WenKai-OFL.txt)。

## 版权

《408改考：最高的山！》正文与封面版权归作者 **如月风铃** 所有，未经许可请勿转载、改编或用于商业用途。

`tools/` 与 `site/` 下的构建脚本可自由参考使用。
