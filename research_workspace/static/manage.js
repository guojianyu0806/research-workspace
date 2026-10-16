"use strict";

(() => {
  const instances = new WeakMap();
  let sequence = 0;
  const el = (tag, className = "", text) => {
    const result = document.createElement(tag);
    if (className) result.className = className;
    if (text !== undefined) result.textContent = String(text);
    return result;
  };
  const btn = (text, action, className = "rm-button") => {
    const result = el("button", className, text);
    result.type = "button";
    if (action) result.addEventListener("click", action);
    return result;
  };
  const field = (title, control) => {
    const result = el("label", "rm-field");
    result.append(el("span", "rm-field-title", title), control);
    return result;
  };
  const option = (value, label) => {
    const result = el("option", "", label);
    result.value = value;
    return result;
  };
  const json = (method, body) => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const pathFor = (id) => `/api/documents/${encodeURIComponent(id)}`;
  const statusName = (value) => ({ queued: "等待解析", running: "解析中", completed: "解析完成", partial: "部分内容可用", needs_ocr: "尚无可提取文字", failed: "解析失败", cancelled: "已取消" })[value] || "尚未解析";

  function mount(container, options = {}) {
    if (!container || typeof options.api !== "function") throw new Error("文献管理需要容器和api方法。");
    instances.get(container)?.destroy();
    const { api, onEvidence, onTranslate, refreshLibrary } = options;
    const prefix = `research-manage-${++sequence}`;
    let alive = true;
    let refreshEpoch = 0;
    let documentEpoch = 0;
    let pageEpoch = 0;
    let textNavigationEpoch = 0;
    let diffEpoch = 0;
    let documents = [];
    let selectedId = null;
    let selectedDocument = null;
    let structureData = null;
    let currentPage = null;
    let pageLoading = false;
    let pageReadSelection = null;
    let pageReadError = "";
    let selection = null;
    let invalidSelection = false;
    let pagePreview = null;
    let preserveLineBreaks = false;
    let suspended = false;
    let sourceDialog = null;
    let activeTab = "metadata";
    const extensions = {};
    const pageCache = new Map();
    const pendingPages = new Map();
    const referenceElements = new Map();
    const textElements = new Map();
    const translateButtons = new Map();

    const shell = el("section", "research-manage rm-shell");
    const header = el("header", "rm-header");
    const headerWords = el("div");
    headerWords.append(el("span", "rm-kicker", "LIBRARY & SOURCES"), el("h2", "", "管理文献，核对每一处出处"), el("p", "rm-description", "整理文献信息，确认版本关系，沿着引用回到原文。"));
    const refreshButton = btn("刷新文献", () => refresh());
    header.append(headerWords, refreshButton);
    const notice = el("div", "rm-notice");
    notice.hidden = true;
    notice.setAttribute("role", "status");
    notice.setAttribute("aria-live", "polite");
    const select = el("select", "rm-select");
    select.setAttribute("aria-label", "选择文献，含已归档文献");
    const documentBar = el("div", "rm-document-bar");
    const documentSummary = el("p", "rm-muted");
    documentBar.append(field("当前文献 · 含归档记录", select), documentSummary);
    const tabs = el("div", "rm-tabs");
    tabs.setAttribute("role", "tablist");
    tabs.setAttribute("aria-label", "文献管理工作区");
    const panes = {};
    const tabButtons = {};
    for (const [key, title] of [["metadata", "资料与版本"], ["structure", "标题与引用"], ["text", "本页文字"], ["layout", "版面结构"], ["references", "参考文献筛选"]]) {
      const tab = btn(title, () => switchTab(key), "rm-tab");
      tab.id = `${prefix}-tab-${key}`;
      tab.setAttribute("role", "tab");
      tab.setAttribute("aria-controls", `${prefix}-panel-${key}`);
      const pane = el("section", "rm-pane");
      pane.id = `${prefix}-panel-${key}`;
      pane.setAttribute("role", "tabpanel");
      pane.setAttribute("aria-labelledby", tab.id);
      pane.tabIndex = 0;
      tabs.append(tab);
      panes[key] = pane;
      tabButtons[key] = tab;
    }
    const content = el("div", "rm-content");
    content.append(...Object.values(panes));
    shell.append(header, notice, documentBar, tabs, content);
    container.replaceChildren(shell);
    const discardPendingSelection = () => { if (pageLoading) pageReadSelection = null; };
    // Inert can hide the old range. Restore it after failure only if the user
    // has not started another pointer or keyboard interaction while waiting.
    shell.addEventListener("pointerdown", discardPendingSelection, true);
    shell.addEventListener("keydown", discardPendingSelection, true);

    function showNotice(message, error = false) {
      if (!alive) return;
      window.ResearchNotices.show(message, error, notice);
    }
    function empty(target, title, message) {
      const box = el("div", "rm-empty");
      box.append(el("h3", "", title), el("p", "", message));
      target.replaceChildren(box);
    }
    function panelTitle(title, detail) {
      const result = el("div", "rm-section-heading");
      result.append(el("h3", "", title));
      if (detail) result.append(el("p", "rm-muted", detail));
      return result;
    }
    function evidenceButton(evidence, label = "定位原文 →") {
      const button = btn(label, () => {
        Promise.resolve().then(() => onEvidence?.({ ...evidence, block_id: evidence.block_id || evidence.id })).catch((error) => showNotice(error.message || String(error), true));
      }, "rm-link-button");
      button.disabled = typeof onEvidence !== "function" || !evidence?.document_id || !evidence?.page;
      return button;
    }
    async function act(button, operation) {
      button.disabled = true;
      try { await operation(); }
      catch (error) { showNotice(error.message || String(error), true); }
      finally { if (alive) button.disabled = false; }
    }
    async function changed(message) {
      if (typeof refreshLibrary === "function") await refreshLibrary();
      await refresh();
      showNotice(message);
    }
    function selectedFromHost() {
      const value = typeof options.currentDocument === "function" ? options.currentDocument() : options.currentDocument;
      return typeof value === "string" ? value : value?.id;
    }
    function switchTab(key, loadDefault = true) {
      sourceDialog?.close();
      options.invalidateEvidenceNavigation?.();
      if (activeTab !== key) textNavigationEpoch++;
      activeTab = key;
      for (const name of Object.keys(panes)) {
        panes[name].hidden = name !== key;
        tabButtons[name].classList.toggle("is-active", name === key);
        tabButtons[name].setAttribute("aria-selected", String(name === key));
        tabButtons[name].tabIndex = name === key ? 0 : -1;
      }
      if (loadDefault && key === "text" && selectedDocument && !currentPage && !pageLoading) loadPage(1);
      mountExtension(key);
    }
    function clearExtensions() {
      for (const key of Object.keys(extensions)) { extensions[key]?.destroy(); delete extensions[key]; }
    }
    function mountExtension(key) {
      if (!selectedDocument || extensions[key]) return;
      const module = key === "layout" ? window.ResearchLayout : key === "references" ? window.ResearchReferences : null;
      if (module) extensions[key] = module.mount(panes[key], { api, document: selectedDocument, onEvidence });
    }
    tabs.addEventListener("keydown", (event) => {
      const names = Object.keys(tabButtons);
      const index = names.indexOf(activeTab);
      let next = index;
      if (event.key === "ArrowRight") next = (index + 1) % names.length;
      else if (event.key === "ArrowLeft") next = (index + names.length - 1) % names.length;
      else if (event.key === "Home") next = 0;
      else if (event.key === "End") next = names.length - 1;
      else return;
      event.preventDefault();
      switchTab(names[next]);
      tabButtons[names[next]].focus();
    });
    select.addEventListener("change", () => loadDocument(select.value));

    async function refresh(preferredId) {
      const epoch = ++refreshEpoch;
      refreshButton.disabled = true;
      try {
        const result = await api("/api/documents?include_archived=true");
        if (!alive || epoch !== refreshEpoch) return;
        documents = Array.isArray(result.documents) ? result.documents : [];
        select.replaceChildren();
        if (!documents.length) {
          clearExtensions();
          selectedDocument = null; selectedId = null; currentPage = null; pageLoading = false;
          documentEpoch++; pageEpoch++; diffEpoch++;
          select.append(option("", "尚未导入文献"));
          select.disabled = true;
          documentSummary.textContent = "上传PDF后，可在这里整理资料、查看引用和选择文字翻译。";
          for (const pane of Object.values(panes)) empty(pane, "文献库还是空的", "请先通过工作台的上传入口导入PDF；上传后点击刷新文献。");
          return;
        }
        select.disabled = false;
        documents.forEach((doc) => select.append(option(doc.id, `${doc.archived_at ? "[已归档] " : ""}${doc.title || doc.filename}`)));
        const desired = typeof preferredId === "string" ? preferredId : selectedId || selectedFromHost();
        const next = documents.some((doc) => doc.id === desired) ? desired : documents[0].id;
        select.value = next;
        await loadDocument(next);
      } catch (error) { if (alive && epoch === refreshEpoch) showNotice(error.message || String(error), true); }
      finally { if (alive && epoch === refreshEpoch) refreshButton.disabled = false; }
    }

    async function loadDocument(id) {
      sourceDialog?.close();
      options.invalidateEvidenceNavigation?.();
      const epoch = ++documentEpoch;
      clearExtensions();
      pageEpoch++; diffEpoch++;
      textNavigationEpoch++;
      selectedId = id;
      selectedDocument = documents.find((doc) => doc.id === id) || null;
      currentPage = null; pageLoading = false; selection = null; invalidSelection = false; structureData = null;
      pageReadSelection = null; pageReadError = "";
      pageCache.clear(); referenceElements.clear(); textElements.clear(); translateButtons.clear();
      if (!selectedDocument) return;
      const doc = selectedDocument;
      select.value = id;
      documentSummary.textContent = `${doc.page_count} 页 · ${statusName(doc.current_run?.status)}${doc.archived_at ? " · 已归档，原始文件和历史证据仍保留" : ""}`;
      renderMetadata(doc);
      empty(panes.structure, "正在读取标题与引用", "使用已保存的解析结果整理可定位的文献结构。");
      renderTextControls(doc);
      mountExtension(activeTab);
      const settled = await Promise.allSettled([
        api(`${pathFor(id)}/versions`),
        api(`${pathFor(id)}/structure`),
      ]);
      if (!alive || epoch !== documentEpoch) return;
      const versionTarget = panes.metadata.querySelector(".rm-version-body");
      if (settled[0].status === "fulfilled") renderVersions(versionTarget, doc, settled[0].value);
      else empty(versionTarget, "版本信息暂不可用", settled[0].reason?.message || "请点击刷新文献重试。");
      if (settled[1].status === "fulfilled") {
        structureData = settled[1].value;
        renderStructure(structureData);
      } else empty(panes.structure, "标题与引用暂不可用", settled[1].reason?.message || "请完成解析后刷新重试。");
      if (activeTab === "text" && !currentPage && !pageLoading) await loadPage(1);
    }

    function renderMetadata(doc) {
      const layout = el("div", "rm-metadata-layout");
      const card = el("section", "rm-card");
      card.append(panelTitle("文献信息", "编辑显示信息不会修改原始PDF。"));
      const form = el("form", "rm-form");
      const title = el("input", "rm-input");
      title.value = doc.title || ""; title.maxLength = 1000; title.required = true;
      const authors = el("textarea", "rm-input rm-authors");
      authors.value = (doc.authors || []).join("\n"); authors.rows = 4;
      authors.placeholder = "每行填写一位作者";
      form.append(field("标题", title), field("作者 · 每行一位", authors));
      const filename = el("dl", "rm-file-facts");
      filename.append(el("dt", "", "原文件"), el("dd", "", doc.filename), el("dt", "", "解析版本"), el("dd", "", doc.available_run ? `第 ${doc.available_run.version} 版可读结果` : "暂无完整可读结果"));
      form.append(filename);
      const save = btn("保存资料", null, "rm-button rm-primary");
      save.type = "submit";
      form.append(save);
      form.addEventListener("submit", (event) => {
        event.preventDefault();
        act(save, async () => {
          await api(pathFor(doc.id), json("PATCH", { title: title.value.trim(), authors: authors.value.split(/\r?\n/).map((name) => name.trim()).filter(Boolean) }));
          await changed("文献信息已保存。");
        });
      });
      const archiveArea = el("div", "rm-archive-area");
      const archive = btn(doc.archived_at ? "恢复到文献库" : "归档这篇文献", () => act(archive, async () => {
        await api(`${pathFor(doc.id)}/${doc.archived_at ? "restore" : "archive"}`, { method: "POST" });
        await changed(doc.archived_at ? "文献已恢复。" : "文献已归档，仍可从下拉列表恢复。");
      }));
      archiveArea.append(archive, el("p", "rm-muted", "归档后从常用文献列表隐藏，原PDF与历史引用保留。"));
      card.append(form, archiveArea);
      const versions = el("section", "rm-card rm-version-card");
      versions.append(panelTitle("论文版本", "先比较正文，再决定保留关系与当前使用的版本。"));
      const body = el("div", "rm-version-body");
      empty(body, "正在检查版本关系", "仅提供待确认候选，系统不会自动合并论文。");
      versions.append(body);
      layout.append(card, versions);
      panes.metadata.replaceChildren(layout);
    }

    function renderVersions(target, doc, data) {
      target.replaceChildren();
      const family = data.family || {};
      if (family.documents?.length > 1) {
        const group = el("div", "rm-family");
        group.append(el("h4", "", "已确认的版本组"));
        family.documents.forEach((member) => {
          const row = el("div", "rm-family-row");
          const words = el("div");
          words.append(el("strong", "", member.title || member.filename), el("span", "rm-muted", family.current_document_id === member.id ? "当前优先版本" : !family.current_document_id ? "并列保留" : "保留的历史版本"));
          row.append(words);
          if (member.id !== doc.id) {
            const use = btn("使用这份", () => confirmVersion(use, doc.id, member.id, "use_other"), "rm-small-button");
            use.disabled = Boolean(member.archived_at) || family.current_document_id === member.id;
            row.append(use, btn("比较", () => compare(doc, member, target), "rm-small-button"));
          } else if (family.current_document_id !== doc.id) {
            const other = family.documents.find((item) => item.id !== doc.id);
            const use = btn("使用这份", () => confirmVersion(use, doc.id, other.id, "use_document"), "rm-small-button");
            use.disabled = Boolean(doc.archived_at);
            row.append(use);
          }
          group.append(row);
        });
        target.append(group);
      }
      const confirmedMembers = new Set((family.documents || []).filter((member) => member.id !== doc.id).map((member) => member.id));
      const candidates = (data.candidates || []).filter((candidate) => candidate.document?.id !== doc.id && !confirmedMembers.has(candidate.document?.id));
      if (!candidates.length) target.append(el("p", "rm-muted rm-version-empty", "没有发现待确认的高重合候选。不同主题的相似内容不会自动归为同一论文。"));
      candidates.forEach((candidate) => {
        const other = candidate.document;
        const item = el("article", "rm-candidate");
        item.append(el("span", "rm-badge", "待确认"), el("h4", "", other.title || other.filename));
        const reasons = [];
        if (candidate.reason === "same_arxiv_id") reasons.push("arXiv论文编号一致");
        if (Number.isFinite(candidate.semantic_similarity)) reasons.push(`开篇语义相似 ${Math.round(candidate.semantic_similarity * 100)}%（仅为版本候选）`);
        if (Number.isFinite(candidate.body_similarity)) reasons.push(`正文指纹重合 ${Math.round(candidate.body_similarity * 100)}%`);
        if (candidate.arxiv?.version) reasons.push(`候选标注 v${candidate.arxiv.version}`);
        item.append(el("p", "rm-muted", reasons.join(" · ")));
        if (candidate.conflicting_identifiers) item.append(el("p", "rm-warning", "论文标识存在冲突，请先比较原文再确认关系。"));
        const actions = el("div", "rm-actions");
        const keep = btn("关联并保留两份", () => confirmVersion(keep, doc.id, other.id, "keep_both"));
        const use = btn("使用候选版本", () => confirmVersion(use, doc.id, other.id, "use_other"), "rm-button rm-primary");
        actions.append(btn("比较正文", () => compare(doc, other, target)), keep, use);
        item.append(actions);
        target.append(item);
      });
      target.append(el("p", "rm-muted", "所有选择均保留原PDF和历史证据；正文重合不代表已经证明论文身份。"));
    }
    async function confirmVersion(button, documentId, otherId, action) {
      await act(button, async () => {
        await api(`${pathFor(documentId)}/versions`, json("POST", { other_id: otherId, action }));
        await changed(action === "keep_both" ? "版本关系已确认，两份文件和历史证据均保留。" : "当前论文版本已切换，历史证据仍可访问。");
      });
    }
    async function compare(doc, other, target) {
      const epoch = ++diffEpoch;
      let output = target.querySelector(".rm-diff");
      if (!output) { output = el("section", "rm-diff"); target.append(output); }
      empty(output, "正在比较两份正文", "差异会分别保留两侧的原文位置。");
      try {
        const data = await api(`${pathFor(doc.id)}/diff?other_id=${encodeURIComponent(other.id)}`);
        if (!alive || epoch !== diffEpoch || selectedId !== doc.id) return;
        output.replaceChildren(panelTitle("正文差异", `${doc.filename} → ${other.filename}`));
        output.append(el("p", "rm-muted", "正文块差异可能包含排版或分块变化，请核对两侧原文。"));
        const changes = Array.isArray(data.changes) ? data.changes : [];
        if (!changes.length) output.append(el("p", "rm-empty-inline", "已提取正文中未发现块级差异。"));
        let shown = 0;
        const items = el("div");
        const more = btn("显示更多差异", () => addChanges());
        function addChanges() {
          const end = Math.min(changes.length, shown + 30);
          for (; shown < end; shown++) {
            const change = changes[shown];
            const details = el("details", "rm-change");
            details.open = shown < 2;
            details.append(el("summary", "", `${({ replace: "文本变化", insert: "新增内容", delete: "移除内容" })[change.kind] || "正文变化"} · 第 ${shown + 1} 处`));
            const sides = el("div", "rm-diff-sides");
            for (const [side, label] of [["before", "所选文献"], ["after", "对比文献"]]) {
              const evidence = change[side];
              const sideBox = el("div", "rm-diff-side");
              sideBox.append(el("span", "rm-field-title", label));
              if (evidence) sideBox.append(el("p", "rm-original-text", evidence.text), evidenceButton(evidence, `查看第 ${evidence.page} 页 →`));
              else sideBox.append(el("p", "rm-muted", "此侧没有对应文本。"));
              sides.append(sideBox);
            }
            details.append(sides); items.append(details);
          }
          more.hidden = shown >= changes.length;
          more.textContent = `显示更多差异（已显示 ${shown} / ${changes.length}）`;
        }
        output.append(items, more); addChanges();
        output.scrollIntoView({ block: "nearest", behavior: "auto" });
      } catch (error) { if (alive && epoch === diffEpoch) empty(output, "无法比较正文", error.message || String(error)); }
    }

    function renderStructure(data) {
      referenceElements.clear();
      const root = panes.structure;
      root.replaceChildren(panelTitle("从章节读到引用", "标题、书目和引用均来自已保存正文。规则识别结果可能漏检或误判，请核对原页。"));
      const counts = el("div", "rm-structure-counts");
      counts.append(el("span", "rm-badge", `${data.headings?.length || 0} 个标题`), el("span", "rm-badge", `${data.references?.length || 0} 条书目`), el("span", "rm-badge", `${data.citations?.length || 0} 处数字引用`));
      root.append(counts);
      const layout = el("div", "rm-structure-layout");
      const outline = el("nav", "rm-card rm-outline");
      outline.setAttribute("aria-label", "论文标题树");
      outline.append(el("h3", "", "标题树"));
      if (!data.headings?.length) outline.append(el("p", "rm-muted", "尚未识别到可靠标题，可通过本页文字继续阅读。"));
      (data.headings || []).forEach((heading) => {
        const link = btn(heading.title, () => navigateTextEvidence(heading.evidence), "rm-outline-item");
        link.disabled = !heading.evidence?.page;
        link.style.setProperty("--heading-level", Math.min(3, Math.max(1, Number(heading.level) || 1)));
        outline.append(link);
      });
      const references = el("section", "rm-card rm-references");
      references.append(panelTitle("参考文献与正文出处", "展开正文引用可查看上下文，再定位到引用发生的原页。"));
      const citationMap = new Map((data.citations || []).map((item) => [item.id, item]));
      const referenceMap = new Map((data.references || []).map((item) => [item.id, item]));
      if (!referenceMap.size) references.append(el("p", "rm-muted", "尚未识别到数字编号的参考文献条目。作者年份型等格式可能暂不支持。"));
      (data.references || []).forEach((reference, index) => {
        const article = el("article", "rm-reference");
        article.id = `${prefix}-reference-${index}`; article.tabIndex = -1;
        referenceElements.set(reference.id, article);
        article.append(el("span", "rm-reference-label", `[${reference.label || index + 1}]`), el("p", "rm-reference-text", reference.text));
        const actions = el("div", "rm-actions");
        actions.append(evidenceButton(reference.evidence, `查看书目原文 · 第 ${reference.evidence?.page || "—"} 页`));
        article.append(actions);
        const occurrences = (reference.citation_ids || []).map((id) => citationMap.get(id)).filter(Boolean);
        if (!occurrences.length) article.append(el("p", "rm-muted", "当前尚未匹配到正文中的引用位置。"));
        occurrences.forEach((citation, occurrenceIndex) => {
          const details = el("details", "rm-citation-context");
          details.append(el("summary", "", `正文引用 ${occurrenceIndex + 1} · 第 ${citation.evidence.page} 页 · ${citation.marker}`));
          const context = el("div", "rm-context-body");
          context.append(el("p", "rm-muted", "展开后读取该引用所在文本块。"));
          details.append(context);
          let loaded = false;
          details.addEventListener("toggle", async () => {
            if (!details.open || loaded) return;
            loaded = true;
            try {
              const page = await readPage(citation.evidence.document_id, citation.evidence.page, citation.evidence.run_id);
              if (!alive || data !== structureData) return;
              const block = page.blocks?.find((item) => item.id === citation.evidence.block_id);
              context.replaceChildren(el("p", "rm-original-text", block?.text || citation.evidence.text), evidenceButton(citation.evidence, "定位这处正文引用 →"));
            } catch (error) {
              loaded = false;
              context.replaceChildren(el("p", "rm-warning", error.message || String(error)));
            }
          });
          article.append(details);
        });
        references.append(article);
      });
      layout.append(outline, references); root.append(layout);
      if (data.citations?.length) {
        const reverse = el("section", "rm-card rm-reverse");
        reverse.append(panelTitle("正文引用 → 书目", "选择引用标记，定位对应条目及其原文；也可直接打开引用所在正文。"));
        data.citations.forEach((citation) => {
          const row = el("div", "rm-reverse-row");
          row.append(el("span", "rm-muted", `第 ${citation.evidence.page} 页 ${citation.marker}`));
          (citation.reference_ids || []).forEach((id) => {
            const reference = referenceMap.get(id);
            if (!reference) return;
            row.append(btn(`书目 [${reference.label}] ↗`, () => {
              for (const article of referenceElements.values()) article.classList.remove("is-target");
              const article = referenceElements.get(id);
              article?.classList.add("is-target"); article?.scrollIntoView({ block: "center", behavior: "auto" }); article?.focus({ preventScroll: true });
              Promise.resolve().then(() => onEvidence?.(reference.evidence)).catch((error) => showNotice(error.message || String(error), true));
            }, "rm-small-button"));
          });
          if (citation.unresolved_labels?.length) row.append(el("span", "rm-warning", `未匹配：${citation.unresolved_labels.join("、")}`));
          row.append(evidenceButton(citation.evidence, "正文原位 →"));
          reverse.append(row);
        });
        root.append(reverse);
      }
      const limits = el("details", "rm-limits");
      limits.append(el("summary", "", "识别范围与当前限制"));
      (data.limitations || []).forEach((text) => limits.append(el("p", "rm-muted", text)));
      if (data.unavailable_fields?.length) limits.append(el("p", "rm-muted", "复杂公式、表格单元格、作者机构及作者年份型引用仍可能无法完整提取。"));
      root.append(limits);
      const textOutline = panes.text.querySelector(".rm-text-outline");
      if (textOutline) textOutline.replaceWith(createTextOutline());
    }

    function createTextOutline() {
      const outline = el("details", "rm-text-outline");
      outline.open = shell.clientWidth > 1080;
      outline.append(el("summary", "", "章节导航"));
      const nav = el("nav"); nav.setAttribute("aria-label", "跳转到章节文字");
      (structureData?.headings || []).forEach(heading => {
        const link = btn(heading.title, () => navigateTextEvidence(heading.evidence), "rm-outline-item");
        link.disabled = !heading.evidence?.page;
        link.dataset.blockId = heading.evidence?.block_id || heading.evidence?.id || "";
        link.style.setProperty("--heading-level", Math.min(3, Math.max(1, Number(heading.level) || 1)));
        nav.append(link);
      });
      if (!nav.children.length) nav.append(el("p", "rm-muted", "暂无可靠章节目录，可按页码核对文字。"));
      outline.append(nav);
      return outline;
    }
    async function navigateTextEvidence(evidence) {
      if (!evidence?.page || !selectedDocument) return;
      const documentId = selectedId;
      if (evidence.document_id && evidence.document_id !== documentId) return;
      switchTab("text", false);
      const navigation = ++textNavigationEpoch;
      const runId = evidence.run_id || (selectedDocument.available_run || selectedDocument.current_run)?.id;
      // Chapter links within the displayed page only change the reading target.
      // Re-fetching here rebuilt both the text and the chapter list on every click.
      const samePage = !pageLoading && currentPage?.page === evidence.page && (!runId || currentPage.run_id === runId);
      if (samePage) {
        panes.text.querySelector('.rm-page-number').value = evidence.page;
        pageReadError = "";
        updateSelectionHint();
      }
      const result = samePage ? currentPage : await loadPage(evidence.page, { runId, navigation });
      if (!result || !alive || suspended || navigation !== textNavigationEpoch || activeTab !== "text" || selectedId !== documentId) return;
      const id = evidence.block_id || evidence.id;
      let block = result.blocks?.find(item => item.id === id);
      if (!block && Array.isArray(evidence.bbox) && evidence.bbox.length === 4) {
        const matches = result.blocks?.filter(item => Array.isArray(item.bbox) && item.bbox.length === 4 && item.bbox.every((value, index) => Number.isFinite(value) && Math.abs(value - evidence.bbox[index]) < 0.0001)) || [];
        if (matches.length === 1) block = matches[0];
      }
      pagePreview?.setBlock(block?.id);
      if (block) {
        const text = textElements.get(block.id);
        text?.focus({ preventScroll: true });
        text?.closest(".rm-text-block")?.scrollIntoView({ block: "start", behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
      } else {
        const hint = panes.text.querySelector(".rm-selection-hint");
        hint.textContent = `已打开第 ${result.page} 页，未能可靠匹配文字块。请对照原页确认位置。`;
        hint.scrollIntoView({ block: "nearest", behavior: "auto" });
      }
    }

    function openSourcePreview(doc, result, block, trigger) {
      sourceDialog?.close();
      const originalScroll = { top: container.scrollTop, left: container.scrollLeft, overflow: container.style.overflow };
      const selected = window.getSelection();
      const savedRange = selected?.rangeCount && panes.text.contains(selected.getRangeAt(0).commonAncestorContainer) ? selected.getRangeAt(0).cloneRange() : null;
      const dialog = el("dialog", "rm-source-dialog");
      dialog.setAttribute("aria-label", "原页放大预览");
      const heading = el("div", "rm-source-heading");
      const words = el("div");
      words.append(el("h3", "", "原页放大预览"), el("p", "rm-muted", `${doc.title || doc.filename} · 第 ${result.page} 页`));
      const close = btn("关闭预览", () => dialog.close(), "rm-button"); close.autofocus = true;
      heading.append(words, close);
      const toolbar = el("div", "rm-source-toolbar");
      const zoom = el("select", "rm-select"); zoom.setAttribute("aria-label", "预览缩放");
      [["1", "适合宽度"], ["page", "适合整页"], ["1.5", "150%"], ["2", "200%"]].forEach(([value, title]) => zoom.append(option(value, title)));
      const openReader = btn("在阅读器中打开原文", () => {
        dialog.close();
        Promise.resolve().then(() => onEvidence?.({ document_id: doc.id, run_id: result.run_id, page: result.page, block_id: block?.id, bbox: block?.bbox, text: block?.text })).catch(error => showNotice(error.message || String(error), true));
      }, "rm-link-button");
      openReader.disabled = typeof onEvidence !== "function";
      toolbar.append(field("缩放", zoom), openReader);
      const status = el("p", "rm-source-status rm-muted", "正在加载原页大图…"); status.setAttribute("role", "status");
      const retry = btn("重试大图", () => loadImage(), "rm-small-button"); retry.hidden = true;
      const viewport = el("div", "rm-source-viewport");
      const paper = el("div", "rm-source-paper");
      const image = el("img", "rm-source-image"); image.alt = `第 ${result.page} 页原文大图`;
      const highlight = el("span", "rm-source-highlight"); highlight.hidden = true; highlight.setAttribute("aria-hidden", "true");
      const box = block?.bbox;
      const validBox = Array.isArray(box) && box.length === 4 && box.every(value => Number.isFinite(value) && value >= 0 && value <= 1) && box[2] > box[0] && box[3] > box[1];
      if (validBox) {
        highlight.style.left = `${box[0] * 100}%`; highlight.style.top = `${box[1] * 100}%`;
        highlight.style.width = `${(box[2] - box[0]) * 100}%`; highlight.style.height = `${(box[3] - box[1]) * 100}%`;
      }
      paper.append(image, highlight); viewport.append(paper);
      dialog.append(heading, toolbar, status, retry, viewport);
      let loaded = false, cleaned = false;
      function resize(center = false) {
        const ratio = image.naturalWidth && image.naturalHeight ? image.naturalWidth / image.naturalHeight : (result.width / result.height || 0.75);
        const available = Math.max(1, viewport.clientWidth - 24);
        const width = zoom.value === "page" ? Math.min(available, Math.max(1, viewport.clientHeight - 24) * ratio) : available * Number(zoom.value);
        paper.style.width = `${width}px`; paper.style.aspectRatio = String(ratio);
        if (center && loaded && validBox) {
          viewport.scrollTop = Math.max(0, 12 + (box[1] + box[3]) / 2 * width / ratio - viewport.clientHeight / 2);
          viewport.scrollLeft = Math.max(0, 12 + (box[0] + box[2]) / 2 * width - viewport.clientWidth / 2);
        }
      }
      image.addEventListener("load", () => {
        if (cleaned) return;
        loaded = true; image.hidden = false; highlight.hidden = !validBox; retry.hidden = true;
        status.textContent = validBox ? "色框为当前文本块；关闭预览后继续阅读原位置。" : "当前块没有可靠坐标，可核对整页原文。";
        resize(true);
      });
      image.addEventListener("error", () => {
        if (cleaned) return;
        loaded = false; image.hidden = true; highlight.hidden = true; retry.hidden = false;
        status.textContent = "原页大图加载失败，请重试，或关闭后继续阅读文字。";
      });
      function loadImage() {
        loaded = false; image.hidden = true; highlight.hidden = true; retry.hidden = true;
        status.textContent = "正在加载原页大图…";
        image.src = `${pathFor(doc.id)}/pages/${result.page}/image?scale=1.5`;
      }
      zoom.addEventListener("change", () => resize(true));
      const observer = new ResizeObserver(() => resize());
      const cleanup = () => {
        if (cleaned) return;
        cleaned = true; observer.disconnect();
        container.style.overflow = originalScroll.overflow;
        dialog.remove(); if (sourceDialog === dialog) sourceDialog = null;
        if (alive && !suspended && container.isConnected && !container.hidden) {
          trigger?.focus({ preventScroll: true });
          if (savedRange?.commonAncestorContainer.isConnected) {
            const current = window.getSelection(); current.removeAllRanges(); current.addRange(savedRange);
          }
          container.scrollTop = originalScroll.top; container.scrollLeft = originalScroll.left;
          selectionChanged();
        }
      };
      // Cleanup synchronously before a reader navigation can detach this module.
      const nativeClose = dialog.close.bind(dialog);
      dialog.close = () => { nativeClose(); cleanup(); };
      dialog.addEventListener("cancel", event => { event.preventDefault(); dialog.close(); });
      dialog.addEventListener("close", cleanup);
      sourceDialog = dialog; shell.append(dialog);
      container.style.overflow = "hidden";
      dialog.showModal(); observer.observe(viewport); resize(); loadImage();
    }

    function renderTextControls(doc) {
      const root = panes.text;
      pagePreview = null;
      root.classList.toggle("rm-preserve-lines", preserveLineBreaks);
      root.replaceChildren(panelTitle("对照原页，阅读与翻译", "正文自动换行。选中块内片段可单独翻译，点击“预览位置”可核对原页。"));
      const toolbar = el("form", "rm-page-form rm-text-toolbar");
      const pageNumber = el("input", "rm-input rm-page-number");
      pageNumber.type = "number"; pageNumber.min = 1; pageNumber.max = doc.page_count; pageNumber.value = 1; pageNumber.required = true;
      pageNumber.setAttribute("aria-label", "原文页码");
      const load = btn("读取本页文字"); load.type = "submit";
      const lineMode = el("label", "rm-line-mode");
      const preserve = el("input"); preserve.type = "checkbox"; preserve.checked = preserveLineBreaks;
      preserve.addEventListener("change", () => {
        preserveLineBreaks = preserve.checked;
        root.classList.toggle("rm-preserve-lines", preserveLineBreaks);
      });
      lineMode.append(preserve, el("span", "", "保留原文换行"));
      toolbar.append(field("页码", pageNumber), el("span", "rm-muted", `/ ${doc.page_count} 页`), load, lineMode);
      toolbar.addEventListener("submit", (event) => { event.preventDefault(); loadPage(Number(pageNumber.value)); });
      const hint = el("p", "rm-selection-hint", "尚未选择文字；每个文本块都可以单独翻译。");
      hint.setAttribute("aria-live", "polite");
      const body = el("div", "rm-page-blocks");
      empty(body, "选择页码读取文字", "解析完成的页面可显示原文、定位区域和翻译入口。");
      const layout = el("div", "rm-text-layout");
      layout.append(createTextOutline(), body);
      root.append(toolbar, hint, layout);
    }
    function createPagePreview(doc, result) {
      const preview = el("aside", "rm-page-preview");
      preview.setAttribute("aria-label", "原页位置预览");
      const title = el("h4", "", "原页位置");
      const caption = el("p", "rm-preview-caption", `第 ${result.page} 页`);
      caption.setAttribute("aria-live", "polite");
      const status = el("p", "rm-muted rm-preview-status", "正在加载原页缩略图…");
      status.setAttribute("role", "status");
      let activeBlock = null, loaded = false, failed = false;
      const imageButton = btn("", () => openSourcePreview(doc, result, activeBlock, imageButton), "rm-preview-page");
      imageButton.addEventListener("mousedown", event => event.preventDefault());
      imageButton.setAttribute("aria-label", "放大查看原页");
      imageButton.title = "在当前页放大核对原文";
      const image = el("img", "rm-preview-image");
      image.alt = `第 ${result.page} 页原文缩略图`;
      image.decoding = "async";
      if (Number.isFinite(result.width) && result.width > 0 && Number.isFinite(result.height) && result.height > 0) {
        image.width = Math.round(result.width); image.height = Math.round(result.height);
        imageButton.style.aspectRatio = `${result.width} / ${result.height}`;
      }
      const highlight = el("span", "rm-preview-highlight");
      highlight.hidden = true; highlight.setAttribute("aria-hidden", "true");
      imageButton.append(image, highlight);
      const retry = btn("重试预览", () => loadImage(), "rm-small-button"); retry.hidden = true;
      const info = el("div", "rm-preview-info");
      info.append(title, caption, status, retry);
      preview.append(info, imageButton);
      function renderHighlight() {
        const box = activeBlock?.bbox;
        const valid = Array.isArray(box) && box.length === 4 && box.every(value => Number.isFinite(value) && value >= 0 && value <= 1) && box[2] > box[0] && box[3] > box[1];
        highlight.hidden = !loaded || !valid;
        if (valid) {
          highlight.style.left = `${box[0] * 100}%`; highlight.style.top = `${box[1] * 100}%`;
          highlight.style.width = `${(box[2] - box[0]) * 100}%`; highlight.style.height = `${(box[3] - box[1]) * 100}%`;
        }
        status.textContent = failed ? "缩略图加载失败，可重试或使用“定位原文”。" : !loaded ? "正在加载原页缩略图…" : !activeBlock ? "本页暂无可定位文本块。" : valid ? "点击小图就地放大，关闭后继续读这段文字。" : "此文本块缺少可靠坐标，请放大原页核对。";
      }
      image.addEventListener("load", () => {
        loaded = true; failed = false; retry.hidden = true; image.hidden = false;
        imageButton.style.aspectRatio = `${image.naturalWidth} / ${image.naturalHeight}`;
        renderHighlight();
      });
      image.addEventListener("error", () => {
        loaded = false; failed = true; retry.hidden = false; image.hidden = true; imageButton.hidden = true; renderHighlight();
      });
      function loadImage() {
        loaded = false; failed = false; retry.hidden = true; image.hidden = false; imageButton.hidden = false; renderHighlight();
        image.src = `${pathFor(doc.id)}/pages/${result.page}/image?scale=0.5`;
      }
      loadImage();
      return {
        element: preview,
        setBlock(id, reveal = false) {
          const index = result.blocks?.findIndex(block => block.id === id) ?? -1;
          activeBlock = index >= 0 ? result.blocks[index] : null;
          caption.textContent = activeBlock ? `第 ${result.page} 页 · 文本块 ${index + 1}` : `第 ${result.page} 页`;
          for (const [blockId, text] of textElements) {
            const article = text.closest(".rm-text-block");
            article.classList.toggle("is-previewed", blockId === id);
            article.querySelector(".rm-preview-block")?.setAttribute("aria-pressed", String(blockId === id));
          }
          // Reading a paragraph under a heading keeps that chapter selected;
          // otherwise each body click would remove the navigation marker.
          let chapterId = null;
          if (index >= 0) for (const heading of structureData?.headings || []) {
            const source = heading.evidence;
            if (!source?.page || source.page > result.page) continue;
            const headingId = source.block_id || source.id;
            const position = result.blocks.findIndex(block => block.id === headingId);
            if (source.page < result.page || (position >= 0 && position <= index)) chapterId = headingId;
          }
          for (const link of panes.text.querySelectorAll(".rm-text-outline .rm-outline-item")) {
            if (chapterId && link.dataset.blockId === chapterId) link.setAttribute("aria-current", "location");
            else link.removeAttribute("aria-current");
          }
          renderHighlight();
          if (reveal) preview.scrollIntoView({ block: "nearest", behavior: "auto" });
        },
      };
    }
    async function readPage(documentId, page, runId) {
      const key = `${documentId}:${runId || "default"}:${page}`;
      if (pendingPages.has(key)) return pendingPages.get(key);
      if (!pageCache.has(key)) {
        const query = runId ? `?run_id=${encodeURIComponent(runId)}` : "";
        const request = api(`${pathFor(documentId)}/pages/${page}${query}`).catch((error) => {
          if (pageCache.get(key) === request) pageCache.delete(key);
          throw error;
        }).finally(() => { if (pendingPages.get(key) === request) pendingPages.delete(key); });
        pageCache.set(key, request);
        pendingPages.set(key, request);
      }
      return pageCache.get(key);
    }
    async function loadPage(page, pageOptions = {}) {
      sourceDialog?.close();
      const doc = selectedDocument;
      if (!doc) return;
      if (!Number.isInteger(page) || page < 1 || page > doc.page_count) { showNotice(`请输入1至${doc.page_count}之间的页码。`, true); return; }
      if (!pageOptions.navigation) textNavigationEpoch++;
      const epoch = ++pageEpoch;
      const body = panes.text.querySelector(".rm-page-blocks");
      const input = panes.text.querySelector(".rm-page-number");
      const hint = panes.text.querySelector(".rm-selection-hint");
      if (!pageLoading) {
        const selected = window.getSelection();
        pageReadSelection = selected?.rangeCount && body.contains(selected.anchorNode) && body.contains(selected.focusNode)
          ? {anchor:selected.anchorNode, anchorOffset:selected.anchorOffset, focus:selected.focusNode, focusOffset:selected.focusOffset} : null;
      }
      pageLoading = true; pageReadError = "";
      let failed = false;
      const visibleHere = () => alive && !suspended && activeTab === "text" && body.isConnected && container.contains(shell) && !container.hidden;
      const preserveScroll = operation => {
        const restore = visibleHere(), top = container.scrollTop, left = container.scrollLeft;
        operation();
        if (restore) { container.scrollTop = top; container.scrollLeft = left; }
      };
      if (input) input.value = page;
      // Keep the previous page's height while loading. Clearing it here would
      // force the browser to clamp the module scroll position to the top.
      preserveScroll(() => {
        body.setAttribute("aria-busy", "true"); body.inert = true;
        hint.classList.remove("is-error");
        hint.textContent = `正在读取第 ${page} 页…${currentPage ? ` 暂时保留第 ${currentPage.page} 页内容。` : ""}`;
      });
      const runId = pageOptions.runId || (doc.available_run || doc.current_run)?.id;
      try {
        // Explicit rereads see parser progress; repeated clicks share an
        // already pending request instead of launching duplicate reads.
        const key = `${doc.id}:${runId || "default"}:${page}`;
        if (!pendingPages.has(key)) pageCache.delete(key);
        const result = await readPage(doc.id, page, runId);
        if (!alive || epoch !== pageEpoch || doc.id !== selectedId) return;
        const fragment = document.createDocumentFragment();
        const nextTextElements = new Map(), nextTranslateButtons = new Map();
        const list = el("div", "rm-text-list");
        const nextPreview = createPagePreview(doc, result);
        if (result.notice) fragment.append(el("p", "rm-warning", result.notice));
        if (result.error) fragment.append(el("p", "rm-warning", result.error));
        if (!result.blocks?.length) {
          const message = result.status === "pending" ? "这一页尚未处理完成，请稍后刷新文献。" : "这一页没有可提取文字。请先检查解析或OCR结果。";
          list.append(el("p", "rm-empty-inline", message));
        }
        (result.blocks || []).forEach((block, index) => {
          const article = el("article", "rm-text-block");
          const heading = el("div", "rm-text-block-heading");
          const evidence = { document_id: doc.id, run_id: result.run_id, block_id: block.id, page: result.page, bbox: block.bbox, text: block.text };
          const actions = el("div", "rm-block-actions");
          const preview = btn("预览位置", () => pagePreview?.setBlock(block.id, true), "rm-small-button rm-preview-block");
          preview.setAttribute("aria-pressed", "false");
          preview.addEventListener("mousedown", (event) => event.preventDefault());
          heading.append(el("span", "rm-field-title", `第 ${result.page} 页 · 文本块 ${index + 1}`), actions);
          const text = el("div", "rm-block-text", block.text);
          text.tabIndex = 0; text.setAttribute("aria-label", `可选择的原文文本块 ${index + 1}`);
          text.addEventListener("focus", () => pagePreview?.setBlock(block.id));
          text.addEventListener("click", () => pagePreview?.setBlock(block.id));
          nextTextElements.set(block.id, text);
          const translate = btn("翻译整块", () => act(translate, async () => {
            if (typeof onTranslate !== "function") throw new Error("当前未连接翻译任务入口。");
            if (invalidSelection) throw new Error("请只在同一个文本块内选择要翻译的文字。");
            if (selection && selection.blockId !== block.id) throw new Error("选中文字来自另一文本块，请点击对应文本块的翻译按钮，或取消选择。");
            const payload = { document_id: doc.id, run_id: result.run_id, block_ids: [block.id] };
            if (selection?.blockId === block.id) {
              if (!block.text.includes(selection.text)) throw new Error("选中文字与原文不一致，请重新选择。");
              payload.selected_text = selection.text;
            }
            await onTranslate(payload);
            if (!suspended) showNotice(payload.selected_text ? "已发送所选文字的翻译请求，可在任务区查看结果。" : "已发送整块翻译请求，可在任务区查看结果。");
          }), "rm-button rm-primary");
          translate.addEventListener("mousedown", (event) => event.preventDefault());
          translate.disabled = typeof onTranslate !== "function";
          nextTranslateButtons.set(block.id, translate);
          actions.append(preview, evidenceButton(evidence, "定位原文"), translate);
          article.append(heading, text); list.append(article);
        });
        fragment.append(list, nextPreview.element);
        preserveScroll(() => {
          const selected = window.getSelection();
          if (selected?.rangeCount && body.contains(selected.getRangeAt(0).commonAncestorContainer)) selected.removeAllRanges();
          currentPage = result; pagePreview = nextPreview;
          selection = null; invalidSelection = false;
          textElements.clear(); translateButtons.clear();
          for (const [id, text] of nextTextElements) textElements.set(id, text);
          for (const [id, button] of nextTranslateButtons) translateButtons.set(id, button);
          body.replaceChildren(fragment);
          pagePreview.setBlock(result.blocks?.[0]?.id);
          updateSelectionHint();
        });
        return result;
      } catch (error) {
        if (alive && epoch === pageEpoch && doc.id === selectedId) preserveScroll(() => {
          failed = true;
          pageReadError = `第 ${page} 页读取失败${currentPage ? `，仍显示第 ${currentPage.page} 页` : ""}。${error.message || String(error)} 请点击“读取本页文字”重试。`;
          hint.classList.add("is-error");
          hint.textContent = pageReadError;
        });
      } finally {
        if (alive && epoch === pageEpoch) {
          pageLoading = false; body.inert = false; body.setAttribute("aria-busy", "false");
          const selected = window.getSelection();
          if (failed && pageReadSelection && visibleHere() && !selected?.toString()) {
            const {anchor, anchorOffset, focus, focusOffset} = pageReadSelection;
            if (body.contains(anchor) && body.contains(focus)) selected.setBaseAndExtent(anchor, anchorOffset, focus, focusOffset);
          }
          pageReadSelection = null;
          if (failed && visibleHere()) selectionChanged();
        }
      }
    }
    function updateSelectionHint() {
      const hint = panes.text.querySelector(".rm-selection-hint");
      if (!hint) return;
      hint.classList.toggle("is-error", invalidSelection || Boolean(pageReadError));
      hint.textContent = invalidSelection ? "选区跨越了文本块，请在同一个块内重新选择。" : pageReadError || (selection ? `已选择 ${selection.text.length} 个字符，将翻译对应文本块内的选定片段。` : "尚未选择文字；每个文本块都可以单独翻译。");
      for (const [id, button] of translateButtons) button.textContent = selection?.blockId === id ? "翻译所选文字" : "翻译整块";
    }
    function selectionChanged() {
      if (!alive || suspended || pageLoading || sourceDialog || activeTab !== "text") return;
      const selected = window.getSelection();
      // Keep the exact DOM substring: Selection.toString() collapses PDF line
      // breaks in normal white-space mode, but the backend checks source text.
      const value = selected?.rangeCount ? selected.getRangeAt(0).toString().trim() : "";
      selection = null; invalidSelection = false;
      if (value && selected?.rangeCount) {
        let startBlock = null, endBlock = null;
        for (const [id, element] of textElements) {
          if (element.contains(selected.anchorNode)) startBlock = id;
          if (element.contains(selected.focusNode)) endBlock = id;
        }
        if (startBlock || endBlock) {
          if (!startBlock || startBlock !== endBlock) invalidSelection = true;
          else {
            const original = currentPage?.blocks?.find((block) => block.id === startBlock);
            if (original?.text.includes(value)) {
              selection = { blockId: startBlock, text: value };
              pagePreview?.setBlock(startBlock);
            }
            else invalidSelection = true;
          }
        }
      }
      updateSelectionHint();
    }
    document.addEventListener("selectionchange", selectionChanged);
    switchTab("metadata");
    const controller = {
      ready: null,
      refresh,
      suspend() { sourceDialog?.close(); suspended = true; textNavigationEpoch++; },
      resume() { suspended = false; },
      destroy() {
        if (!alive) return;
        sourceDialog?.close();
        alive = false; refreshEpoch++; documentEpoch++; pageEpoch++; diffEpoch++;
        options.invalidateEvidenceNavigation?.();
        clearExtensions();
        document.removeEventListener("selectionchange", selectionChanged);
        pageCache.clear(); pendingPages.clear(); shell.remove();
        if (instances.get(container) === controller) instances.delete(container);
      },
    };
    instances.set(container, controller);
    controller.ready = refresh();
    return controller;
  }

  window.ResearchManage = { mount };
})();
