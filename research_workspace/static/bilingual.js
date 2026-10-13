"use strict";

/* Real translation results only. This widget performs no model or network call. */
(() => {
  const instances = new WeakMap();
  let instanceNumber = 0;

  function node(tag, className, text) {
    const result = document.createElement(tag);
    if (className) result.className = className;
    if (text !== undefined) result.textContent = String(text);
    return result;
  }

  const clamp = (value, min, max) => Math.min(Math.max(value, min), max);

  function render(container, jobResult, options = {}) {
    if (!(container instanceof Element)) throw new TypeError("A DOM container is required.");
    const prior = instances.get(container);
    const previousPosition = prior ? prior.getPosition() : null;
    if (prior) prior.destroy();
    const result = jobResult && typeof jobResult === "object" ? jobResult : {};
    const seen = new Set();
    const blocks = (Array.isArray(result.blocks) ? result.blocks : []).filter((block) => {
      if (!block || typeof block.block_id !== "string" || !block.block_id || seen.has(block.block_id)) return false;
      seen.add(block.block_id);
      return true;
    });
    const missing = Array.isArray(result.missing_pages) ? result.missing_pages : [];
    const id = `research-bilingual-${++instanceNumber}`;
    const root = node("section", "rb-reader");
    root.setAttribute("aria-label", "原文与译文对照阅读");
    const heading = node("div", "rb-heading");
    const titleGroup = node("div", "rb-title-group");
    titleGroup.append(node("span", "rb-eyebrow", "BILINGUAL READING"), node("h3", "rb-title", "原文与译文"));
    const count = node("span", "rb-count", `${blocks.length} 个已保存文本块`);
    heading.append(titleGroup, count);
    root.append(heading);

    const explanation = node("p", "rb-explanation", "拖动任一栏，另一栏会按对应段落联动；点击出处可回到原文页。");
    explanation.id = `${id}-explanation`;
    root.append(explanation);
    const isIncomplete = result.status === "partial" || missing.length > 0
      || (Number.isFinite(result.total_blocks) && blocks.length < result.total_blocks);
    if (isIncomplete) {
      const note = node("div", "rb-partial");
      note.setAttribute("role", "status");
      const counts = Number.isFinite(result.total_blocks) ? `已保存 ${blocks.length}/${result.total_blocks} 个文本块。` : "仅显示已保存的翻译结果。";
      note.append(node("p", "", `当前结果尚不完整。${counts}`));
      if (missing.length) {
        const details = node("details", "rb-missing");
        details.append(node("summary", "", `${missing.length} 页尚无完整可翻译文本`));
        const list = node("ul", "rb-missing-list");
        const labels = { no_extractable_text: "无可提取文字", failed: "解析失败", pending: "尚未解析", needs_ocr: "待文字识别" };
        for (const value of missing) {
          const entry = value && typeof value === "object" ? value : { page: value };
          const number = Number.isInteger(entry.page) ? entry.page : "未知";
          list.append(node("li", "", `第 ${number} 页 · ${labels[entry.status] || "待处理"}`));
        }
        details.append(list);
        note.append(details);
      }
      root.append(note);
    }
    const feedback = node("p", "rb-feedback");
    feedback.setAttribute("role", "status");
    feedback.hidden = true;
    root.append(feedback);

    if (!blocks.length) {
      root.append(node("p", "rb-empty", "尚无可显示的双语文本块。翻译结果保存后会显示在这里。"));
      container.replaceChildren(root);
      const empty = { destroy() { if (instances.get(container) === empty) instances.delete(container); }, getPosition() { return null; }, syncTo() { return false; }, suspend() {}, resume() {} };
      instances.set(container, empty);
      return empty;
    }

    const columns = node("div", "rb-columns");
    const panes = {};
    const listeners = [];
    let destroyed = false;
    let suspended = false;
    let suspendedPosition = null;
    let lockedSide = null;
    let lastSource = "source";
    let syncFrame = 0;
    let measureFrame = 0;
    let releaseFrame = 0;
    let releaseInnerFrame = 0;
    let currentBlockId = null;
    const measurements = { source: [], translation: [] };

    function listen(target, type, handler, settings) {
      target.addEventListener(type, handler, settings);
      listeners.push(() => target.removeEventListener(type, handler, settings));
    }

    for (const side of ["source", "translation"]) {
      const panel = node("section", `rb-panel rb-panel-${side}`);
      const label = side === "source" ? "原文" : result.target_language === "en" ? "学术英文译文" : "译文";
      const paneHeading = node("h4", "rb-pane-title", label);
      paneHeading.id = `${id}-${side}-title`;
      panel.append(paneHeading);
      const scroll = node("div", "rb-scroll");
      scroll.tabIndex = 0;
      scroll.setAttribute("role", "region");
      scroll.setAttribute("aria-labelledby", paneHeading.id);
      scroll.setAttribute("aria-describedby", explanation.id);
      scroll.dataset.side = side;
      const content = node("div", "rb-content");
      const blockElements = new Map();
      const fragment = document.createDocumentFragment();
      for (const block of blocks) {
        const article = node("article", "rb-block");
        article.dataset.blockId = block.block_id;
        const meta = node("div", "rb-block-meta");
        const pageLabel = Number.isInteger(block.page) ? `第 ${block.page} 页` : "原文位置";
        meta.append(node("span", "rb-page-label", pageLabel));
        const evidence = node("button", "rb-evidence", "查看出处");
        evidence.type = "button";
        evidence.setAttribute("aria-label", `${label} · ${pageLabel} · 查看原文出处`);
        evidence.disabled = typeof options.onEvidence !== "function" || !Number.isInteger(block.page)
          || typeof block.document_id !== "string" || typeof block.run_id !== "string";
        listen(evidence, "click", async () => {
          feedback.hidden = true;
          try {
            await options.onEvidence({ document_id: block.document_id, run_id: block.run_id,
              block_id: block.block_id, page: block.page, bbox: block.bbox,
              evidence_id: block.evidence_id });
          } catch {
            if (!destroyed) {
              feedback.textContent = "暂时无法打开这条原文出处，请稍后重试。";
              feedback.hidden = false;
            }
          }
        });
        meta.append(evidence);
        const rawText = side === "source" ? block.source_text : block.translated_text;
        const text = node("p", "rb-text", typeof rawText === "string" && rawText.trim() ? rawText
          : side === "source" ? "原文内容暂不可用" : "本段尚无已保存译文");
        if (typeof rawText !== "string" || !rawText.trim()) text.classList.add("rb-text-missing");
        article.append(meta, text);
        blockElements.set(block.block_id, article);
        fragment.append(article);
      }
      const tail = node("div", "rb-tail");
      tail.setAttribute("aria-hidden", "true");
      content.append(fragment, tail);
      scroll.append(content);
      panel.append(scroll);
      columns.append(panel);
      panes[side] = { panel, scroll, content, tail, blockElements };
    }
    root.append(columns);
    container.replaceChildren(root);

    function setActive(blockId) {
      if (currentBlockId === blockId) return;
      for (const side of ["source", "translation"]) {
        if (currentBlockId) panes[side].blockElements.get(currentBlockId)?.classList.remove("rb-block-active");
        panes[side].blockElements.get(blockId)?.classList.add("rb-block-active");
      }
      currentBlockId = blockId;
    }

    function measure() {
      if (destroyed || suspended) return;
      for (const side of ["source", "translation"]) {
        const pane = panes[side];
        const tailHeight = `${Math.max(pane.scroll.clientHeight - 12, 0)}px`;
        if (pane.tail.style.height !== tailHeight) pane.tail.style.height = tailHeight;
        measurements[side] = blocks.map((block) => {
          const article = pane.blockElements.get(block.block_id);
          return { block_id: block.block_id, top: article.offsetTop, height: Math.max(article.offsetHeight, 1) };
        });
      }
    }

    function position(side) {
      const entries = measurements[side];
      if (!entries.length) return null;
      const top = panes[side].scroll.scrollTop;
      let low = 0, high = entries.length - 1;
      while (low < high) {
        const middle = Math.floor((low + high) / 2);
        if (entries[middle].top + entries[middle].height <= top + 0.5) low = middle + 1;
        else high = middle;
      }
      const entry = entries[low];
      return { block_id: entry.block_id, fraction: clamp((top - entry.top) / entry.height, 0, 1),
        leading: Math.min(top - entry.top, 0), side };
    }

    function releaseLockSoon() {
      cancelAnimationFrame(releaseFrame);
      cancelAnimationFrame(releaseInnerFrame);
      releaseFrame = requestAnimationFrame(() => {
        if (destroyed || suspended) return;
        releaseInnerFrame = requestAnimationFrame(() => { if (!destroyed && !suspended) lockedSide = null; });
      });
    }

    function align(targetSide, anchor) {
      const entry = measurements[targetSide].find((item) => item.block_id === anchor.block_id);
      if (!entry) return false;
      const target = panes[targetSide].scroll;
      const requested = entry.top + (anchor.leading < 0 ? anchor.leading : clamp(anchor.fraction, 0, 1) * entry.height);
      const bounded = clamp(requested, 0, Math.max(target.scrollHeight - target.clientHeight, 0));
      if (Math.abs(target.scrollTop - bounded) > 0.5) {
        lockedSide = targetSide;
        target.scrollTop = bounded;
        releaseLockSoon();
      }
      setActive(anchor.block_id);
      return true;
    }

    function synchronize(sourceSide) {
      if (destroyed || suspended) return;
      const anchor = position(sourceSide);
      if (anchor) align(sourceSide === "source" ? "translation" : "source", anchor);
    }

    for (const side of ["source", "translation"]) {
      const scroll = panes[side].scroll;
      listen(scroll, "scroll", () => {
        if (destroyed || suspended || lockedSide === side) return;
        lastSource = side;
        cancelAnimationFrame(syncFrame);
        syncFrame = requestAnimationFrame(() => synchronize(side));
      }, { passive: true });
      // A deliberate gesture on the other column immediately takes ownership.
      const takeControl = () => {
        if (destroyed || suspended) return;
        lockedSide = null;
        lastSource = side;
        cancelAnimationFrame(syncFrame);
      };
      for (const type of ["wheel", "touchstart", "pointerdown", "keydown"]) listen(scroll, type, takeControl, { passive: true });
    }

    function scheduleMeasure() {
      if (destroyed || suspended) return;
      cancelAnimationFrame(measureFrame);
      measureFrame = requestAnimationFrame(() => { if (!destroyed && !suspended) { measure(); synchronize(lastSource); } });
    }
    const observer = typeof ResizeObserver === "function" ? new ResizeObserver(scheduleMeasure) : null;
    function observePanes() {
      if (!observer) return;
      for (const side of ["source", "translation"]) {
        observer.observe(panes[side].scroll);
        observer.observe(panes[side].content);
      }
    }
    observePanes();
    listen(window, "resize", scheduleMeasure, { passive: true });
    measure();

    const controller = {
      getPosition() { return destroyed ? null : suspended ? suspendedPosition : position(lastSource); },
      syncTo(blockId, settings = {}) {
        if (destroyed || suspended) return false;
        measure();
        const side = settings.side === "translation" ? "translation" : "source";
        const anchor = { block_id: blockId, fraction: Number.isFinite(settings.fraction) ? settings.fraction : 0,
          leading: Number.isFinite(settings.leading) ? Math.min(settings.leading, 0) : 0 };
        if (!panes[side].blockElements.has(blockId)) return false;
        lastSource = side;
        align(side, anchor);
        align(side === "source" ? "translation" : "source", anchor);
        return true;
      },
      suspend() {
        if (destroyed || suspended) return;
        suspendedPosition = position(lastSource);
        suspended = true;
        observer?.disconnect();
        for (const frame of [syncFrame, measureFrame, releaseFrame, releaseInnerFrame]) cancelAnimationFrame(frame);
        lockedSide = null;
      },
      resume() {
        if (destroyed || !suspended) return;
        suspended = false;
        measure();
        if (suspendedPosition) controller.syncTo(suspendedPosition.block_id, suspendedPosition);
        suspendedPosition = null;
        observePanes();
      },
      destroy() {
        destroyed = true;
        for (const remove of listeners) remove();
        observer?.disconnect();
        for (const frame of [syncFrame, measureFrame, releaseFrame, releaseInnerFrame]) cancelAnimationFrame(frame);
        if (instances.get(container) === controller) instances.delete(container);
      },
    };
    instances.set(container, controller);
    if (previousPosition) controller.syncTo(previousPosition.block_id, previousPosition);
    return controller;
  }

  window.ResearchBilingual = Object.freeze({ render });
})();
