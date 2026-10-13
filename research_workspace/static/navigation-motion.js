/* Shared selection background. Navigation and focus remain owned by each view. */
"use strict";
(() => {
  const selector = '.module-navigation,.mobile-navigation,.rm-tabs,.ex-tabs,.rm-text-outline nav';
  const activeSelector = 'button[aria-selected="true"],button[aria-pressed="true"],button[aria-current="location"]';
  const groups = new Map();
  const dirty = new Set();
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
  let frame = 0, rescan = true;

  function schedule(scan = false) {
    rescan ||= scan;
    if (!frame) frame = requestAnimationFrame(flush);
  }
  function mount(group) {
    const indicator = document.createElement('span');
    indicator.className = 'selection-indicator';
    indicator.setAttribute('aria-hidden', 'true');
    indicator.hidden = true;
    group.append(indicator);
    group.classList.add('motion-group');
    const state = {indicator, active: null, rect: null, observer: null};
    state.observer = new ResizeObserver(() => { dirty.add(group); schedule(); });
    state.observer.observe(group);
    for (const button of group.querySelectorAll(':scope > button')) state.observer.observe(button);
    groups.set(group, state);
    dirty.add(group);
  }
  function update(group, state) {
    const active = group.querySelector(activeSelector);
    const box = active?.getBoundingClientRect();
    if (!box?.width || !box.height || !group.getClientRects().length) {
      state.indicator.hidden = true;
      group.removeAttribute('data-motion-ready');
      state.active = null; state.rect = null;
      return;
    }
    const parent = group.getBoundingClientRect();
    const rect = {
      x: box.left - parent.left + group.scrollLeft - group.clientLeft,
      y: box.top - parent.top + group.scrollTop - group.clientTop,
      width: box.width, height: box.height,
    };
    if (state.active === active && state.rect && Object.keys(rect).every(key => Math.abs(rect[key] - state.rect[key]) < .5)) return;
    const moving = state.active && state.active !== active && !reduced.matches;
    const indicator = state.indicator;
    // Initial mount, reveal and resize snap to the correct geometry. Only an
    // actual change of selection animates, so a scroll cannot restart movement.
    if (!moving) indicator.style.transition = 'none';
    indicator.hidden = false;
    indicator.style.transform = `translate(${rect.x}px,${rect.y}px)`;
    indicator.style.width = `${rect.width}px`;
    indicator.style.height = `${rect.height}px`;
    group.setAttribute('data-motion-ready', 'true');
    if (!moving) { void indicator.offsetWidth; indicator.style.removeProperty('transition'); }
    state.active = active; state.rect = rect;
  }
  function flush() {
    frame = 0;
    if (rescan) {
      rescan = false;
      for (const [group, state] of groups) if (!group.isConnected) {
        state.observer.disconnect(); state.indicator.remove();
        group.classList.remove('motion-group'); group.removeAttribute('data-motion-ready');
        groups.delete(group); dirty.delete(group);
      }
      document.querySelectorAll(selector).forEach(group => {
        if (!groups.has(group)) mount(group);
        dirty.add(group);
      });
    }
    for (const group of dirty) if (groups.has(group)) update(group, groups.get(group));
    dirty.clear();
  }
  const observer = new MutationObserver(records => {
    for (const record of records) {
      if (record.target.nodeType === 1 && record.target.classList.contains('selection-indicator')) continue;
      if (record.type === 'childList') {
        const nodes = [...record.addedNodes, ...record.removedNodes];
        if (nodes.length && nodes.every(node => node.nodeType === 1 && node.classList.contains('selection-indicator'))) continue;
        schedule(true);
      } else if (['hidden', 'open'].includes(record.attributeName)) schedule(true);
      else {
        const group = record.target.closest(selector);
        if (group) { dirty.add(group); schedule(); }
      }
    }
  });
  observer.observe(document.body, {subtree: true, childList: true, attributes: true, attributeFilter: ['aria-selected', 'aria-pressed', 'aria-current', 'hidden', 'open']});
  reduced.addEventListener('change', () => {
    for (const state of groups.values()) state.active = null;
    schedule(true);
  });
  window.addEventListener('resize', () => schedule(true));
  document.fonts?.ready.then(() => schedule(true));
  schedule(true);
})();
