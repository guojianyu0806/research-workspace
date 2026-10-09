"use strict";

/* Reader-only presentation controls. No document, account or model data is written. */
(() => {
  const byId = (id) => document.getElementById(id);
  const workspace = byId("workspace");
  const reader = byId("readerPanel");
  const viewport = byId("readerViewport");
  const paper = byId("paperPage");
  const image = byId("pageImage");
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
  let lastImage = "";
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
    // app.js updates these attributes before the displayed image finishes decoding.
    return (Number(image.getAttribute("width")) || image.naturalWidth || 1)
      / (Number(image.getAttribute("height")) || image.naturalHeight || 1);
  }

  function currentMultiplier() {
    if (zoomSelect.value !== "page") return Number(zoomSelect.value) || 1;
    const size = availableSize();
    const ratio = pageRatio();
    return Math.min(1, size.height * ratio / size.width);
  }

  function syncZoomButtons() {
    const ready = !paper.hidden && Boolean(image.getAttribute("src"));
    const multiplier = currentMultiplier();
    if (zoomOut) zoomOut.disabled = !ready || multiplier <= steps[0];
    if (zoomIn) zoomIn.disabled = !ready || multiplier >= steps.at(-1);
    zoomSelect.disabled = !ready;
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
    restoreAnchor(saved);
    syncZoomButtons();
    window.requestAnimationFrame(() => { applying = false; });
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
    zoomSelect.value = String(value);
    reflow(anchor);
    anchor = currentAnchor();
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
      anchor: currentAnchor(),
      focus: document.body.classList.contains("reader-focus"),
      active: document.activeElement,
      compact: compact.matches,
    };
  }

  function restore(saved) {
    if (!saved) return;
    holdLayoutAnchor(saved.anchor?.source === image.getAttribute("src") ? saved.anchor : currentAnchor());
    workspace.classList.toggle("library-collapsed", saved.library);
    workspace.classList.toggle("evidence-collapsed", saved.evidence);
    if (compact.matches !== saved.compact) {
      workspace.classList.toggle("library-collapsed", compact.matches || desktopPanels.library);
      workspace.classList.toggle("evidence-collapsed", compact.matches || desktopPanels.evidence);
    }
    document.body.classList.toggle("reader-focus", saved.focus);
    setMobileView(saved.view || "reader");
    zoomSelect.value = saved.zoom;
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
  viewport.addEventListener("wheel", (event) => {
    if (!event.ctrlKey || event.altKey || event.metaKey || paper.hidden || !event.deltaY) return;
    event.preventDefault();
    stepZoom(event.deltaY < 0 ? 1 : -1, currentAnchor(event.clientX, event.clientY));
  }, { passive: false });
  viewport.addEventListener("scroll", () => { if (!applying && !anchorLocked) anchor = currentAnchor(); }, { passive: true });
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
    if (fullscreenSnapshot) { event.preventDefault(); void exitFullscreen(); }
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
  window.addEventListener("research:module-change", (event) => {
    leavingReader = event.detail?.module !== "reader";
    if (leavingReader) {
      if (fullscreenSnapshot) void exitFullscreen().finally(() => { if (focusSnapshot) toggleFocus(); });
      else if (focusSnapshot) toggleFocus();
    } else scheduleReflow();
  });
  window.addEventListener("research:page-ready", () => {
    layoutRevision += 1;
    anchorLocked = false;
    anchor = null;
    lastImage = image.getAttribute("src");
    reflow(null);
  });
  image.addEventListener("load", () => {
    const changed = lastImage !== image.getAttribute("src");
    lastImage = image.getAttribute("src");
    if (changed) anchor = null;
    reflow(changed ? null : anchor);
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
})();
