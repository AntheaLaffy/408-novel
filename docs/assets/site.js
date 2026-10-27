/* 408改考 — 阅读增强：主题、字号、目录抽屉、进度与续读 */
(() => {
  "use strict";

  const root = document.documentElement;
  const KEYS = {
    theme: "novel:theme",
    font: "novel:font",
    last: "novel:last",
    read: "novel:read",
  };

  const raw = (key) => {
    try {
      return localStorage.getItem(key);
    } catch (err) {
      return null;
    }
  };

  const store = (key, value) => {
    try {
      localStorage.setItem(key, value);
    } catch (err) {
      /* 隐私模式下忽略 */
    }
  };

  const readJson = (key, fallback) => {
    const value = raw(key);
    if (!value) return fallback;
    try {
      return JSON.parse(value);
    } catch (err) {
      return fallback;
    }
  };

  const writeJson = (key, value) => {
    try {
      localStorage.setItem(key, JSON.stringify(value));
    } catch (err) {
      /* 忽略 */
    }
  };

  /* ------------------------------------------------------------ 主题 */

  const themeButtons = document.querySelectorAll("[data-theme-toggle]");

  const applyTheme = (theme) => {
    root.dataset.theme = theme;
    themeButtons.forEach((btn) => {
      btn.setAttribute("aria-pressed", String(theme === "dark"));
      btn.setAttribute(
        "aria-label",
        theme === "dark" ? "切换到浅色模式" : "切换到深色模式"
      );
      btn.setAttribute("title", theme === "dark" ? "浅色模式" : "深色模式");
    });
  };

  applyTheme(
    root.dataset.theme ||
      (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches
        ? "dark"
        : "light")
  );

  document.addEventListener("click", (event) => {
    const btn = event.target.closest("[data-theme-toggle]");
    if (!btn) return;
    const next = root.dataset.theme === "dark" ? "light" : "dark";
    applyTheme(next);
    store(KEYS.theme, next);
  });

  /* ------------------------------------------------------------ 字号 */

  const FONTS = ["sm", "md", "lg"];
  const fontInc = document.querySelector("[data-font-inc]");
  const fontDec = document.querySelector("[data-font-dec]");

  const applyFont = (size, persist) => {
    const index = Math.max(0, Math.min(FONTS.length - 1, FONTS.indexOf(size)));
    const current = FONTS[index];
    if (current === "md") {
      delete root.dataset.font;
    } else {
      root.dataset.font = current;
    }
    if (fontDec) fontDec.setAttribute("aria-disabled", String(index === 0));
    if (fontInc) fontInc.setAttribute("aria-disabled", String(index === FONTS.length - 1));
    if (persist) store(KEYS.font, current);
  };

  applyFont(root.dataset.font || raw(KEYS.font) || "md", false);

  if (fontInc) {
    fontInc.addEventListener("click", () => {
      const index = FONTS.indexOf(root.dataset.font || "md");
      applyFont(FONTS[Math.min(FONTS.length - 1, index + 1)], true);
    });
  }

  if (fontDec) {
    fontDec.addEventListener("click", () => {
      const index = FONTS.indexOf(root.dataset.font || "md");
      applyFont(FONTS[Math.max(0, index - 1)], true);
    });
  }

  /* ------------------------------------------------------------ 目录抽屉 */

  const drawer = document.getElementById("toc-drawer");
  const openButtons = document.querySelectorAll("[data-toc-open]");

  if (drawer) {
    let lastFocus = null;

    const focusable = () =>
      Array.from(
        drawer.querySelectorAll(
          'a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])'
        )
      ).filter((el) => el.offsetParent !== null);

    const open = () => {
      lastFocus = document.activeElement;
      drawer.classList.add("open");
      drawer.setAttribute("aria-hidden", "false");
      document.body.classList.add("no-scroll");
      const first = drawer.querySelector("[data-close]");
      if (first) first.focus();
    };

    const close = () => {
      drawer.classList.remove("open");
      drawer.setAttribute("aria-hidden", "true");
      document.body.classList.remove("no-scroll");
      if (lastFocus && lastFocus.focus) lastFocus.focus();
    };

    openButtons.forEach((btn) => btn.addEventListener("click", open));

    drawer.addEventListener("click", (event) => {
      if (event.target.closest("[data-close]")) close();
    });

    document.addEventListener("keydown", (event) => {
      if (!drawer.classList.contains("open")) return;
      if (event.key === "Escape") {
        close();
        return;
      }
      if (event.key !== "Tab") return;
      const items = focusable();
      if (!items.length) return;
      const first = items[0];
      const last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    });
  }

  /* ------------------------------------------------------------ 阅读进度 */

  const chapter = document.querySelector("[data-chapter-id]");
  const progressBar = document.querySelector(".progress span");
  const readMap = readJson(KEYS.read, {}) || {};

  const scrollPercent = () => {
    const height = document.documentElement.scrollHeight - window.innerHeight;
    if (height <= 0) return 0;
    return Math.min(100, Math.max(0, (window.scrollY / height) * 100));
  };

  const markRead = (id) => {
    if (!id || readMap[id]) return;
    readMap[id] = Date.now();
    writeJson(KEYS.read, readMap);
    document.querySelectorAll('[data-chapter-id="' + id + '"]').forEach((el) => {
      el.classList.add("is-read");
    });
  };

  if (chapter) {
    const id = chapter.dataset.chapterId;
    const url = chapter.dataset.chapterUrl;
    const label = chapter.dataset.chapterLabel;

    let lastSave = 0;
    let restoring = false;

    const save = () => {
      if (!url || restoring) return;
      writeJson(KEYS.last, {
        url,
        label,
        pct: Math.round(scrollPercent() * 10) / 10,
        at: Date.now(),
      });
    };

    const onScroll = () => {
      const pct = scrollPercent();
      if (progressBar) progressBar.style.width = pct.toFixed(2) + "%";
      if (pct > 92) markRead(id);
      const now = Date.now();
      if (now - lastSave > 900) {
        lastSave = now;
        save();
      }
    };

    let ticking = false;
    window.addEventListener(
      "scroll",
      () => {
        if (ticking) return;
        ticking = true;
        window.requestAnimationFrame(() => {
          ticking = false;
          onScroll();
        });
      },
      { passive: true }
    );

    window.addEventListener("pagehide", save);
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "hidden") save();
    });

    const last = readJson(KEYS.last, null);
    const samePlace =
      last && last.url === url && last.pct > 4 && last.pct < 97 && !window.location.hash;

    if (samePlace) {
      restoring = true;
      const restore = () => {
        const height = document.documentElement.scrollHeight - window.innerHeight;
        if (height > 0) window.scrollTo(0, (last.pct / 100) * height);
        restoring = false;
      };
      if (document.fonts && document.fonts.ready) {
        document.fonts.ready.then(() => setTimeout(restore, 60));
      } else {
        window.addEventListener("load", () => setTimeout(restore, 60), { once: true });
      }
    }

    onScroll();
  }

  /* ------------------------------------------------------------ 已读标记（卷页） */

  document.querySelectorAll("li[data-chapter-id]").forEach((li) => {
    if (readMap[li.dataset.chapterId]) li.classList.add("is-read");
  });

  /* ------------------------------------------------------------ 续读入口（首页） */

  const continueLink = document.querySelector("[data-continue]");
  if (continueLink) {
    const last = readJson(KEYS.last, null);
    if (last && last.url) {
      continueLink.href = last.url;
      continueLink.hidden = false;
      const label = continueLink.querySelector("[data-continue-label]");
      if (label) label.textContent = last.label || "上次读到的章节";
      const wrap = document.querySelector("[data-continue-wrap]");
      if (wrap) wrap.hidden = false;
    }
  }

  /* ------------------------------------------------------------ 键盘翻页 */

  document.addEventListener("keydown", (event) => {
    if (event.metaKey || event.ctrlKey || event.altKey) return;
    if (event.target.closest("input, textarea, select, [contenteditable]")) return;
    if (drawer && drawer.classList.contains("open")) return;
    if (event.key === "ArrowLeft") {
      const prev = document.querySelector("[data-prev-chapter]");
      if (prev) window.location.href = prev.href;
    } else if (event.key === "ArrowRight") {
      const next = document.querySelector("[data-next-chapter]");
      if (next) window.location.href = next.href;
    }
  });
})();
