"use strict";

/* Shared transient feedback. Owners keep notifications scoped to their view. */
(() => {
  const active = new Set(), latest = new WeakMap();
  const available = owner => !owner || (owner.isConnected && !owner.parentElement?.closest('[hidden],dialog:not([open])'));
  function hostFor(owner) {
    // A modal dialog is in the browser top layer. Its feedback must live there
    // too, otherwise it is obscured and cannot receive hover or keyboard focus.
    const parent = owner?.closest('dialog[open]') || document.body;
    let host = parent.querySelector(':scope > .notice-stack');
    if (!host) {
      host = document.createElement('div'); host.className = 'notice-stack';
      host.setAttribute('aria-label', '操作提示'); parent.append(host);
    }
    return host;
  }
  function show(message, error = false, owner = null) {
    if (owner) latest.get(owner)?.();
    if (!message || !available(owner)) return () => {};
    const host = hostFor(owner);
    const base = document.getElementById('appNotice');
    const reusable = !owner && base?.hidden && base.parentElement === host;
    const card = reusable ? base : document.createElement('div');
    if (!reusable) {
      card.className = 'app-notice';
      const text = document.createElement('span'); text.className = 'notice-text';
      const close = document.createElement('button'); close.className = 'icon-button';
      close.type = 'button'; close.setAttribute('aria-label', '关闭提示'); close.textContent = '×';
      card.append(text, close); host.append(card);
    }
    card.setAttribute('role', 'status'); card.setAttribute('aria-atomic', 'true');
    card.querySelector('.notice-text').textContent = String(message);
    card.classList.toggle('is-error', error); card.hidden = false;
    if (owner) card.dataset.noticeSource = owner.id || owner.className;
    let timer, closed = false;
    const events = new AbortController();
    const pause = () => { clearTimeout(timer); timer = null; };
    const entry = {owner, host, dismiss: null};
    const dismiss = () => {
      if (closed) return;
      closed = true; pause(); events.abort(); active.delete(entry);
      if (owner && latest.get(owner) === dismiss) latest.delete(owner);
      if (reusable) card.hidden = true; else card.remove();
      if (host.parentElement !== document.body && !host.children.length) host.remove();
    };
    entry.dismiss = dismiss;
    const resume = () => {
      pause();
      if (!closed && !card.matches(':hover') && !card.contains(document.activeElement)) timer = setTimeout(dismiss, 2000);
    };
    const on = (name, action) => card.addEventListener(name, action, {signal:events.signal});
    on('mouseenter', pause); on('mouseleave', resume); on('focusin', pause);
    on('focusout', event => { if (!card.contains(event.relatedTarget)) resume(); });
    card.querySelector('.icon-button').addEventListener('click', dismiss, {signal:events.signal});
    active.add(entry); if (owner) latest.set(owner, dismiss); resume();
    return dismiss;
  }
  new MutationObserver(() => {
    for (const entry of active) if (!available(entry.owner) || !entry.host.isConnected) entry.dismiss();
  }).observe(document.body, {subtree:true, childList:true, attributes:true, attributeFilter:['hidden','open']});
  window.ResearchNotices = Object.freeze({show});
})();
