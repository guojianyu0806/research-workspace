"use strict";

/* Local presentation preferences only; no document or account data is stored. */
(() => {
  const workspace = document.getElementById("workspace");
  if (!workspace) return;
  const compact = window.matchMedia("(max-width: 999px)");
  const names = ["library", "evidence"];
  const handles = Object.fromEntries(names.map((name) => [name, document.getElementById(`${name}ResizeHandle`)]));
  const defaults = { desktop: { library: 240, evidence: 300 }, compact: { library: 340, evidence: 340 } };
  const preferred = JSON.parse(JSON.stringify(defaults));
  const key = "research.reader.panelWidths.v1";
  const minimum = 240;
  const readerMinimum = 420;
  let sizes = { library: 240, evidence: 300 };
  let drag = null;
  let frame = 0;
  const clamp = (value, low, high) => Math.min(high, Math.max(low, value));
  const mode = () => compact.matches ? "compact" : "desktop";
  const isOpen = (name) => !workspace.classList.contains(`${name}-collapsed`);
  try {
    const stored = JSON.parse(localStorage.getItem(key));
    for (const layout of ["desktop", "compact"]) for (const name of names) {
      const value = stored?.[layout]?.[name];
      if (Number.isFinite(value)) preferred[layout][name] = clamp(value, minimum, 10000);
    }
  } catch { /* Storage may be unavailable; resizing still works for this session. */ }

  function persist() {
    try { localStorage.setItem(key, JSON.stringify(preferred)); } catch { /* Session-only fallback. */ }
  }

  function bounds(name) {
    const width = workspace.clientWidth;
    const other = name === "library" ? "evidence" : "library";
    const maximum = compact.matches ? Math.max(1, width - 36)
      : Math.max(minimum, width - readerMinimum - (isOpen(other) ? sizes[other] : 0));
    return { min: Math.min(minimum, maximum), max: maximum };
  }

  function render() {
    if (!workspace.clientWidth || workspace.hidden) return;
    const width = workspace.clientWidth;
    const active = mode();
    sizes = { ...preferred[active] };
    if (compact.matches) {
      const maximum = Math.max(1, width - 36);
      for (const name of names) sizes[name] = clamp(sizes[name], Math.min(minimum, maximum), maximum);
    } else {
      const budget = Math.max(minimum, width - readerMinimum);
      const open = names.filter(isOpen);
      for (const name of names) sizes[name] = clamp(sizes[name], minimum, budget);
      const total = open.reduce((sum, name) => sum + sizes[name], 0);
      if (total > budget) {
        const extras = total - open.length * minimum;
        const available = Math.max(0, budget - open.length * minimum);
        for (const name of open) sizes[name] = minimum + (sizes[name] - minimum) * available / extras;
      }
    }
    for (const name of names) {
      workspace.style.setProperty(`--${name}-${compact.matches ? "drawer-size" : "size"}`, `${sizes[name]}px`);
      const limits = bounds(name);
      handles[name]?.setAttribute("aria-valuemin", String(Math.round(limits.min)));
      handles[name]?.setAttribute("aria-valuemax", String(Math.round(limits.max)));
      handles[name]?.setAttribute("aria-valuenow", String(Math.round(sizes[name])));
      handles[name]?.setAttribute("aria-valuetext", `${Math.round(sizes[name])} 像素`);
    }
  }

  function schedule() {
    window.cancelAnimationFrame(frame);
    frame = window.requestAnimationFrame(render);
  }

  function setWidth(name, value) {
    const limits = bounds(name);
    // Preserve the current other column while resizing this divider. Preferred
    // widths that only exceed a smaller window are otherwise kept for return.
    if (!compact.matches) {
      const other = name === "library" ? "evidence" : "library";
      if (isOpen(other)) preferred.desktop[other] = sizes[other];
    }
    preferred[mode()][name] = clamp(value, limits.min, limits.max);
    render();
  }

  function finish(cancel = false) {
    if (!drag) return;
    const ended = drag;
    drag = null;
    if (cancel) preferred[ended.mode] = ended.before;
    if (ended.handle.hasPointerCapture(ended.id)) ended.handle.releasePointerCapture(ended.id);
    workspace.classList.remove("is-resizing");
    render();
    if (!cancel) persist();
    window.dispatchEvent(new CustomEvent("research:reader-resize-end"));
  }

  for (const name of names) {
    const handle = handles[name];
    if (!handle) continue;
    handle.addEventListener("pointerdown", (event) => {
      if (event.button !== 0 || !isOpen(name) || workspace.hidden) return;
      event.preventDefault();
      render();
      drag = { name, handle, id: event.pointerId, startX: event.clientX, width: sizes[name], mode: mode(), before: { ...preferred[mode()] } };
      handle.setPointerCapture(event.pointerId);
      handle.focus({ preventScroll: true });
      workspace.classList.add("is-resizing");
      window.dispatchEvent(new CustomEvent("research:reader-resize-start"));
    });
    handle.addEventListener("pointermove", (event) => {
      if (!drag || event.pointerId !== drag.id) return;
      const delta = (event.clientX - drag.startX) * (name === "library" ? 1 : -1);
      setWidth(name, drag.width + delta);
    });
    handle.addEventListener("pointerup", () => finish());
    handle.addEventListener("pointercancel", () => finish(true));
    handle.addEventListener("lostpointercapture", () => finish(true));
    handle.addEventListener("dblclick", (event) => {
      event.preventDefault();
      window.dispatchEvent(new CustomEvent("research:reader-resize-start"));
      setWidth(name, defaults[mode()][name]);
      window.dispatchEvent(new CustomEvent("research:reader-resize-end"));
      persist();
    });
    handle.addEventListener("keydown", (event) => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      render();
      const limits = bounds(name);
      const delta = (event.shiftKey ? 40 : 10) * (event.key === "ArrowRight" ? 1 : -1) * (name === "library" ? 1 : -1);
      const value = event.key === "Home" ? limits.min : event.key === "End" ? limits.max : sizes[name] + delta;
      window.dispatchEvent(new CustomEvent("research:reader-resize-start"));
      setWidth(name, value);
      window.dispatchEvent(new CustomEvent("research:reader-resize-end"));
      persist();
    });
  }
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && drag) { event.preventDefault(); event.stopPropagation(); finish(true); }
  }, true);
  window.addEventListener("blur", () => finish());
  compact.addEventListener("change", () => { finish(); render(); });
  new MutationObserver(() => {
    if (drag && (workspace.hidden || !isOpen(drag.name))) finish(true);
    schedule();
  }).observe(workspace, { attributes: true, attributeFilter: ["class", "hidden"] });
  if (window.ResizeObserver) new ResizeObserver(schedule).observe(workspace);
  else window.addEventListener("resize", schedule);
  render();
})();
