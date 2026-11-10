"use strict";

(() => {
  const byId = (id) => document.getElementById(id);
  const ui = Object.fromEntries([
    "workspace", "libraryPanel", "documentList", "documentCount", "documentFilter", "libraryHistoryToggle",
    "uploadButton", "emptyUploadButton", "fileInput", "dropZone", "uploadStatus", "uploadLimit",
    "connectionState", "connectionText", "libraryFooter", "appNotice", "noticeText", "dismissNotice",
    "readerTitle", "readerSubtitle", "reparseButton", "readerToolbar", "readerMessage", "readerViewport",
    "readerEmpty", "pageLoading", "pagePlaceholder", "pageError", "pageErrorText", "retryPageButton", "paperPage", "pageImage",
    "evidenceHighlight", "readerFooterText", "pageForm", "pageNumber", "pageTotal", "firstPage", "previousPage", "nextPage", "lastPage",
    "searchForm", "searchQuery", "searchScope", "searchMode", "searchEnglish", "searchEnglishDetails", "exportSearchButton", "currentScopeOption", "searchButton", "searchExplanation",
    "resultsHeading", "resultsTitle", "clearSearchButton", "searchResults",
  ].map((id) => [id, byId(id)]));

  const state = {
    documents: [], capabilities: null, selectedId: null, selectedRunId: null, pinnedRun: false,
    page: 1, highlight: null, selectedHit: null, query: "", hits: [], uploading: false,
    searching: false, reparsing: false, connected: false, searchRecord: null, showHistory: false,
  };
  let listRequest = 0;
  let pageRequest = 0;
  let pageLoadingTimer = null;
  let displayedPage = null;
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
      const error = new Error(typeof result.detail === "string" ? result.detail : `请求未完成（${response.status}），请稍后重试。`);
      error.status = response.status;
      throw error;
    }
    return result;
  }

  function notice(message, error = false) {
    window.ResearchNotices.show(message, error);
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
    const historical = state.documents.filter(doc => doc.is_current_version === false).length;
    ui.documentCount.textContent = String(state.documents.length - historical);
    ui.documentCount.title = `${state.documents.length - historical} 份常用文献，${historical} 份历史版本`;
    ui.libraryHistoryToggle.hidden = historical === 0;
    ui.libraryHistoryToggle.textContent = `${state.showHistory ? "收起" : "显示"}历史版本（${historical}）`;
    ui.libraryHistoryToggle.setAttribute("aria-pressed", String(state.showHistory));
    ui.documentList.setAttribute("aria-busy", "false");
    const filter = ui.documentFilter.value.trim().toLocaleLowerCase();
    const documents = state.documents.filter(doc => state.showHistory || doc.is_current_version !== false || doc.id === state.selectedId)
      .filter((doc) => `${doc.title || ""} ${doc.filename || ""}`.toLocaleLowerCase().includes(filter));
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
      if (doc.is_current_version === false) meta.append(element("span", "document-history-label", "历史版本 · 原引用保留"));
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
    const visiblePage = displayedPage?.id === doc.id ? displayedPage : null;
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
    else if (visiblePage?.hasText === false) message = "这一页没有可检索文字，仍可查看原文页面。";
    ui.readerMessage.textContent = message;
    ui.readerMessage.hidden = !message;
    ui.readerFooterText.textContent = !ui.evidenceHighlight.hidden && visiblePage
      ? `已定位第 ${visiblePage.page} 页的检索证据，请结合上下文核对。`
      : `第 ${visiblePage?.page ?? state.page} / ${total || "—"} 页 · 选择证据片段可定位原文`;
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
        pageRequest += 1;
        hidePageLoading();
        clearDisplayedPage();
        ui.readerTitle.textContent = "原文阅读";
        ui.readerSubtitle.textContent = "导入论文后阅读原文与证据。";
        ui.readerEmpty.hidden = false;
        ui.pageError.hidden = true;
        ui.readerMessage.hidden = true;
        ui.readerFooterText.textContent = "原文与检索证据将在这里关联";
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
    const previousRun = state.selectedRunId;
    state.selectedId = id;
    state.selectedRunId = options.runId || readableRun(doc)?.id || null;
    state.pinnedRun = Boolean(options.runId);
    state.page = clampReadingPage(options.page ?? rememberedPage(id), doc);
    rememberReadingPage(doc);
    if ((changed || previousRun !== state.selectedRunId) && ui.searchScope.value === "current" && !options.fromSearch) clearSearch(false);
    // Clear evidence belonging to the previous search before applying the new
    // citation. Otherwise current-document scope erases a cross-paper bbox.
    state.highlight = normalizeBox(options.bbox);
    state.selectedHit = options.hitKey || null;
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
    const box = displayedPage?.id === state.selectedId && displayedPage.page === state.page
      && displayedPage.runId === state.selectedRunId ? state.highlight : null;
    ui.evidenceHighlight.hidden = !box;
    if (!box) return;
    ui.evidenceHighlight.style.left = `${box[0] * 100}%`;
    ui.evidenceHighlight.style.top = `${box[1] * 100}%`;
    ui.evidenceHighlight.style.width = `${(box[2] - box[0]) * 100}%`;
    ui.evidenceHighlight.style.height = `${(box[3] - box[1]) * 100}%`;
  }

  function hidePageLoading() {
    window.clearTimeout(pageLoadingTimer);
    ui.pageLoading.hidden = true;
    ui.pagePlaceholder.hidden = true;
  }

  function clearDisplayedPage() {
    displayedPage = null;
    ui.paperPage.hidden = true;
    ui.evidenceHighlight.hidden = true;
    ui.pageImage.removeAttribute("src");
  }

  async function loadPage() {
    const doc = currentDocument();
    if (!doc) return;
    const request = ++pageRequest;
    const id = doc.id;
    const page = state.page;
    const runId = state.selectedRunId;
    const retained = displayedPage?.id === id ? displayedPage : null;
    const sameImage = retained?.page === page;
    const path = `/api/documents/${encodeURIComponent(id)}/pages/${page}`;
    hidePageLoading();
    ui.readerEmpty.hidden = true;
    ui.pageError.hidden = true;
    ui.evidenceHighlight.hidden = true;
    if (!retained) {
      clearDisplayedPage();
      ui.readerViewport.scrollTop = 0;
      ui.pagePlaceholder.style.removeProperty("--page-ratio");
      ui.pagePlaceholder.hidden = false;
    }
    if (!sameImage) pageLoadingTimer = window.setTimeout(() => {
      if (request !== pageRequest) return;
      ui.pageLoading.textContent = `正在打开第 ${page} 页…`;
      ui.pageLoading.hidden = false;
    }, 150);
    renderReaderHeading();

    const params = new URLSearchParams();
    if (runId) params.set("run_id", runId);
    const suffix = params.size ? `?${params}` : "";
    let pageGeometry = null;
    let hasText = sameImage && retained.runId === runId ? retained.hasText : null;
    const metadata = api(`${path}${suffix}`).then((result) => {
      if (request !== pageRequest) return;
      pageGeometry = result;
      const width = Number(result.width), height = Number(result.height);
      if (!ui.pagePlaceholder.hidden && width > 0 && height > 0 && Number.isFinite(width) && Number.isFinite(height)) {
        ui.pagePlaceholder.style.setProperty("--page-ratio", `${width} / ${height}`);
      }
      hasText = Boolean(result.text?.trim() || result.blocks?.some((block) => block.text?.trim()));
      if (displayedPage?.request === request) {
        displayedPage.hasText = hasText;
        renderReaderHeading();
      }
    }).catch(() => {
      // The original page can still be read while text extraction is pending.
    });

    if (!sameImage) {
      let scale = 2;
      const image = new Image();
      while (request === pageRequest) {
        image.src = `${path}/image?scale=${scale}`;
        try {
          await image.decode();
          break;
        } catch {
          if (request !== pageRequest) return;
          // Only a failed render needs geometry. Give a pending metadata request
          // a bounded head start before looking for a safe lower scale.
          if (!pageGeometry) {
            let timeout;
            await Promise.race([metadata, new Promise(resolve => { timeout = setTimeout(resolve, 250); })]);
            clearTimeout(timeout);
            if (request !== pageRequest) return;
          }
          const area = Number(pageGeometry?.width) * Number(pageGeometry?.height);
          const oversized = Number.isFinite(area) && area * scale * scale > 16_000_000;
          const next = oversized ? [1, .5, .25].find(value => value < scale && area * value * value <= 16_000_000) : undefined;
          if (next !== undefined) {
            scale = next;
            continue;
          }
          hidePageLoading();
          clearDisplayedPage();
          ui.pageErrorText.textContent = oversized
            ? `第 ${page} 页超过本机安全渲染上限，最小倍率仍无法打开。`
            : `第 ${page} 页暂时无法打开，请确认服务可用后重试。`;
          ui.pageError.hidden = false;
          renderReaderHeading();
          return;
        }
      }
      if (request !== pageRequest) return;
      image.id = "pageImage";
      image.alt = `${documentName(doc)}，第 ${page} 页原文`;
      image.width = image.naturalWidth;
      image.height = image.naturalHeight;
      image.draggable = false;
      image.dataset.renderScale = String(scale);
      if (!reducedMotion.matches) {
        image.classList.add("reader-page-arrival");
        image.addEventListener("animationend", () => image.classList.remove("reader-page-arrival"), { once: true });
      }
      ui.pageImage.replaceWith(image);
      ui.pageImage = image;
      ui.readerViewport.scrollTop = 0;
    }
    displayedPage = { id, page, runId, request, hasText };
    hidePageLoading();
    ui.paperPage.hidden = false;
    renderHighlight();
    renderReaderHeading();
    if (!sameImage) window.dispatchEvent(new CustomEvent("research:page-ready"));
    if (state.highlight) {
      window.requestAnimationFrame(() => {
        if (request !== pageRequest || !state.highlight) return;
        const top = ui.paperPage.offsetTop + ui.paperPage.clientHeight * state.highlight[1] - ui.readerViewport.clientHeight * .28;
        const left = ui.paperPage.offsetLeft + ui.paperPage.clientWidth * (state.highlight[0] + state.highlight[2]) / 2 - ui.readerViewport.clientWidth / 2;
        ui.readerViewport.scrollTo({ top: Math.max(0, top), left: Math.max(0, left), behavior: reducedMotion.matches ? "auto" : "smooth" });
      });
    }
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
    state.searchRecord = null;
    ui.exportSearchButton.hidden = true;
    state.selectedHit = null;
    state.highlight = null;
    ui.searchButton.disabled = false;
    ui.searchResults.setAttribute("aria-busy", "false");
    ui.resultsHeading.hidden = true;
    if (clearInput) ui.searchQuery.value = "";
    renderHighlight();
    renderReaderHeading();
    searchEmpty("让每个发现，都有出处", "输入问题或关键词。点击候选片段，核对固定版本的原文。", "语义检索可以查找近义表达；相似度只用于排序，不代表结论已被原文证明。");
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
    const record = state.searchRecord;
    if (record?.method) {
      const meta = element("div", "search-audit");
      const labels = {searched:"本地语义检索",lexical_fallback:"已降级为关键词检索",semantic_unavailable:"语义检索未完成",busy:"本地检索繁忙",source_budget_exceeded:"超出检索范围",no_readable_text:"没有可读正文"};
      meta.append(element("p", "", `${labels[record.status] || "本地检索"} · ${record.source_snapshots?.length || 0} 份固定来源`));
      (record.warnings || []).forEach(text => meta.append(element("p", "", text)));
      const explanation = element("details", "search-audit-details");
      explanation.append(element("summary", "", "范围与核对说明"), element("p", "", "候选片段按相关性排序，请结合上下文核对；不是自动生成的回答。"));
      meta.append(explanation);
      ui.searchResults.append(meta);
    }
    state.hits.forEach((hit, index) => {
      const key = hitKey(hit, index);
      const button = element("button", "hit-card");
      button.type = "button";
      button.dataset.hitKey = key;
      button.setAttribute("aria-label", `${hit.document_title || hit.filename || "文献"}，查看第 ${hit.page} 页原文`);
      const heading = element("span", "hit-heading");
      heading.append(icon("file"), element("span", "hit-title", hit.document_title || hit.filename || "文献"));
      if (hit.channels) heading.append(element("span", "hit-channel", hit.channels.includes("semantic") ? "语义候选" : "关键词"));
      const text = element("p", "hit-text");
      appendHighlightedText(text, hit.text, state.query);
      const location = element("span", "hit-location");
      const source = record?.source_snapshots?.find(s => s.document_id === hit.document_id && s.run_id === hit.run_id);
      location.append(element("span", "hit-page", `P. ${hit.page}`), element("span", "", `${source ? `解析${source.parse_version} · ` : ""}${normalizeBox(hit.bbox) ? "定位原文区域" : "定位到这一页"}`), icon("arrow"));
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
    state.hits = [];
    state.searchRecord = null;
    ui.exportSearchButton.hidden = true;
    state.selectedHit = null;
    state.highlight = null;
    renderHighlight();
    ui.searchButton.disabled = true;
    ui.searchResults.setAttribute("aria-busy", "true");
    ui.resultsHeading.hidden = true;
    const loading = element("div", "search-loading");
    loading.append(element("span", "spinner"), element("span", "", "正在查找原文片段"));
    ui.searchResults.replaceChildren(loading);
    const mode = ui.searchMode.value;
    const params = new URLSearchParams({ q: query, mode });
    if (mode !== "literal" && ui.searchEnglish.value.trim()) params.set("english_query", ui.searchEnglish.value.trim());
    if (ui.searchScope.value === "current" && state.selectedId) {
      params.set("document_id", state.selectedId);
      if (state.selectedRunId) params.set("run_id", state.selectedRunId);
    }
    try {
      const result = await api(`/api/search?${params}`);
      if (request !== searchRequest) return;
      state.hits = Array.isArray(result.hits) ? result.hits : [];
      state.searchRecord = result;
      ui.exportSearchButton.hidden = !result.search_id;
      ui.resultsHeading.hidden = false;
      ui.resultsTitle.textContent = `${state.hits.length} 条${mode === "literal" ? "匹配" : "候选"}片段`;
      if (state.hits.length) renderHits();
      else if (mode === "literal") searchEmpty("未找到匹配片段", `没有找到与“${query}”匹配的已提取原文。`, "可改用混合检索查找近义表达，或检查文献是否已完成文字提取。");
      else {
        renderHits();
        ui.searchResults.append(element("p", "search-no-hits", result.complete ? "未返回达到当前筛选条件的候选片段。可调整检索词；这不代表全文不存在相关内容。" : "本次检索未完整完成，请查看状态提示后重试或改用字面检索。"));
      }
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
      updateSearchMode();
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
  ui.libraryHistoryToggle.addEventListener("click", () => { state.showHistory = !state.showHistory; renderLibrary(); });
  ui.clearSearchButton.addEventListener("click", () => clearSearch());
  ui.searchScope.addEventListener("change", () => clearSearch(false));
  function updateSearchMode() {
    const literal = ui.searchMode.value === "literal";
    ui.searchEnglishDetails.hidden = literal;
    ui.searchExplanation.textContent = literal ? "按连续原文查找，点击结果核对出处。" : "本地检索，无付费调用。主要面向英文；首次索引较慢，后续复用缓存。";
  }
  ui.searchMode.addEventListener("change", () => { clearSearch(false); updateSearchMode(); });
  ui.searchEnglish.addEventListener("input", () => { if (state.searching || state.searchRecord) clearSearch(false); });
  ui.exportSearchButton.addEventListener("click", () => {
    if (!state.searchRecord?.search_id) return;
    const blob = new Blob([JSON.stringify(state.searchRecord,null,2)], {type:"application/json;charset=utf-8"});
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a"); link.href = url; link.download = `${state.searchRecord.search_id.slice(0,18)}.json`; link.click();
    window.setTimeout(() => URL.revokeObjectURL(url),1000);
  });
  ui.searchQuery.addEventListener("input", () => {
    if (state.searching || state.searchRecord) clearSearch(false);
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
