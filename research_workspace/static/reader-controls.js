"use strict";

/* Reader-only presentation controls. No document, account or model data is written. */
(() => {
  const byId = (id) => document.getElementById(id);
  const workspace = byId("workspace");
  const reader = byId("readerPanel");
  const viewport = byId("readerViewport");
  const paper = byId("paperPage");
  let image = byId("pageImage");
  const zoomSelect = byId("pageZoom");
  if (!workspace || !reader || !viewport || !paper || !image || !zoomSelect) return;

  const library = byId("libraryPanel");
  const evidence = byId("evidencePanel");
  const libraryButton = byId("toggleLibrary");
  const evidenceButton = byId("toggleEvidence");
  const focusButton = byId("readerFocusButton");
  const fullscreenButton = byId("readerFullscreenButton");
  const zoomOut = byId("zoomOutButton");
  const zoomIn = byId("zoomInButton");
  const info = byId("readerInfo");
  const infoButton = byId("readerInfoToggle");
  const adjustButton = byId("readerAdjustButton");
  const adjustPopover = byId("readerAdjustPopover");
  const widthRange = byId("readerWidthRange");
  const widthValue = byId("readerWidthValue");
  const positionRange = byId("readerPositionRange");
  const positionValue = byId("readerPositionValue");
  const resetView = byId("readerResetView");
  const customZoom = byId("readerCustomZoom");
  const steps = [.5, .75, 1, 1.25, 1.5, 2, 3];
  const compact = window.matchMedia("(max-width: 999px)");
  let anchor = null;
  let frame = 0;
  let applying = false;
  let anchorLocked = false;
  let layoutRevision = 0;
  let focusSnapshot = null;
  let fullscreenSnapshot = null;
  let nativePending = false;
  let nativeEntered = false;
  let leavingReader = false;
  let horizontalPosition = .5;
  let pan = null;
  let suppressPanClick = false;
  let desktopPanels = { library: false, evidence: true };
  workspace.classList.toggle("library-collapsed", compact.matches);
  workspace.classList.add("evidence-collapsed");

  function label(button, text) {
    if (!button) return;
    (button.querySelector("[data-reader-label]") || button).textContent = text;
  }

  function currentAnchor(clientX, clientY) {
    if (paper.hidden || !paper.clientWidth || !viewport.clientWidth) return null;
    const surface = viewport.getBoundingClientRect();
    const page = paper.getBoundingClientRect();
    const x = clientX ?? surface.left + viewport.clientWidth / 2;
    const y = clientY ?? surface.top + viewport.clientHeight / 2;
    return {
      x: (x - page.left) / page.width,
      y: (y - page.top) / page.height,
      viewportX: (x - surface.left) / viewport.clientWidth,
      viewportY: (y - surface.top) / viewport.clientHeight,
      source: image.getAttribute("src"),
    };
  }

  function restoreAnchor(saved) {
    if (!saved || saved.source !== image.getAttribute("src")) return;
    const surface = viewport.getBoundingClientRect();
    const page = paper.getBoundingClientRect();
    viewport.scrollLeft += page.left + saved.x * page.width - surface.left - saved.viewportX * viewport.clientWidth;
    viewport.scrollTop += page.top + saved.y * page.height - surface.top - saved.viewportY * viewport.clientHeight;
  }

  function availableSize() {
    // Resolve the current grid/Fullscreen layout before measuring its scrollport.
    viewport.getBoundingClientRect();
    const style = getComputedStyle(viewport);
    return {
      width: Math.max(1, viewport.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight)),
      height: Math.max(1, viewport.clientHeight - parseFloat(style.paddingTop) - parseFloat(style.paddingBottom)),
    };
  }

  function pageRatio() {
    return (Number(image.getAttribute("width")) || image.naturalWidth || 1)
      / (Number(image.getAttribute("height")) || image.naturalHeight || 1);
  }

  function currentMultiplier() {
    if (zoomSelect.value !== "page") return Number(zoomSelect.value) || 1;
    const size = availableSize();
    const ratio = pageRatio();
    return Math.min(1, size.height * ratio / size.width);
  }

  function selectZoom(value) {
    const text = String(value);
    const preset = [...zoomSelect.options].find((option) => option !== customZoom && option.value === text);
    if (preset) zoomSelect.value = text;
    else if (customZoom && Number.isFinite(Number(value))) {
      customZoom.value = text;
      customZoom.textContent = `自定义 ${Math.round(Number(value) * 100)}%`;
      customZoom.hidden = false;
      zoomSelect.value = text;
    }
  }

  function horizontalSpace() {
    const width = parseFloat(paper.style.width) || paper.getBoundingClientRect().width;
    return Math.max(0, availableSize().width - width);
  }

  function alignPaper(width = parseFloat(paper.style.width) || paper.getBoundingClientRect().width) {
    // Explicit positive margins let a narrow page move within the canvas;
    // oversized pages start at the left and use the native scroll range.
    paper.style.marginLeft = `${Math.max(0, availableSize().width - width) * horizontalPosition}px`;
    paper.style.marginRight = "0px";
  }

  function syncAdjustControls() {
    const ready = !paper.hidden && Boolean(image.getAttribute("src"));
    const percent = Math.round(currentMultiplier() * 100);
    if (widthRange) {
      widthRange.disabled = !ready;
      widthRange.value = String(Math.min(300, Math.max(50, percent)));
    }
    if (widthValue) widthValue.textContent = `${percent}%${zoomSelect.value === "page" ? "（整页）" : ""}`;
    const availableWidth = ready ? availableSize().width : 0;
    const paperWidth = parseFloat(paper.style.width) || paper.getBoundingClientRect().width;
    const space = ready ? Math.max(0, availableWidth - paperWidth) : 0;
    const overflow = ready ? Math.max(0, paperWidth - availableWidth) : 0;
    const maxScroll = Math.max(0, viewport.scrollWidth - viewport.clientWidth);
    // Retain the requested fraction when native scrolling only rounded it to a
    // pixel. Otherwise a 1% keyboard step can be lost in a short scroll range.
    if (maxScroll > 1 && space < 1 && Math.abs(viewport.scrollLeft - horizontalPosition * maxScroll) > .75) {
      horizontalPosition = Math.min(1, Math.max(0, viewport.scrollLeft / maxScroll));
    }
    if (positionRange) {
      positionRange.disabled = !ready || (overflow <= 1 && space < 1);
      positionRange.value = String(Math.round(horizontalPosition * 100));
    }
    if (positionValue) positionValue.textContent = `${Math.round(horizontalPosition * 100)}%`;
    if (resetView) resetView.disabled = !ready;
    paper.classList.toggle("is-draggable", ready);
    viewport.classList.toggle("is-draggable", ready);
  }

  function setAdjustOpen(open, returnFocus = false) {
    if (!adjustPopover || !adjustButton) return;
    adjustPopover.hidden = !open;
    adjustPopover.inert = !open;
    adjustButton.setAttribute("aria-expanded", String(open));
    if (open) {
      syncAdjustControls();
      widthRange?.focus({ preventScroll: true });
    } else if (returnFocus) adjustButton.focus({ preventScroll: true });
  }

  function syncZoomButtons() {
    const ready = !paper.hidden && Boolean(image.getAttribute("src"));
    const multiplier = currentMultiplier();
    if (zoomOut) zoomOut.disabled = !ready || multiplier <= steps[0];
    if (zoomIn) zoomIn.disabled = !ready || multiplier >= steps.at(-1);
    zoomSelect.disabled = !ready;
    syncAdjustControls();
  }

  function reflow(saved = anchor) {
    if (!viewport.clientWidth || paper.hidden) { syncZoomButtons(); return; }
    applying = true;
    const size = availableSize();
    const ratio = pageRatio();
    const width = zoomSelect.value === "page"
      ? Math.min(size.width, size.height * ratio)
      : size.width * (Number(zoomSelect.value) || 1);
    paper.style.width = `${Math.max(1, width)}px`;
    paper.style.maxWidth = "none";
    alignPaper(width);
    restoreAnchor(saved);
    syncZoomButtons();
    window.requestAnimationFrame(() => {
      applying = false;
      // The rendered scroll range can settle a frame after a width change.
      // Refresh disabled states even when the viewport itself did not resize.
      syncZoomButtons();
    });
  }

  function scheduleReflow() {
    window.cancelAnimationFrame(frame);
    frame = window.requestAnimationFrame(() => reflow());
  }

  function holdLayoutAnchor(saved) {
    anchor = saved;
    anchorLocked = true;
    const revision = ++layoutRevision;
    let previousSize = "";
    let stableFrames = 0;
    // Scroll events also fire when a shrinking layout clamps the old offset.
    // Keep the intended page point until both the transition and resulting
    // scrollport/page geometry have settled, including scrollbar layout passes.
    function settleLayout() {
      if (revision !== layoutRevision) return;
      reflow(anchor);
      const animations = [workspace, info].flatMap((node) => node?.getAnimations?.() || []);
      const size = `${viewport.clientWidth}:${viewport.clientHeight}:${paper.clientWidth}:${paper.clientHeight}`;
      const running = animations.some((animation) => animation.playState === "running" || animation.pending);
      stableFrames = !running && size === previousSize ? stableFrames + 1 : 0;
      previousSize = size;
      if (stableFrames >= 3) { anchorLocked = false; anchor = currentAnchor(); }
      else window.requestAnimationFrame(settleLayout);
    }
    window.requestAnimationFrame(settleLayout);
  }

  function setZoom(value, point) {
    layoutRevision += 1;
    anchorLocked = false;
    anchor = point || currentAnchor();
    selectZoom(value);
    reflow(anchor);
    anchor = currentAnchor();
  }

  function setPosition(value) {
    if (paper.hidden) return;
    layoutRevision += 1;
    anchorLocked = false;
    horizontalPosition = Math.max(0, Math.min(1, Number(value)));
    alignPaper();
    viewport.scrollLeft = (viewport.scrollWidth - viewport.clientWidth) * horizontalPosition;
    anchor = currentAnchor();
    syncAdjustControls();
  }

  function endPan() {
    if (!pan) return;
    const pointerId = pan.pointerId;
    suppressPanClick = pan.moved;
    pan = null;
    viewport.classList.remove("is-panning");
    if (viewport.hasPointerCapture?.(pointerId)) viewport.releasePointerCapture(pointerId);
    anchor = currentAnchor();
    syncAdjustControls();
    window.setTimeout(() => { suppressPanClick = false; }, 0);
  }

  function stepZoom(direction, point) {
    if (paper.hidden) return;
    const current = currentMultiplier();
    const next = direction > 0
      ? steps.find((step) => step > current + .001)
      : [...steps].reverse().find((step) => step < current - .001);
    if (next !== undefined) setZoom(next, point);
  }

  function setMobileView(view) {
    workspace.dataset.mobileView = view;
    document.querySelectorAll("[data-view]").forEach((button) => {
      button.setAttribute("aria-pressed", String(button.dataset.view === view));
    });
  }

  function syncPanels() {
    const isFullscreen = Boolean(fullscreenSnapshot);
    const isFocus = document.body.classList.contains("reader-focus");
    let returnFocus = null;
    for (const [name, panel, button] of [["library", library, libraryButton], ["evidence", evidence, evidenceButton]]) {
      const collapsed = workspace.classList.contains(`${name}-collapsed`);
      const visible = !collapsed && !isFocus && !isFullscreen;
      if (!visible && panel?.contains(document.activeElement)) returnFocus = button;
      if (panel) { panel.inert = !visible; panel.setAttribute("aria-hidden", String(!visible)); }
      if (button) {
        button.setAttribute("aria-expanded", String(visible));
        button.setAttribute("aria-pressed", String(visible));
        button.disabled = isFullscreen || isFocus;
        button.title = isFocus || isFullscreen ? "退出专注或全屏阅读后展开侧栏" : `${visible ? "收起" : "展开"}${name === "library" ? "文献库" : "证据检索"}`;
      }
    }
    if (focusButton) {
      focusButton.disabled = isFullscreen;
      focusButton.setAttribute("aria-pressed", String(isFocus));
      focusButton.setAttribute("aria-label", isFocus ? "退出专注阅读" : "专注阅读");
      label(focusButton, isFocus ? "退出专注" : "专注阅读");
    }
    if (fullscreenButton) {
      fullscreenButton.disabled = nativePending;
      fullscreenButton.setAttribute("aria-pressed", String(isFullscreen));
      label(fullscreenButton, isFullscreen ? "退出全屏" : "全屏阅读");
      fullscreenButton.title = isFullscreen ? "退出全屏阅读（Esc）" : "全屏阅读（Esc 退出）";
    }
    if (info && infoButton) {
      const open = !info.classList.contains("is-collapsed");
      info.inert = !open;
      info.setAttribute("aria-hidden", String(!open));
      infoButton.setAttribute("aria-expanded", String(open));
      infoButton.setAttribute("aria-label", `${open ? "收起" : "展开"}文献信息`);
      infoButton.title = `${open ? "收起" : "展开"}文献信息`;
      label(infoButton, open ? "收起信息" : "文献信息");
    }
    if (returnFocus && !isFullscreen && !isFocus && !leavingReader) returnFocus.focus({ preventScroll: true });
  }

  function togglePanel(name) {
    if (fullscreenSnapshot || document.body.classList.contains("reader-focus")) return;
    holdLayoutAnchor(currentAnchor());
    if (compact.matches) {
      const open = workspace.classList.contains(`${name}-collapsed`);
      workspace.classList.toggle(`${name}-collapsed`, !open);
      workspace.classList.add(`${name === "library" ? "evidence" : "library"}-collapsed`);
      setMobileView(open ? name : "reader");
    } else {
      workspace.classList.toggle(`${name}-collapsed`);
      desktopPanels[name] = workspace.classList.contains(`${name}-collapsed`);
    }
    syncPanels();
    scheduleReflow();
    const panel = name === "library" ? library : evidence;
    if (compact.matches && panel && !panel.inert) {
      const trigger = name === "library" ? libraryButton : evidenceButton;
      function focusOpenedDrawer() {
        if (!compact.matches || workspace.hidden || leavingReader || panel.inert
          || workspace.classList.contains(`${name}-collapsed`) || fullscreenSnapshot || focusSnapshot) return;
        if (panel.contains(document.activeElement) || document.activeElement !== trigger) return;
        panel.getBoundingClientRect();
        const closeButton = byId(name === "library" ? "closeLibrary" : "closeEvidence");
        if (!closeButton) return;
        // Wait for the opening visibility/transform transition; focusing while
        // the drawer is still hidden is silently ignored by the browser.
        if (getComputedStyle(closeButton).visibility !== "visible" || panel.getAnimations?.().some((animation) => animation.playState === "running" || animation.pending)) {
          window.requestAnimationFrame(focusOpenedDrawer);
          return;
        }
        closeButton.focus({ preventScroll: true });
      }
      window.requestAnimationFrame(focusOpenedDrawer);
    }
  }

  function closeDrawers() {
    if (!compact.matches) return false;
    const open = ["library", "evidence"].find((name) => !workspace.classList.contains(`${name}-collapsed`));
    if (!open) return false;
    togglePanel(open);
    (open === "library" ? libraryButton : evidenceButton)?.focus({ preventScroll: true });
    return true;
  }

  function containTab(event) {
    if (event.altKey || event.ctrlKey || event.metaKey || workspace.hidden) return;
    let scope = fullscreenSnapshot || focusSnapshot ? reader : null;
    if (!scope && compact.matches) {
      if (!workspace.classList.contains("library-collapsed")) scope = library;
      else if (!workspace.classList.contains("evidence-collapsed")) scope = evidence;
    }
    if (!scope) return;
    const controls = [...scope.querySelectorAll('button:not(:disabled),a[href],input:not(:disabled):not([type="hidden"]),select:not(:disabled),textarea:not(:disabled),[tabindex]:not([tabindex="-1"])')]
      .filter((node) => !node.closest('[hidden],[inert]') && node.getClientRects().length && getComputedStyle(node).visibility !== "hidden" && node.tabIndex >= 0);
    if (!controls.length) return;
    const first = controls[0], last = controls.at(-1);
    const active = document.activeElement;
    if (!controls.includes(active) || (event.shiftKey ? active === first : active === last)) {
      event.preventDefault();
      (event.shiftKey ? last : first).focus({ preventScroll: true });
    }
  }

  function snapshot() {
    return {
      library: workspace.classList.contains("library-collapsed"),
      evidence: workspace.classList.contains("evidence-collapsed"),
      view: workspace.dataset.mobileView,
      zoom: zoomSelect.value,
      position: horizontalPosition,
      anchor: currentAnchor(),
      focus: document.body.classList.contains("reader-focus"),
      active: document.activeElement,
      compact: compact.matches,
    };
  }

  function restore(saved) {
    if (!saved) return;
    horizontalPosition = saved.position ?? .5;
    holdLayoutAnchor(saved.anchor?.source === image.getAttribute("src") ? saved.anchor : currentAnchor());
    workspace.classList.toggle("library-collapsed", saved.library);
    workspace.classList.toggle("evidence-collapsed", saved.evidence);
    if (compact.matches !== saved.compact) {
      workspace.classList.toggle("library-collapsed", compact.matches || desktopPanels.library);
      workspace.classList.toggle("evidence-collapsed", compact.matches || desktopPanels.evidence);
    }
    document.body.classList.toggle("reader-focus", saved.focus);
    setMobileView(saved.view || "reader");
    selectZoom(saved.zoom);
    syncPanels();
    reflow(anchor);
    scheduleReflow();
    if (!leavingReader && saved.active?.isConnected) saved.active.focus({ preventScroll: true });
  }

  function enterReadingLayout() {
    workspace.classList.add("library-collapsed", "evidence-collapsed");
    setMobileView("reader");
    syncPanels();
    scheduleReflow();
  }

  function toggleFocus() {
    if (fullscreenSnapshot) return;
    setAdjustOpen(false);
    if (focusSnapshot) {
      const saved = focusSnapshot;
      focusSnapshot = null;
      restore(saved);
    } else {
      focusSnapshot = snapshot();
      holdLayoutAnchor(focusSnapshot.anchor);
      document.body.classList.add("reader-focus");
      enterReadingLayout();
    }
  }

  function finishFullscreen() {
    if (!fullscreenSnapshot) return;
    const saved = fullscreenSnapshot;
    fullscreenSnapshot = null;
    nativePending = false;
    nativeEntered = false;
    reader.classList.remove("reader-fullscreen");
    restore(saved);
  }

  async function exitFullscreen() {
    if (document.fullscreenElement === reader && document.exitFullscreen) {
      try { await document.exitFullscreen(); } catch { /* The visible exit control also works in the fallback view. */ }
    }
    if (document.fullscreenElement !== reader) finishFullscreen();
  }

  async function toggleFullscreen() {
    if (nativePending) return;
    setAdjustOpen(false);
    if (fullscreenSnapshot) { await exitFullscreen(); return; }
    fullscreenSnapshot = snapshot();
    holdLayoutAnchor(fullscreenSnapshot.anchor);
    reader.classList.add("reader-fullscreen");
    enterReadingLayout();
    if (reader.requestFullscreen && document.fullscreenEnabled) {
      nativePending = true;
      syncPanels();
      try { await reader.requestFullscreen(); }
      catch { /* Embedded browsers may disallow native fullscreen; the fixed reading view remains usable. */ }
      finally { nativePending = false; syncPanels(); scheduleReflow(); }
    }
    fullscreenButton?.focus({ preventScroll: true });
  }

  zoomSelect.addEventListener("change", () => setZoom(zoomSelect.value));
  zoomOut?.addEventListener("click", () => stepZoom(-1));
  zoomIn?.addEventListener("click", () => stepZoom(1));
  adjustButton?.addEventListener("click", () => setAdjustOpen(Boolean(adjustPopover?.hidden)));
  widthRange?.addEventListener("input", () => setZoom(Number(widthRange.value) / 100));
  positionRange?.addEventListener("input", () => setPosition(Number(positionRange.value) / 100));
  resetView?.addEventListener("click", () => {
    horizontalPosition = .5;
    setZoom(1);
    setPosition(.5);
  });
  document.addEventListener("pointerdown", (event) => {
    if (!adjustPopover?.hidden && !adjustPopover?.contains(event.target) && !adjustButton?.contains(event.target)) setAdjustOpen(false);
  });
  viewport.addEventListener("dragstart", (event) => { if (paper.contains(event.target)) event.preventDefault(); });
  viewport.addEventListener("pointerdown", (event) => {
    if (event.pointerType !== "mouse" || event.button !== 0 || paper.hidden || !image.getAttribute("src")) return;
    if (event.target.closest('button,a,input,select,textarea,[contenteditable="true"],[role="button"]')) return;
    const bounds = viewport.getBoundingClientRect();
    if (event.clientX >= bounds.left + viewport.clientLeft + viewport.clientWidth
      || event.clientY >= bounds.top + viewport.clientTop + viewport.clientHeight) return;
    event.preventDefault();
    layoutRevision += 1;
    anchorLocked = false;
    pan = {
      pointerId: event.pointerId, x: event.clientX, y: event.clientY,
      left: viewport.scrollLeft, top: viewport.scrollTop,
      position: horizontalPosition, space: horizontalSpace(), moved: false,
    };
    viewport.setPointerCapture(event.pointerId);
    viewport.classList.add("is-panning");
  });
  viewport.addEventListener("pointermove", (event) => {
    if (!pan || event.pointerId !== pan.pointerId) return;
    const dx = event.clientX - pan.x;
    const dy = event.clientY - pan.y;
    pan.moved ||= Math.abs(dx) + Math.abs(dy) > 3;
    if (pan.space > 1) {
      horizontalPosition = Math.max(0, Math.min(1, pan.position + dx / pan.space));
      alignPaper();
    } else viewport.scrollLeft = pan.left - dx;
    viewport.scrollTop = pan.top - dy;
    anchor = currentAnchor();
    syncAdjustControls();
  });
  viewport.addEventListener("pointerup", endPan);
  viewport.addEventListener("pointercancel", endPan);
  viewport.addEventListener("lostpointercapture", endPan);
  window.addEventListener("blur", endPan);
  viewport.addEventListener("click", (event) => {
    if (!suppressPanClick) return;
    event.preventDefault();
    event.stopImmediatePropagation();
  }, true);
  viewport.addEventListener("wheel", (event) => {
    if (!event.ctrlKey || event.altKey || event.metaKey || paper.hidden || !event.deltaY) return;
    event.preventDefault();
    stepZoom(event.deltaY < 0 ? 1 : -1, currentAnchor(event.clientX, event.clientY));
  }, { passive: false });
  viewport.addEventListener("scroll", () => {
    if (!applying && !anchorLocked) anchor = currentAnchor();
    syncAdjustControls();
  }, { passive: true });
  libraryButton?.addEventListener("click", () => togglePanel("library"));
  evidenceButton?.addEventListener("click", () => togglePanel("evidence"));
  byId("closeLibrary")?.addEventListener("click", () => togglePanel("library"));
  byId("closeEvidence")?.addEventListener("click", () => togglePanel("evidence"));
  byId("readerDrawerBackdrop")?.addEventListener("click", closeDrawers);
  focusButton?.addEventListener("click", toggleFocus);
  fullscreenButton?.addEventListener("click", toggleFullscreen);
  infoButton?.addEventListener("click", () => {
    holdLayoutAnchor(currentAnchor());
    info?.classList.toggle("is-collapsed");
    syncPanels();
    scheduleReflow();
  });
  document.addEventListener("fullscreenchange", () => {
    if (document.fullscreenElement === reader) { nativeEntered = true; scheduleReflow(); }
    else if (nativeEntered && fullscreenSnapshot) finishFullscreen();
  });
  document.addEventListener("keydown", (event) => {
    if (event.defaultPrevented || document.querySelector("dialog[open]")) return;
    if (event.key === "Tab") { containTab(event); return; }
    if (event.key !== "Escape") return;
    if (!adjustPopover?.hidden && adjustPopover) { event.preventDefault(); setAdjustOpen(false, true); }
    else if (pan) { event.preventDefault(); endPan(); }
    else if (fullscreenSnapshot) { event.preventDefault(); void exitFullscreen(); }
    else if (focusSnapshot) { event.preventDefault(); toggleFocus(); }
    else if (!leavingReader && closeDrawers()) event.preventDefault();
  });
  document.querySelectorAll("[data-view]").forEach((button) => button.addEventListener("click", () => {
    if (button.dataset.view === "reader" && compact.matches) workspace.classList.add("library-collapsed", "evidence-collapsed");
    else if (["library", "evidence"].includes(button.dataset.view)) {
      workspace.classList.remove(`${button.dataset.view}-collapsed`);
      if (compact.matches) workspace.classList.add(`${button.dataset.view === "library" ? "evidence" : "library"}-collapsed`);
    }
    syncPanels();
    scheduleReflow();
  }));
  window.addEventListener("research:reader-layout", () => {
    if (compact.matches && workspace.dataset.mobileView === "reader") workspace.classList.add("library-collapsed", "evidence-collapsed");
    syncPanels(); scheduleReflow();
  });
  window.addEventListener("research:reader-resize-start", () => {
    endPan();
    layoutRevision += 1;
    anchor = currentAnchor();
    anchorLocked = true;
  });
  window.addEventListener("research:reader-resize-end", () => holdLayoutAnchor(anchor));
  window.addEventListener("research:module-change", (event) => {
    leavingReader = event.detail?.module !== "reader";
    if (leavingReader) {
      endPan();
      setAdjustOpen(false);
      if (fullscreenSnapshot) void exitFullscreen().finally(() => { if (focusSnapshot) toggleFocus(); });
      else if (focusSnapshot) toggleFocus();
    } else scheduleReflow();
  });
  window.addEventListener("research:page-ready", () => {
    image = byId("pageImage");
    endPan();
    layoutRevision += 1;
    anchorLocked = false;
    anchor = null;
    reflow(null);
  });
  new MutationObserver(() => { syncPanels(); scheduleReflow(); }).observe(workspace, { attributes: true, attributeFilter: ["hidden"] });
  new MutationObserver(syncZoomButtons).observe(paper, { attributes: true, attributeFilter: ["hidden"] });
  if (window.ResizeObserver) new ResizeObserver(scheduleReflow).observe(viewport);
  else window.addEventListener("resize", scheduleReflow);
  compact.addEventListener("change", () => {
    if (compact.matches) {
      if (!focusSnapshot && !fullscreenSnapshot) desktopPanels = {
        library: workspace.classList.contains("library-collapsed"),
        evidence: workspace.classList.contains("evidence-collapsed"),
      };
      workspace.classList.add("library-collapsed", "evidence-collapsed");
    } else if (!focusSnapshot && !fullscreenSnapshot) {
      workspace.classList.toggle("library-collapsed", desktopPanels.library);
      workspace.classList.toggle("evidence-collapsed", desktopPanels.evidence);
    }
    syncPanels(); scheduleReflow();
  });
  syncPanels();
  syncZoomButtons();
  setAdjustOpen(false);
})();
