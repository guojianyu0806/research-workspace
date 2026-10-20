"use strict";

/* Saved translation content only. Optional paging never invokes a model. */
(() => {
  const instances = new WeakMap();
  const sides = ["source", "translation"];
  const clamp = (value, low, high) => Math.min(Math.max(value, low), high);
  const node = (tag, name, text) => {
    const value = document.createElement(tag);
    if (name) value.className = name;
    if (text !== undefined) value.textContent = String(text);
    return value;
  };
  let serial = 0;

  function render(container, jobResult, options = {}) {
    if (!(container instanceof Element)) throw new TypeError("A DOM container is required.");
    const prior = instances.get(container), previous = prior?.getPosition();
    prior?.destroy();
    const result = jobResult && typeof jobResult === "object" ? jobResult : {};
    const paging = options.paging;
    const full = Array.isArray(result.blocks) ? result.blocks : [];
    const catalog = paging ? paging.index : full.map((block, ordinal) => ({
      ordinal, block_id: typeof block?.block_id === "string" ? block.block_id : null,
      source_chars: typeof block?.source_text === "string" ? block.source_text.length : 0,
      translation_chars: typeof block?.translated_text === "string" ? block.translated_text.length : 0,
      source_newlines: typeof block?.source_text === "string" ? block.source_text.split("\n").length - 1 : 0,
      translation_newlines: typeof block?.translated_text === "string" ? block.translated_text.split("\n").length - 1 : 0,
    }));
    if (!Array.isArray(catalog) || catalog.some((entry, i) => entry.ordinal !== i)) throw new Error("译文索引顺序无效，请刷新任务。");
    const total = catalog.length, identity = new Map(), pages = new Map(), pending = new Set(), failed = new Map();
    catalog.forEach((entry, i) => {
      if (typeof entry.block_id !== "string" || !entry.block_id) return;
      const prior = identity.get(entry.block_id);
      identity.set(entry.block_id, prior === undefined ? i : -1);
    });
    const root = node("section", "rb-reader"), id = `research-bilingual-${++serial}`;
    root.setAttribute("aria-label", "原文与译文对照阅读");
    const heading = node("div", "rb-heading"), title = node("div", "rb-title-group");
    title.append(node("span", "rb-eyebrow", "BILINGUAL READING"), node("h3", "rb-title", "原文与译文"));
    heading.append(title, node("span", "rb-count", `${total} 个已保存文本块`)); root.append(heading);
    const explanation = node("p", "rb-explanation", "拖动任一栏，另一栏会按对应段落联动；点击出处可回到原文页。");
    explanation.id = `${id}-explanation`; root.append(explanation);
    const missing = Array.isArray(result.missing_pages) ? result.missing_pages : [];
    if (result.status === "partial" || missing.length || (Number.isFinite(result.total_blocks) && total < result.total_blocks)) {
      const note = node("div", "rb-partial"); note.setAttribute("role", "status");
      note.append(node("p", "", `当前结果尚不完整。已保存 ${total}${Number.isFinite(result.total_blocks) ? `/${result.total_blocks}` : ""} 个文本块。`));
      if (missing.length) {
        const details = node("details", "rb-missing"), list = node("ul", "rb-missing-list");
        details.append(node("summary", "", `${missing.length} 页尚无完整可翻译文本`));
        const labels = { no_extractable_text: "无可提取文字", failed: "解析失败", pending: "尚未解析", needs_ocr: "待文字识别" };
        for (const item of missing) {
          const value = item && typeof item === "object" ? item : { page: item };
          list.append(node("li", "", `第 ${Number.isInteger(value.page) ? value.page : "未知"} 页 · ${labels[value.status] || "待处理"}`));
        }
        details.append(list); note.append(details);
      }
      root.append(note);
    }
    const feedback = node("p", "rb-feedback"); feedback.hidden = true; feedback.setAttribute("role", "status"); root.append(feedback);
    const notify = message => {
      if (destroyed || suspended) return;
      if (window.ResearchNotices) window.ResearchNotices.show(message, true, feedback);
      else { feedback.textContent = message; feedback.hidden = false; }
    };
    const panes = {}, listeners = [], programmatic = new Map();
    let destroyed = false, suspended = false, stale = false, frame = 0, lastSide = "source", lastAnchor = null, suspendedAnchor = null;
    const alive = () => !destroyed && !suspended;
    const listen = (target, event, callback, settings) => {
      target.addEventListener(event, callback, settings);
      listeners.push(() => target.removeEventListener(event, callback, settings));
    };
    const record = ordinal => paging ? pages.get(Math.floor(ordinal / 100) * 100)?.blocks[ordinal % 100] : full[ordinal];
    const stateFor = ordinal => paging ? pages.get(Math.floor(ordinal / 100) * 100)?.states[ordinal % 100] : null;
    if (paging?.initial_page) pages.set(paging.initial_page.offset, { blocks: paging.initial_page.blocks, states: paging.initial_page.record_metadata || [] });
    const validAnchor = (block, ordinal) => {
      const box = block?.bbox;
      return !!block && identity.get(block.block_id) === ordinal && Number.isInteger(block.page) && block.page > 0
        && typeof block.document_id === "string" && !!block.document_id && typeof block.run_id === "string" && !!block.run_id
        && (!result.document_id || result.document_id === block.document_id) && (!result.run_id || result.run_id === block.run_id)
        && Array.isArray(box) && box.length === 4 && box.every(v => typeof v === "number" && Number.isFinite(v) && v >= 0 && v <= 1)
        && box[0] < box[2] && box[1] < box[3] && stateFor(ordinal)?.anchor_valid !== false;
    };

    const columns = node("div", "rb-columns");
    for (const side of sides) {
      const panel = node("section", `rb-panel rb-panel-${side}`), label = side === "source" ? "原文" : result.target_language === "en" ? "学术英文译文" : "译文";
      const paneTitle = node("h4", "rb-pane-title", label); paneTitle.id = `${id}-${side}-title`;
      const scroll = node("div", "rb-scroll"); scroll.tabIndex = 0; scroll.dataset.side = side;
      scroll.setAttribute("role", "region"); scroll.setAttribute("aria-labelledby", paneTitle.id); scroll.setAttribute("aria-describedby", explanation.id);
      const content = node("div", "rb-content"), top = node("div", "rb-spacer"), bottom = node("div", "rb-spacer"), tail = node("div", "rb-tail");
      for (const item of [top, bottom, tail]) item.setAttribute("aria-hidden", "true");
      content.append(top, bottom, tail); scroll.append(content); panel.append(paneTitle, scroll); columns.append(panel);
      panes[side] = { panel, scroll, content, top, bottom, tail, label, elements: new Map(), heights: new Float64Array(total), sums: new Float64Array(total + 1), width: 0, font: "", start: 0, end: 0, padding: 0 };
    }
    if (total) root.append(columns); else root.append(node("p", "rb-empty", "尚无可显示的双语文本块。翻译结果保存后会显示在这里。"));
    container.replaceChildren(root);

    function rebuild(pane) { for (let i = 0; i < total; i++) pane.sums[i + 1] = pane.sums[i] + pane.heights[i]; }
    function resetEstimates(side) {
      const pane = panes[side], text = pane.content.querySelector(".rb-text"), style = getComputedStyle(text || pane.scroll);
      const font = `${style.fontSize}/${style.lineHeight}/${style.fontFamily}`;
      const width = pane.scroll.clientWidth;
      pane.padding = parseFloat(getComputedStyle(pane.content).paddingTop) || 0;
      if (width === pane.width && font === pane.font) return;
      pane.width = width; pane.font = font;
      const size = text ? parseFloat(style.fontSize) || 16 : 16, line = text ? parseFloat(style.lineHeight) || size * 1.85 : size * 1.85;
      const chars = Math.max(8, (width - 52) / (size * (side === "source" ? .55 : 1)));
      catalog.forEach((entry, i) => {
        const length = Number(entry[side === "source" ? "source_chars" : "translation_chars"]) || 0;
        const breaks = Number(entry[side === "source" ? "source_newlines" : "translation_newlines"]) || 0;
        pane.heights[i] = 83 + Math.max(1, breaks + 1, Math.ceil(length / chars)) * line;
      });
      rebuild(pane);
    }
    function locate(pane, top) {
      let low = 0, high = total - 1;
      const y = top - pane.padding;
      while (low < high) { const mid = (low + high) >>> 1; if (pane.sums[mid + 1] <= y + .1) low = mid + 1; else high = mid; }
      return low;
    }
    function position(side) {
      if (!total) return null;
      const pane = panes[side], ordinal = locate(pane, pane.scroll.scrollTop), delta = pane.scroll.scrollTop - pane.padding - pane.sums[ordinal];
      return { ordinal, block_id: catalog[ordinal].block_id, document_id: result.document_id, run_id: result.run_id,
        fraction: clamp(delta / pane.heights[ordinal], 0, 1), leading: Math.min(delta, 0), side };
    }
    function article(side, ordinal) {
      const pane = panes[side], block = record(ordinal), entry = node("article", "rb-block");
      entry.dataset.ordinal = ordinal;
      if (catalog[ordinal].block_id) entry.dataset.blockId = catalog[ordinal].block_id;
      entry.setAttribute("aria-posinset", ordinal + 1); entry.setAttribute("aria-setsize", total);
      const meta = node("div", "rb-block-meta");
      meta.append(node("span", "rb-page-label", Number.isInteger(block?.page) ? `第 ${block.page} 页` : `第 ${ordinal + 1} 段`));
      const evidence = node("button", "rb-evidence", "查看出处"); evidence.type = "button"; evidence.dataset.ordinal = ordinal;
      evidence.setAttribute("aria-label", `${pane.label} · ${Number.isInteger(block?.page) ? `第 ${block.page} 页` : "原文位置"} · 查看原文出处`);
      evidence.disabled = typeof options.onEvidence !== "function" || !validAnchor(block, ordinal);
      if (evidence.disabled && block !== undefined) evidence.title = "来源标识缺失、重复或不一致，暂不能唯一定位。";
      meta.append(evidence); entry.append(meta);
      if (paging && block === undefined) {
        entry.classList.add("rb-block-loading"); entry.setAttribute("aria-busy", "true");
        entry.style.minHeight = `${pane.heights[ordinal]}px`;
        const offset = Math.floor(ordinal / 100) * 100;
        entry.append(node("p", "rb-text rb-text-missing", stale ? "保存版本已变化，请刷新资料与任务后继续。" : failed.has(offset) ? "本段加载失败，原记录仍保留。" : "正在读取已保存内容……"));
        if (failed.has(offset) && !stale) { const retry = node("button", "rb-retry", "重试读取"); retry.type = "button"; retry.dataset.offset = offset; entry.append(retry); }
      } else {
        const raw = side === "source" ? block?.source_text : block?.translated_text;
        const text = node("p", "rb-text", typeof raw === "string" && raw.trim() ? raw : side === "source" ? "原文内容暂不可用" : "本段尚无已保存译文");
        if (typeof raw !== "string" || !raw.trim()) text.classList.add("rb-text-missing");
        entry.append(text);
      }
      return { element: entry, block, stale, failure: failed.has(Math.floor(ordinal / 100) * 100) };
    }
    function windowFor(side, anchor) {
      const pane = panes[side], center = clamp(anchor.ordinal, 0, total - 1);
      const start = total <= 120 ? 0 : Math.max(0, center - 6);
      const bottom = pane.padding + pane.sums[center] + pane.heights[center] * anchor.fraction + pane.scroll.clientHeight;
      const end = total <= 120 ? total : Math.min(total, start + 120, Math.max(center + 9, locate(pane, bottom) + 9));
      pane.start = start; pane.end = end;
      for (const [i, value] of pane.elements) if (i < start || i >= end) { observer?.unobserve(value.element); value.element.remove(); pane.elements.delete(i); }
      let cursor = pane.top.nextSibling;
      for (let i = start; i < end; i++) {
        let value = pane.elements.get(i);
        if (!value || value.block !== record(i) || value.failure !== failed.has(Math.floor(i / 100) * 100) || (value.block === undefined && value.stale !== stale)) {
          if (value) { observer?.unobserve(value.element); if (cursor === value.element) cursor = cursor.nextSibling; value.element.remove(); }
          value = article(side, i); pane.elements.set(i, value); observer?.observe(value.element);
        }
        if (cursor !== value.element) pane.content.insertBefore(value.element, cursor); else cursor = cursor.nextSibling;
      }
    }
    function spacer(pane) {
      pane.top.style.height = `${pane.sums[pane.start]}px`;
      pane.bottom.style.height = `${pane.sums[total] - pane.sums[pane.end]}px`;
      pane.tail.style.height = `${Math.max(0, pane.scroll.clientHeight - pane.padding)}px`;
    }
    function measure() {
      for (const side of sides) {
        const pane = panes[side];
        for (const [i, item] of pane.elements) {
          if (item.block !== undefined || !paging) {
            const height = item.element.getBoundingClientRect().height;
            if (height > 0) pane.heights[i] = height;
          }
        }
        rebuild(pane); spacer(pane);
      }
    }
    function align(side, anchor) {
      const pane = panes[side], i = anchor.ordinal;
      const top = pane.padding + pane.sums[i] + (anchor.leading < 0 ? anchor.leading : clamp(anchor.fraction, 0, 1) * pane.heights[i]);
      const requested = clamp(top, 0, Math.max(0, pane.scroll.scrollHeight - pane.scroll.clientHeight));
      programmatic.set(side, requested); if (Math.abs(pane.scroll.scrollTop - requested) > .2) pane.scroll.scrollTop = requested;
      // Browsers round subpixel scroll positions. Compare with the applied value.
      programmatic.set(side, pane.scroll.scrollTop);
      for (const [ordinal, item] of pane.elements) item.element.classList.toggle("rb-block-active", ordinal === i);
    }
    function wantedPages() {
      const wanted = new Set();
      for (const side of sides) for (let i = panes[side].start; i < panes[side].end; i++) wanted.add(Math.floor(i / 100) * 100);
      return wanted;
    }
    function fetchVisible() {
      if (!paging || !alive() || stale || !total) return;
      const wanted = wantedPages();
      for (const offset of wanted) {
        if (pages.has(offset)) { const value = pages.get(offset); pages.delete(offset); pages.set(offset, value); continue; }
        if (pending.size >= 2) break;
        if (pending.has(offset) || failed.has(offset)) continue;
        pending.add(offset);
        Promise.resolve().then(() => paging.load(offset, 100)).then(page => {
          if (destroyed) return;
          if (page.revision !== paging.revision || page.total !== total || page.offset !== offset
            || !Array.isArray(page.blocks) || page.blocks.length !== Math.min(100, total - offset)
            || page.blocks.some((block, i) => (typeof block?.block_id === "string" ? block.block_id : null) !== catalog[offset + i].block_id)) {
            throw new Error("译文保存版本或索引已变化，请刷新任务。");
          }
          pages.set(offset, { blocks: page.blocks, states: page.record_metadata || [] });
          const protectedPages = wantedPages();
          for (const key of pages.keys()) if (pages.size > 8 && !protectedPages.has(key)) pages.delete(key);
        }).catch(error => {
          if (destroyed) return;
          failed.set(offset, error.message);
          if (error.status === 409 || /版本|索引/.test(error.message)) { stale = true; options.onStale?.(error); }
          notify(error.message || "译文读取失败，可重试；原记录保留。");
        }).finally(() => { pending.delete(offset); if (alive()) schedule(); });
      }
    }
    function flush() {
      frame = 0;
      if (!alive() || !total) return;
      const anchor = lastAnchor || position(lastSide) || { ordinal: 0, fraction: 0, leading: -12, side: lastSide };
      for (const side of sides) resetEstimates(side);
      for (const side of sides) windowFor(side, anchor);
      measure();
      for (const side of sides) align(side, anchor);
      lastAnchor = { ...anchor, side: lastSide };
      fetchVisible();
    }
    function schedule() { if (alive() && !frame) frame = requestAnimationFrame(flush); }
    const observer = typeof ResizeObserver === "function" ? new ResizeObserver(schedule) : null;
    for (const side of sides) {
      const pane = panes[side];
      listen(pane.scroll, "scroll", () => {
        if (!alive() || !total) return;
        if (programmatic.has(side) && Math.abs(pane.scroll.scrollTop - programmatic.get(side)) <= 1) return;
        lastSide = side; lastAnchor = position(side); schedule();
      }, { passive: true });
      for (const event of ["wheel", "touchstart", "pointerdown", "keydown"]) listen(pane.scroll, event, () => {
        if (!alive()) return;
        lastSide = side; programmatic.delete(side);
      }, { passive: true });
      listen(pane.content, "click", async event => {
        const retry = event.target.closest(".rb-retry");
        if (retry) { failed.delete(Number(retry.dataset.offset)); schedule(); return; }
        const button = event.target.closest(".rb-evidence");
        if (!button || button.disabled || !alive()) return;
        const ordinal = Number(button.dataset.ordinal), block = record(ordinal);
        if (!validAnchor(block, ordinal)) return;
        try { await options.onEvidence({ document_id: block.document_id, run_id: block.run_id, block_id: block.block_id, page: block.page, bbox: block.bbox, evidence_id: block.evidence_id }); }
        catch { if (!destroyed) notify("暂时无法打开这条原文出处，请稍后重试。"); }
      });
      observer?.observe(pane.scroll);
    }
    listen(window, "resize", schedule, { passive: true });
    if (document.fonts?.addEventListener) listen(document.fonts, "loadingdone", schedule);
    document.fonts?.ready.then(() => { if (alive()) schedule(); });
    const controller = {
      getPosition() { return destroyed ? null : suspended ? suspendedAnchor : total ? { ...(lastAnchor || position(lastSide)), side: lastSide,
        source_key: options.sourceKey, ambiguous_block_id: identity.get((lastAnchor || position(lastSide)).block_id) === -1 } : null; },
      syncTo(blockId, settings = {}) {
        if (!alive() || !total) return false;
        if (settings.source_key !== undefined && settings.source_key !== options.sourceKey) return false;
        const i = Number.isInteger(settings.ordinal) && catalog[settings.ordinal]?.block_id === blockId ? settings.ordinal : identity.get(blockId);
        if (!Number.isInteger(i) || i < 0 || i >= total) return false;
        if ((settings.document_id && result.document_id && settings.document_id !== result.document_id) || (settings.run_id && result.run_id && settings.run_id !== result.run_id)) return false;
        lastSide = settings.side === "translation" ? "translation" : "source";
        lastAnchor = { ordinal: i, block_id: catalog[i].block_id, document_id: result.document_id, run_id: result.run_id,
          fraction: Number.isFinite(settings.fraction) ? clamp(settings.fraction, 0, 1) : 0,
          leading: Number.isFinite(settings.leading) ? Math.min(settings.leading, 0) : 0, side: lastSide };
        cancelAnimationFrame(frame); flush(); return true;
      },
      suspend() {
        if (!alive()) return;
        suspendedAnchor = controller.getPosition(); suspended = true; observer?.disconnect(); cancelAnimationFrame(frame); frame = 0;
      },
      resume() {
        if (destroyed || !suspended) return;
        suspended = false; lastAnchor = suspendedAnchor; suspendedAnchor = null;
        for (const side of sides) { observer?.observe(panes[side].scroll); for (const item of panes[side].elements.values()) observer?.observe(item.element); }
        flush();
      },
      destroy() {
        if (destroyed) return;
        destroyed = true; observer?.disconnect(); cancelAnimationFrame(frame);
        for (const remove of listeners) remove(); pages.clear();
        if (instances.get(container) === controller) instances.delete(container);
      },
    };
    instances.set(container, controller);
    for (const side of sides) resetEstimates(side);
    lastAnchor = { ordinal: 0, block_id: catalog[0]?.block_id, fraction: 0, leading: -panes.source.padding, side: "source" };
    flush();
    if (previous && previous.source_key === options.sourceKey && !previous.ambiguous_block_id) controller.syncTo(previous.block_id, previous);
    return controller;
  }
  window.ResearchBilingual = Object.freeze({ render });
})();
