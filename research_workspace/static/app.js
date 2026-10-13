"use strict";

(() => {
  const byId = (id) => document.getElementById(id);
  const ui = Object.fromEntries([
    "workspace", "libraryPanel", "documentList", "documentCount", "documentFilter",
    "uploadButton", "emptyUploadButton", "fileInput", "dropZone", "uploadStatus", "uploadLimit",
    "connectionState", "connectionText", "libraryFooter", "appNotice", "noticeText", "dismissNotice",
    "readerTitle", "readerSubtitle", "reparseButton", "readerToolbar", "readerMessage", "readerViewport",
    "readerEmpty", "pageLoading", "pageError", "pageErrorText", "retryPageButton", "paperPage", "pageImage",
    "evidenceHighlight", "readerFooterText", "pageForm", "pageNumber", "pageTotal", "firstPage", "previousPage", "nextPage", "lastPage",
    "searchForm", "searchQuery", "searchScope", "currentScopeOption", "searchButton", "searchExplanation",
    "resultsHeading", "resultsTitle", "clearSearchButton", "searchResults",
  ].map((id) => [id, byId(id)]));

  const state = {
    documents: [], capabilities: null, selectedId: null, selectedRunId: null, pinnedRun: false,
    page: 1, highlight: null, selectedHit: null, query: "", hits: [], uploading: false,
    searching: false, reparsing: false, connected: false, pageHasText: null,
  };
  let listRequest = 0;
  let pageRequest = 0;
  let searchRequest = 0;
  let navigationRequest = 0;
  let pollTimer = null;
  let dragDepth = 0;
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  const isPending = (run) => run && ["queued", "running"].includes(run.status);
  const currentDocument = () => state.documents.find((doc) => doc.id === state.selectedId);
  const readableRun = (doc) => doc?.available_run || doc?.current_run;
  const readingPages = new Map();
  const readingPageKey = (id) => `research-reader-page:v1:${id}`;
  function rememberedPage(id) {
    if (readingPages.has(id)) return readingPages.get(id);
    let page = 1;
    try {
      const stored = Number(localStorage.getItem(readingPageKey(id)));
      if (Number.isSafeInteger(stored) && stored > 0) page = stored;
    } catch { /* Reading still works when browser storage is unavailable. */ }
    readingPages.set(id, page);
    return page;
  }
  function clampReadingPage(value, doc) {
    const page = Number(value);
    return Math.min(Number.isSafeInteger(page) && page > 0 ? page : 1, Math.max(1, Number(doc.page_count) || 1));
  }
  function rememberReadingPage(doc) {
    if (!doc || !(Number(doc.page_count) > 0)) return;
    readingPages.set(doc.id, state.page);
    try { localStorage.setItem(readingPageKey(doc.id), String(state.page)); }
    catch { /* Keep per-document progress in memory for this session. */ }
  }
  const statusLabels = {
    queued: "等待处理", running: "正在提取文字", completed: "文本已提取",
    partial: "部分页面待识别", needs_ocr: "需要文字识别", failed: "处理失败", cancelled: "已取消",
  };

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = String(text);
    return node;
  }

  function icon(name) {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", "icon");
    svg.setAttribute("aria-hidden", "true");
    const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
    use.setAttribute("href", `#i-${name}`);
    svg.append(use);
    return svg;
  }

  async function api(path, options = {}) {
    let response;
    try {
      response = await fetch(path, { cache: "no-store", ...options });
    } catch {
      throw new Error("无法连接工作区，请检查服务是否正在运行。");
    }
    let result;
    try { result = await response.json(); } catch { result = {}; }
    if (!response.ok) {
      throw new Error(typeof result.detail === "string" ? result.detail : `请求未完成（${response.status}），请稍后重试。`);
    }
    return result;
  }

  const notices = new Map();
  function notice(message, error = false) {
    // Reuse the first card; concurrent messages have independent lifetimes.
    const card = ui.appNotice.hidden ? ui.appNotice : ui.appNotice.cloneNode(true);
    if (card !== ui.appNotice) {
      card.removeAttribute("id");
      card.querySelectorAll("[id]").forEach(node => node.removeAttribute("id"));
      ui.appNotice.parentElement.append(card);
    }
    let controls = notices.get(card);
    if (!controls) {
      let timer;
      const pause = () => { clearTimeout(timer); timer = null; };
      const dismiss = () => {
        pause();
        if (card === ui.appNotice) card.hidden = true;
        else { card.remove(); notices.delete(card); }
      };
      const resume = () => {
        pause();
        if (!card.hidden && !card.matches(":hover") && !card.contains(document.activeElement)) {
          timer = setTimeout(dismiss, 2000);
        }
      };
      card.addEventListener("mouseenter", pause);
      card.addEventListener("mouseleave", resume);
      card.addEventListener("focusin", pause);
      card.addEventListener("focusout", event => { if (!card.contains(event.relatedTarget)) resume(); });
      card.querySelector(".icon-button").addEventListener("click", dismiss);
      controls = {resume}; notices.set(card, controls);
    }
    card.querySelector(".notice-text").textContent = message;
    card.classList.toggle("is-error", error);
    card.hidden = false;
    controls.resume();
  }

  function setConnection(connected) {
    state.connected = connected;
    ui.connectionState.classList.toggle("is-connected", connected);
    ui.connectionState.classList.toggle("is-offline", !connected);
    ui.connectionText.textContent = connected ? (state.capabilities?.mode === "local" ? "本地工作区" : "已连接") : "连接中断";
    ui.libraryFooter.textContent = connected ? "原文保留 · 处理结果可追溯" : "连接中断，可重新加载文献";
  }

  function setView(view) {
    ui.workspace.dataset.mobileView = view;
    document.querySelectorAll("[data-view]").forEach((button) => {
      button.setAttribute("aria-pressed", String(button.dataset.view === view));
    });
    window.dispatchEvent(new CustomEvent("research:reader-layout"));
  }

  function documentName(doc) { return doc.title || doc.filename || "未命名文献"; }

  function runSummary(run) {
    if (!run) return "尚未处理";
    if (isPending(run) && Number(run.total_pages) > 0) {
      return `${statusLabels[run.status]} · ${Number(run.pages_done) || 0} / ${run.total_pages} 页`;
    }
    if (run.status === "partial") return `${run.text_pages || 0} 页已提取，${run.no_text_pages || 0} 页未检测到文字`;
    return statusLabels[run.status] || "状态待确认";
  }

  let libraryStamp = "";
  function renderLibrary() {
    ui.documentCount.textContent = String(state.documents.length);
    ui.documentList.setAttribute("aria-busy", "false");
    const filter = ui.documentFilter.value.trim().toLocaleLowerCase();
    const documents = state.documents.filter((doc) => `${doc.title || ""} ${doc.filename || ""}`.toLocaleLowerCase().includes(filter));
    const stamp = JSON.stringify([filter, documents]);
    if (documents.length && stamp === libraryStamp && ui.documentList.querySelectorAll('.document-row').length === documents.length) {
      for (const button of ui.documentList.querySelectorAll('.document-row')) {
        const selected = button.dataset.documentId === state.selectedId;
        button.classList.toggle('is-selected', selected);
        button.setAttribute('aria-pressed', String(selected));
      }
      return;
    }
    libraryStamp = stamp;
    ui.documentList.replaceChildren();
    if (!documents.length) {
      const empty = element("div", "list-message");
      empty.append(element("strong", "", filter ? "没有匹配的文献" : "文献库还是空的"));
      empty.append(element("p", "", filter ? "换一个文件名关键词试试。" : "导入 PDF 后，文献会显示在这里。"));
      ui.documentList.append(empty);
      return;
    }
    documents.forEach((doc) => {
      const button = element("button", `document-row${doc.id === state.selectedId ? " is-selected" : ""}`);
      button.type = "button";
      button.dataset.documentId = doc.id;
      button.title = doc.filename || documentName(doc);
      button.setAttribute("aria-pressed", String(doc.id === state.selectedId));
      const fileIcon = element("span", "document-file-icon");
      fileIcon.append(icon("file"));
      const main = element("span", "document-main");
      main.append(element("span", "document-name", documentName(doc)));
      const meta = element("span", "document-meta");
      const run = doc.current_run;
      meta.append(element("span", `document-state is-${run?.status || "unknown"}`, statusLabels[run?.status] || "尚未处理"));
      if (Number(doc.page_count) > 0) meta.append(element("span", "", `${doc.page_count} 页`));
      main.append(meta);
      if (isPending(run) && Number(run.total_pages) > 0) {
        const progress = element("span", "document-progress");
        const bar = element("span");
        bar.style.width = `${Math.min(100, Math.max(0, Number(run.pages_done) / Number(run.total_pages) * 100))}%`;
        progress.append(bar);
        progress.setAttribute("aria-label", `已处理 ${run.pages_done || 0} / ${run.total_pages} 页`);
        main.append(progress);
      }
      button.append(fileIcon, main);
      button.addEventListener("click", () => selectDocument(doc.id));
      ui.documentList.append(button);
    });
  }

  function renderReaderHeading() {
    const doc = currentDocument();
    ui.currentScopeOption.disabled = !doc;
    ui.readerToolbar.hidden = !doc;
    ui.reparseButton.disabled = !doc || isPending(doc.current_run) || state.reparsing;
    if (!doc) return;
    ui.readerTitle.textContent = documentName(doc);
    ui.readerTitle.title = doc.filename || documentName(doc);
    const historical = state.pinnedRun && state.selectedRunId !== readableRun(doc)?.id;
    ui.readerSubtitle.textContent = historical ? "正在查看这条证据对应的历史处理结果" : runSummary(doc.current_run);
    const total = Number(doc.page_count) || 0;
    ui.pageNumber.value = String(state.page);
    ui.pageNumber.max = String(Math.max(1, total));
    ui.pageNumber.disabled = total < 1;
    ui.pageTotal.textContent = `/ ${total || "—"}`;
    ui.previousPage.disabled = total < 1 || state.page <= 1;
    ui.nextPage.disabled = state.page >= total;
    ui.firstPage.disabled = ui.previousPage.disabled;
    ui.lastPage.disabled = ui.nextPage.disabled;
    const run = doc.current_run;
    let message = "";
    if (historical) message = "这条证据来自之前的处理结果，原文位置仍与该结果对应。";
    else if (run?.status === "failed") message = doc.available_run && doc.available_run.id !== run.id
      ? `本次提取未完成，已有结果仍可阅读和检索。${run.error || "可点击右上角重试。"}`
      : run.error || "文字提取未完成，可点击右上角重新提取。";
    else if (run?.status === "needs_ocr") message = state.capabilities?.ocr === false ? "未检测到可提取文本。当前未启用扫描件识别，仍可阅读原文。" : "这份文献需要文字识别，当前可先阅读原文。";
    else if (run?.status === "partial") message = state.capabilities?.ocr === false ? "部分页面未检测到文字。当前未启用扫描件识别，检索仅覆盖已提取内容。" : "部分页面未检测到文字，检索仅覆盖已提取内容。";
    else if (isPending(run)) message = doc.available_run
      ? "新一轮文字提取正在进行，已有结果仍可阅读和检索。"
      : "正在提取文献文字，可先阅读原文。处理完成后可检索内容。";
    else if (state.pageHasText === false) message = "这一页没有可检索文字，仍可查看原文页面。";
    ui.readerMessage.textContent = message;
    ui.readerMessage.hidden = !message;
    ui.readerFooterText.textContent = state.highlight
      ? `已定位第 ${state.page} 页的检索证据，请结合上下文核对。`
      : `第 ${state.page} / ${total || "—"} 页 · 选择证据片段可定位原文`;
  }

  async function refreshDocuments({ quiet = false } = {}) {
    const request = ++listRequest;
    const previous = currentDocument();
    const previousReadableId = readableRun(previous)?.id;
    const previousCurrentRun = previous?.current_run;
    try {
      const result = await api("/api/documents");
      if (request !== listRequest) return;
      state.documents = Array.isArray(result.documents) ? result.documents : [];
      if (state.selectedId && !currentDocument()) {
        state.selectedId = null;
        state.pinnedRun = false;
        state.selectedRunId = null;
      }
      setConnection(true);
      renderLibrary();
      if (!state.selectedId && state.documents.length) {
        selectDocument(state.documents[0].id, { keepView: true });
      } else if (state.selectedId) {
        const selected = currentDocument();
        const clampedPage = clampReadingPage(state.page, selected);
        const pageChanged = clampedPage !== state.page;
        if (pageChanged) {
          state.page = clampedPage;
          state.highlight = null;
          state.selectedHit = null;
          state.pageHasText = null;
          rememberReadingPage(selected);
          renderHitSelection();
        }
        if (selected && !state.pinnedRun) {
          state.selectedRunId = readableRun(selected)?.id || null;
          const firstRunFinished = isPending(previousCurrentRun) && !isPending(selected.current_run)
            && state.selectedRunId === selected.current_run?.id;
          if (pageChanged || readableRun(selected)?.id !== previousReadableId || firstRunFinished) loadPage();
        } else if (pageChanged) loadPage();
        renderReaderHeading();
      } else {
        ui.readerTitle.textContent = "原文阅读";
        ui.readerSubtitle.textContent = "导入论文后阅读原文与证据。";
        ui.readerEmpty.hidden = false;
        ui.paperPage.hidden = true;
        ui.readerToolbar.hidden = true;
        ui.reparseButton.disabled = true;
      }
      schedulePolling();
    } catch (error) {
      if (request !== listRequest) return;
      setConnection(false);
      if (!quiet || !state.documents.length) {
        ui.documentList.setAttribute("aria-busy", "false");
        if (!state.documents.length) {
          ui.documentCount.textContent = "—";
          const empty = element("div", "list-message");
          empty.append(element("strong", "", "暂时无法加载文献"), element("p", "", error.message));
          const retry = element("button", "button button-secondary", "重新连接");
          retry.type = "button";
          retry.addEventListener("click", initialize);
          empty.append(retry);
          ui.documentList.replaceChildren(empty);
        } else notice(error.message, true);
      }
      schedulePolling();
    }
  }

  function schedulePolling() {
    window.clearTimeout(pollTimer);
    if (!state.documents.some((doc) => isPending(doc.current_run))) return;
    pollTimer = window.setTimeout(() => refreshDocuments({ quiet: true }), 2000);
  }

  function selectDocument(id, options = {}) {
    navigationRequest += 1;
    const doc = state.documents.find((item) => item.id === id);
    if (!doc) return;
    const changed = state.selectedId !== id;
    state.selectedId = id;
    state.selectedRunId = options.runId || readableRun(doc)?.id || null;
    state.pinnedRun = Boolean(options.runId);
    state.page = clampReadingPage(options.page ?? rememberedPage(id), doc);
    rememberReadingPage(doc);
    state.highlight = normalizeBox(options.bbox);
    state.selectedHit = options.hitKey || null;
    state.pageHasText = null;
    if (changed && ui.searchScope.value === "current" && !options.fromSearch) clearSearch(false);
    renderLibrary();
    renderReaderHeading();
    renderHitSelection();
    loadPage();
    if (!options.keepView) setView("reader");
  }

  function normalizeBox(box) {
    if (!Array.isArray(box) || box.length !== 4 || !box.every((value) => Number.isFinite(Number(value)))) return null;
    const [x0, y0, x1, y1] = box.map((value) => Math.max(0, Math.min(1, Number(value))));
    return x1 > x0 && y1 > y0 ? [x0, y0, x1, y1] : null;
  }

  function renderHighlight() {
    const box = state.highlight;
    ui.evidenceHighlight.hidden = !box;
    if (!box) return;
    ui.evidenceHighlight.style.left = `${box[0] * 100}%`;
    ui.evidenceHighlight.style.top = `${box[1] * 100}%`;
    ui.evidenceHighlight.style.width = `${(box[2] - box[0]) * 100}%`;
    ui.evidenceHighlight.style.height = `${(box[3] - box[1]) * 100}%`;
  }

  async function loadPage() {
    const doc = currentDocument();
    if (!doc) return;
    const request = ++pageRequest;
    const id = doc.id;
    const page = state.page;
    const runId = state.selectedRunId;
    ui.readerEmpty.hidden = true;
    ui.pageLoading.hidden = false;
    ui.pageError.hidden = true;
    ui.paperPage.hidden = true;
    ui.evidenceHighlight.hidden = true;
    ui.readerViewport.scrollTop = 0;
    renderReaderHeading();

    const params = new URLSearchParams();
    if (runId) params.set("run_id", runId);
    const suffix = params.size ? `?${params}` : "";
    api(`/api/documents/${encodeURIComponent(id)}/pages/${page}${suffix}`).then((result) => {
      if (request !== pageRequest) return;
      state.pageHasText = Boolean(result.text?.trim() || result.blocks?.some((block) => block.text?.trim()));
      renderReaderHeading();
    }).catch(() => {
      // The original page can still be read while text extraction is pending.
      if (request === pageRequest) state.pageHasText = null;
    });

    const image = new Image();
    image.onload = () => {
      if (request !== pageRequest) return;
      ui.pageImage.src = image.src;
      ui.pageImage.alt = `${documentName(doc)}，第 ${page} 页原文`;
      ui.pageImage.width = image.naturalWidth;
      ui.pageImage.height = image.naturalHeight;
      ui.pageLoading.hidden = true;
      ui.paperPage.hidden = false;
      renderHighlight();
      window.dispatchEvent(new CustomEvent("research:page-ready"));
      if (state.highlight) {
        window.requestAnimationFrame(() => {
          if (request !== pageRequest || !state.highlight) return;
          const top = ui.paperPage.offsetTop + ui.paperPage.clientHeight * state.highlight[1] - ui.readerViewport.clientHeight * .28;
          const left = ui.paperPage.offsetLeft + ui.paperPage.clientWidth * (state.highlight[0] + state.highlight[2]) / 2 - ui.readerViewport.clientWidth / 2;
          ui.readerViewport.scrollTo({ top: Math.max(0, top), left: Math.max(0, left), behavior: reducedMotion.matches ? "auto" : "smooth" });
        });
      }
    };
    image.onerror = () => {
      if (request !== pageRequest) return;
      ui.pageLoading.hidden = true;
      ui.pageError.hidden = false;
      ui.pageErrorText.textContent = "这一页暂时无法打开。请确认服务可用后重试。";
    };
    image.src = `/api/documents/${encodeURIComponent(id)}/pages/${page}/image?scale=2`;
  }

  function goToPage(value) {
    const doc = currentDocument();
    if (!doc) return;
    const page = Number(value);
    if (!Number.isInteger(page) || page < 1 || page > Number(doc.page_count)) {
      ui.pageNumber.value = String(state.page);
      return;
    }
    if (page === state.page) return;
    navigationRequest += 1;
    state.page = page;
    rememberReadingPage(doc);
    state.highlight = null;
    state.selectedHit = null;
    state.pageHasText = null;
    renderHitSelection();
    loadPage();
  }

  function setUploadBusy(busy) {
    state.uploading = busy;
    [ui.uploadButton, ui.emptyUploadButton, ui.dropZone].forEach((button) => { button.disabled = busy; });
    ui.uploadButton.querySelector("span").textContent = busy ? "正在导入" : "导入 PDF";
    ui.uploadStatus.hidden = !busy;
  }

  async function uploadFiles(fileList) {
    if (state.uploading || !fileList.length) return;
    const files = Array.from(fileList);
    setUploadBusy(true);
    const errors = [];
    let imported = 0;
    let duplicates = 0;
    let firstId = null;
    for (let index = 0; index < files.length; index += 1) {
      const file = files[index];
      ui.uploadStatus.textContent = `正在导入 ${index + 1} / ${files.length}：${file.name}`;
      if (!file.name.toLowerCase().endsWith(".pdf")) {
        errors.push(`${file.name}：请选择 PDF 文件`);
        continue;
      }
      const limit = Number(state.capabilities?.max_upload_mb);
      if (limit > 0 && file.size > limit * 1024 * 1024) {
        errors.push(`${file.name}：超过 ${limit} MB 大小限制`);
        continue;
      }
      const form = new FormData();
      form.append("file", file);
      try {
        const result = await api("/api/documents", { method: "POST", body: form });
        const doc = result.document;
        if (!doc?.id) throw new Error("未收到文献信息，请刷新文献库后核对。");
        listRequest += 1;
        const existing = state.documents.findIndex((item) => item.id === doc.id);
        if (existing >= 0) state.documents[existing] = doc;
        else state.documents.unshift(doc);
        if (result.duplicate) duplicates += 1;
        else imported += 1;
        if (!firstId) {
          firstId = doc.id;
          ui.documentFilter.value = "";
          selectDocument(doc.id);
        }
        renderLibrary();
        schedulePolling();
      } catch (error) {
        errors.push(`${file.name}：${error.message}`);
      }
    }
    setUploadBusy(false);
    ui.fileInput.value = "";
    const summary = [];
    if (imported) summary.push(`已导入 ${imported} 篇文献`);
    if (duplicates) summary.push(`${duplicates} 个文件已在文献库中，保留已有文件`);
    if (errors.length) summary.push(errors.join("；"));
    if (summary.length) notice(summary.join("；"), Boolean(errors.length));
    await refreshDocuments({ quiet: true });
  }

  function searchEmpty(title, text, tip) {
    const empty = element("div", "search-empty");
    const image = element("div", "evidence-empty-icon");
    image.append(icon("search"), element("span"), element("span"));
    empty.append(image, element("h3", "", title), element("p", "", text));
    if (tip) empty.append(element("div", "search-tip", tip));
    ui.searchResults.replaceChildren(empty);
  }

  function clearSearch(clearInput = true) {
    searchRequest += 1;
    state.searching = false;
    state.query = "";
    state.hits = [];
    state.selectedHit = null;
    state.highlight = null;
    ui.searchButton.disabled = false;
    ui.searchResults.setAttribute("aria-busy", "false");
    ui.resultsHeading.hidden = true;
    if (clearInput) ui.searchQuery.value = "";
    renderHighlight();
    renderReaderHeading();
    searchEmpty("让每个发现，都有出处", "输入论文中出现的关键词。点击匹配片段，回到对应原文。", "想找具体细节？试试方法名称、参数或一句原文。");
  }

  function hitKey(hit, index) { return `${hit.document_id}:${hit.run_id}:${hit.page}:${hit.block_id || index}`; }

  function renderHitSelection() {
    ui.searchResults.querySelectorAll(".hit-card").forEach((node) => {
      const selected = node.dataset.hitKey === state.selectedHit;
      node.classList.toggle("is-selected", selected);
      node.setAttribute("aria-pressed", String(selected));
    });
  }

  function appendHighlightedText(container, text, query) {
    const source = String(text || "");
    const lower = source.toLocaleLowerCase();
    const needle = query.toLocaleLowerCase();
    if (!needle || !lower.includes(needle)) { container.textContent = source; return; }
    let cursor = 0;
    let index;
    while ((index = lower.indexOf(needle, cursor)) !== -1) {
      container.append(document.createTextNode(source.slice(cursor, index)));
      container.append(element("mark", "", source.slice(index, index + query.length)));
      cursor = index + query.length;
    }
    container.append(document.createTextNode(source.slice(cursor)));
  }

  function renderHits() {
    ui.searchResults.replaceChildren();
    state.hits.forEach((hit, index) => {
      const key = hitKey(hit, index);
      const button = element("button", "hit-card");
      button.type = "button";
      button.dataset.hitKey = key;
      button.setAttribute("aria-label", `${hit.document_title || hit.filename || "文献"}，查看第 ${hit.page} 页原文`);
      const heading = element("span", "hit-heading");
      heading.append(icon("file"), element("span", "hit-title", hit.document_title || hit.filename || "文献"));
      const text = element("p", "hit-text");
      appendHighlightedText(text, hit.text, state.query);
      const location = element("span", "hit-location");
      location.append(element("span", "hit-page", `P. ${hit.page}`), element("span", "", normalizeBox(hit.bbox) ? "定位原文区域" : "定位到这一页"), icon("arrow"));
      button.append(heading, text, location);
      button.addEventListener("click", async () => {
        const navigation = ++navigationRequest;
        if (!state.documents.some((doc) => doc.id === hit.document_id)) {
          try {
            const result = await api(`/api/documents/${encodeURIComponent(hit.document_id)}`);
            if (navigation !== navigationRequest) return;
            if (result.document) state.documents.push(result.document);
          } catch (error) { notice(error.message, true); return; }
        }
        selectDocument(hit.document_id, { page: hit.page, runId: hit.run_id, bbox: hit.bbox, hitKey: key, fromSearch: true });
      });
      ui.searchResults.append(button);
    });
    renderHitSelection();
  }

  async function search() {
    const query = ui.searchQuery.value.trim();
    if (!query) { ui.searchQuery.focus(); return; }
    if (!state.documents.length) {
      searchEmpty("先导入一篇论文", "文献完成文字提取后，就可以在这里查找原文证据。", "扫描版 PDF 需要文字识别能力，当前可先查看原文。");
      return;
    }
    const request = ++searchRequest;
    state.searching = true;
    state.query = query;
    state.selectedHit = null;
    state.highlight = null;
    renderHighlight();
    ui.searchButton.disabled = true;
    ui.searchResults.setAttribute("aria-busy", "true");
    ui.resultsHeading.hidden = true;
    const loading = element("div", "search-loading");
    loading.append(element("span", "spinner"), element("span", "", "正在查找原文片段"));
    ui.searchResults.replaceChildren(loading);
    const params = new URLSearchParams({ q: query });
    if (ui.searchScope.value === "current" && state.selectedId) params.set("document_id", state.selectedId);
    try {
      const result = await api(`/api/search?${params}`);
      if (request !== searchRequest) return;
      state.hits = Array.isArray(result.hits) ? result.hits : [];
      ui.resultsHeading.hidden = false;
      ui.resultsTitle.textContent = `${state.hits.length} 条匹配片段`;
      if (state.hits.length) renderHits();
      else searchEmpty("未找到匹配片段", `没有找到与“${query}”匹配的已提取原文。`, "试试更短的关键词，或检查文献是否已完成文字提取。关键词检索不会自动改写或推测原文。");
    } catch (error) {
      if (request !== searchRequest) return;
      searchEmpty("检索暂时未完成", error.message, "保留了输入内容，可以再次点击“查找证据”。");
    } finally {
      if (request === searchRequest) {
        state.searching = false;
        ui.searchButton.disabled = false;
        ui.searchResults.setAttribute("aria-busy", "false");
      }
    }
  }

  async function reparse() {
    const doc = currentDocument();
    if (!doc || state.reparsing || isPending(doc.current_run)) return;
    state.reparsing = true;
    renderReaderHeading();
    try {
      const result = await api(`/api/documents/${encodeURIComponent(doc.id)}/parse-runs`, { method: "POST" });
      listRequest += 1;
      const storedDocument = state.documents.find((item) => item.id === doc.id);
      if (result.run && storedDocument) storedDocument.current_run = result.run;
      notice("已开始重新提取文字，之前的处理结果会保留。");
      if (state.selectedId === doc.id) selectDocument(doc.id, { page: state.page, keepView: true });
      renderLibrary();
      schedulePolling();
      await refreshDocuments({ quiet: true });
    } catch (error) { notice(error.message, true); }
    finally { state.reparsing = false; renderReaderHeading(); }
  }

  async function initialize() {
    ui.documentList.setAttribute("aria-busy", "true");
    if (!state.documents.length) {
      const loading = element("div", "list-message", "正在加载文献库…");
      ui.documentList.replaceChildren(loading);
    }
    const capabilities = api("/api/capabilities").then((result) => {
      state.capabilities = result;
      if (Number(result.max_upload_mb) > 0) ui.uploadLimit.textContent = `每个文件不超过 ${result.max_upload_mb} MB`;
      ui.searchExplanation.textContent = "按关键词查找原文片段，点击结果核对出处。文献问答可在“问答与写作”中创建。";
      if (state.connected) setConnection(true);
      renderReaderHeading();
    }).catch(() => { ui.uploadLimit.textContent = "或点击选择文件"; });
    await Promise.allSettled([capabilities, refreshDocuments()]);
  }

  [ui.uploadButton, ui.emptyUploadButton, ui.dropZone].forEach((button) => button.addEventListener("click", () => ui.fileInput.click()));
  ui.fileInput.addEventListener("change", () => uploadFiles(ui.fileInput.files));
  ui.documentFilter.addEventListener("input", renderLibrary);
  ui.previousPage.addEventListener("click", () => goToPage(state.page - 1));
  ui.nextPage.addEventListener("click", () => goToPage(state.page + 1));
  ui.firstPage.addEventListener("click", () => goToPage(1));
  ui.lastPage.addEventListener("click", () => goToPage(currentDocument()?.page_count));
  ui.pageForm.addEventListener("submit", (event) => { event.preventDefault(); goToPage(ui.pageNumber.value); });
  ui.pageNumber.addEventListener("change", () => goToPage(ui.pageNumber.value));
  ui.retryPageButton.addEventListener("click", loadPage);
  ui.reparseButton.addEventListener("click", reparse);
  ui.searchForm.addEventListener("submit", (event) => { event.preventDefault(); search(); });
  ui.clearSearchButton.addEventListener("click", () => clearSearch());
  ui.searchScope.addEventListener("change", () => clearSearch(false));
  ui.searchQuery.addEventListener("input", () => {
    if (state.searching) clearSearch(false);
    else if (!ui.searchQuery.value.trim() && state.query) clearSearch(false);
  });
  document.querySelectorAll("[data-view]").forEach((button) => button.addEventListener("click", () => setView(button.dataset.view)));
  ui.libraryPanel.addEventListener("dragenter", (event) => {
    if (!Array.from(event.dataTransfer?.types || []).includes("Files")) return;
    event.preventDefault(); dragDepth += 1;
    if (!state.uploading) ui.libraryPanel.classList.add("is-dragging");
  });
  ui.libraryPanel.addEventListener("dragover", (event) => {
    if (!Array.from(event.dataTransfer?.types || []).includes("Files")) return;
    event.preventDefault(); event.dataTransfer.dropEffect = state.uploading ? "none" : "copy";
  });
  ui.libraryPanel.addEventListener("dragleave", (event) => {
    event.preventDefault(); dragDepth = Math.max(0, dragDepth - 1);
    if (!dragDepth) ui.libraryPanel.classList.remove("is-dragging");
  });
  ui.libraryPanel.addEventListener("drop", (event) => {
    event.preventDefault(); dragDepth = 0; ui.libraryPanel.classList.remove("is-dragging");
    if (event.dataTransfer?.files.length) uploadFiles(event.dataTransfer.files);
  });
  window.addEventListener("dragover", (event) => {
    if (Array.from(event.dataTransfer?.types || []).includes("Files")) event.preventDefault();
  });
  window.addEventListener("drop", (event) => {
    if (Array.from(event.dataTransfer?.types || []).includes("Files")) event.preventDefault();
  });
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && state.documents.some((doc) => isPending(doc.current_run))) refreshDocuments({ quiet: true });
  });
  window.ResearchWorkspace = {
    api,
    refreshLibrary: () => refreshDocuments(),
    currentDocument: () => currentDocument(),
    selectedIds: () => state.documents.filter(doc => doc.is_current_version !== false && !doc.archived_at).map(doc => doc.id),
    invalidateEvidenceNavigation() { navigationRequest += 1; },
    async onEvidence(evidence) {
      if (!evidence?.document_id) return;
      const navigation = ++navigationRequest;
      if (!state.documents.some(doc => doc.id === evidence.document_id)) {
        const result = await api(`/api/documents/${encodeURIComponent(evidence.document_id)}`);
        if (navigation !== navigationRequest) return;
        state.documents.push(result.document);
      }
      selectDocument(evidence.document_id, {runId:evidence.run_id, page:evidence.page || 1, bbox:evidence.bbox, hitKey:evidence.block_id});
      window.dispatchEvent(new CustomEvent("research:reader"));
    },
  };
  initialize();
})();
