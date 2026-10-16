"use strict";

(() => {
  const instances = new WeakMap();
  const node = (tag, className = "", text) => {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = String(text);
    return element;
  };
  const button = (text, action, className = "rm-button") => {
    const element = node("button", className, text);
    element.type = "button";
    if (action) element.addEventListener("click", action);
    return element;
  };
  const matchNames = { identifier_match: "标识相符 · 待确认目标版本", title_candidate: "标题候选 · 身份未确认", unconfirmed: "检索返回 · 身份未确认", identifier_conflict: "标识冲突 · 不可按同一论文处理" };
  const statusNames = { completed: "已返回候选", not_found: "本次未找到可核对记录", failed: "本次检索失败", skipped_budget: "未检索 · 预算已用完", skipped_no_query: "未检索 · 无可靠标识或标题" };
  function officialLink(paper) {
    const identifier = paper?.arxiv_id;
    if (typeof identifier !== "string" || !/^(?:\d{4}\.\d{4,5}|[a-z][a-z.-]*\/\d{7})(?:v[1-9]\d*)?$/i.test(identifier)
        || paper.id !== `arxiv:${identifier}` || paper.source !== "arxiv"
        || ![`https://arxiv.org/abs/${identifier}`, `http://arxiv.org/abs/${identifier}`].includes(paper.source_url)) return null;
    const link = node("a", "rm-link-button", "核对 arXiv 官方论文记录 ↗");
    link.href = paper.source_url; link.target = "_blank"; link.rel = "noopener noreferrer";
    return link;
  }

  function mount(container, options = {}) {
    if (!container || typeof options.api !== "function") throw new Error("参考书目组件需要容器和api方法。");
    instances.get(container)?.destroy();
    const doc = options.document;
    const documentId = typeof doc === "string" ? doc : doc?.id;
    const runId = doc?.run_id || doc?.available_run?.id || doc?.current_run?.id || null;
    const endpoint = `/api/documents/${encodeURIComponent(documentId || "")}/references`;
    const abort = new AbortController();
    let alive = true, rankEpoch = 0, searchEpoch = 0, ranking = false, searching = false;
    let ranked = null;
    const selected = new Set();
    const shell = node("section", "rr-shell rm-form");
    const heading = node("div", "rm-section-heading");
    heading.append(node("h3", "", "参考书目：按问题筛选，沿出处核对"),
      node("p", "rm-muted", "相关词、正文引用次数及方法与实验附近的词语线索用于排序；分数不代表论文的学术价值。暂筛出的书目仍保留。"));
    const notice = node("p", "rm-notice"); notice.hidden = true; notice.setAttribute("role", "status");
    const form = node("form", "rm-card rm-form");
    const query = node("input", "rm-input"); query.type = "search"; query.maxLength = 500;
    query.setAttribute("aria-label", "参考书目筛选词"); query.placeholder = "输入关注关键词；留空查看全部书目";
    const queryLabel = node("label", "rm-field"); queryLabel.append(node("span", "rm-field-title", "本地筛选关键词"), query);
    const filterLabel = node("label", "rm-muted");
    const includeFiltered = node("input"); includeFiltered.type = "checkbox";
    filterLabel.append(includeFiltered, node("span", "", " 显示暂筛出的书目（恢复到当前视图）"));
    const filterButton = button("应用本地筛选"); filterButton.type = "submit";
    const summary = node("p", "rm-muted", "正在读取原参考书目……");
    form.append(queryLabel, filterLabel, filterButton, summary);
    const actions = node("div", "rm-card rm-form");
    const selectionText = node("p", "rm-muted", "已选 0 / 5 条书目");
    const searchButton = button("检索选中书目（最多5条）", () => search(), "rm-button rm-primary");
    const clear = button("清空选择", () => { selected.clear(); renderRows(); controls(); });
    const actionRow = node("div", "rm-actions"); actionRow.append(searchButton, clear);
    actions.append(selectionText, node("p", "rm-muted", "点击检索后，只将所选书目的标识或标题片段发送给 arXiv。正文引用上下文不会发送，候选不会自动下载。"), actionRow);
    const list = node("div", "rr-list rm-form"); list.setAttribute("aria-label", "参考书目筛选结果");
    const results = node("section", "rr-search-results rm-form"); results.hidden = true;
    shell.append(heading, notice, form, actions, list, results); container.replaceChildren(shell);

    function message(text, error = false) {
      if (!alive) return;
      window.ResearchNotices.show(text, error, notice);
    }
    function controls() {
      if (!alive) return;
      searchButton.disabled = searching || ranking || !selected.size;
      searchButton.textContent = searching ? "正在逐条查询公开书目……" : "检索选中书目（最多5条）";
      clear.disabled = searching || !selected.size;
      filterButton.disabled = searching || ranking;
      includeFiltered.disabled = searching || ranking;
      const all = [...(ranked?.included || []), ...(ranked?.filtered || [])];
      const labels = [...selected].map(id => all.find(item => item.reference_id === id)?.label || id);
      selectionText.textContent = `已选 ${selected.size} / 5 条书目${labels.length ? ` · ${labels.map(label => `[${label}]`).join("、")}` : ""}`;
      list.querySelectorAll('input[type="checkbox"]').forEach(input => { input.checked = selected.has(input.value); input.disabled = searching; });
    }
    function evidenceLink(evidence, label) {
      const control = button(label, () => {
        if (!alive) return;
        Promise.resolve().then(() => options.onEvidence?.({ ...evidence, document_id: documentId, run_id: runId || evidence.run_id }))
          .catch(error => message(error.message || "无法定位原文。", true));
      }, "rm-link-button");
      control.disabled = typeof options.onEvidence !== "function" || !evidence?.page;
      return control;
    }
    function renderRows() {
      if (!alive || !ranked) return;
      list.replaceChildren();
      summary.textContent = `原书目 ${ranked.total_references} 条 · 保留 ${ranked.included?.length || 0} 条 · 暂筛出 ${ranked.filtered?.length || 0} 条；所有原始条目均保留。`;
      const rows = Array.isArray(ranked.items) ? ranked.items : ranked.include_filtered ? [...(ranked.included || []), ...(ranked.filtered || [])] : ranked.included || [];
      if (!rows.length) list.append(node("p", "rm-empty", ranked.total_references ? "当前关键词未命中书目。可修改查询或勾选显示暂筛出的书目。" : "尚未识别到可用参考书目；请核对该版本的解析结果。"));
      for (const row of rows) {
        const article = node("article", "rr-reference rm-card rm-form");
        article.dataset.referenceId = row.reference_id;
        const label = node("label", "rm-muted");
        const choose = node("input"); choose.type = "checkbox"; choose.value = row.reference_id;
        choose.setAttribute("aria-label", `选择书目 ${row.label}`); choose.checked = selected.has(row.reference_id);
        choose.addEventListener("change", () => {
          if (!alive || searching) return;
          if (choose.checked && !selected.has(row.reference_id) && selected.size >= 5) { choose.checked = false; message("一次最多选择5条书目，请取消一条后再选。", true); return; }
          if (choose.checked) selected.add(row.reference_id); else selected.delete(row.reference_id);
          controls();
        });
        label.append(choose, node("span", "", ` 书目 [${row.label}] · 本查询线索 ${row.score} 分`));
        article.append(label, node("span", "rm-badge", row.included ? "保留展示" : "暂筛出 · 已恢复显示"),
          node("p", "rm-reference-text", row.text || row.reference?.text || ""));
        const referenceActions = node("div", "rm-actions");
        referenceActions.append(evidenceLink(row.reference?.evidence, "定位书目原文 →"));
        for (const continuation of row.reference?.continuation_evidence || []) referenceActions.append(evidenceLink(continuation, `书目续行 · 第 ${continuation.page} 页 →`));
        article.append(referenceActions);
        const details = node("details", "rm-limits"); details.append(node("summary", "", `查看排序理由与 ${row.contexts?.length || 0} 处正文引用`));
        const reasons = node("ul", "rm-muted");
        for (const reason of row.reasons || []) reasons.append(node("li", "", `${reason.detail || ""}${reason.points !== undefined ? ` 本项 ${reason.points} 分。` : ""}${reason.matched_terms?.length ? ` 命中：${reason.matched_terms.join("、")}。` : ""}`));
        details.append(reasons);
        for (const context of row.contexts || []) {
          const item = node("div", "rm-citation-context rm-context-body");
          item.append(node("p", "rm-muted", `引用 ${context.marker || ""} · 第 ${context.citation_evidence?.page || "—"} 页`),
            node("p", "rm-original-text", context.context_evidence?.text || context.reason || "上下文暂不可用。"),
            evidenceLink(context.citation_evidence, "定位这处正文引用 →"));
          if (context.method_cues?.length) item.append(node("p", "rm-muted", `附近词语线索：${context.method_cues.join("、")}；不代表引用质量。`));
          details.append(item);
        }
        article.append(details); list.append(article);
      }
      controls();
    }
    async function rank() {
      const epoch = ++rankEpoch;
      ranking = true; controls();
      const params = new URLSearchParams({ q: query.value.trim(), include_filtered: String(includeFiltered.checked) });
      if (runId) params.set("run_id", runId);
      try {
        if (!documentId) throw new Error("请先选择一篇已导入文献。");
        const value = await options.api(`${endpoint}/ranked?${params}`, { signal: abort.signal });
        if (!alive || epoch !== rankEpoch) return;
        ranked = value;
        const valid = new Set([...(value.included || []), ...(value.filtered || [])].map(item => item.reference_id));
        for (const id of selected) if (!valid.has(id)) selected.delete(id);
        renderRows(); message("");
      } catch (error) { if (alive && epoch === rankEpoch) message(error.message || "参考书目暂不可用。", true); }
      finally { if (alive && epoch === rankEpoch) { ranking = false; controls(); } }
    }
    function renderSearch(value) {
      results.replaceChildren(node("h3", "", "所选书目的公开检索结果"),
        node("p", "rm-muted", `本次使用 ${value.requests_used ?? "—"} / ${value.max_searches ?? 5} 次查询预算，含缓存及失败请求。标题候选需要人工核对；不会自动下载。`));
      for (const row of value.results || []) {
        const item = node("article", "rr-search-result rm-card rm-form");
        item.append(node("h4", "", `书目 [${row.reference?.label || row.reference_id}] · ${statusNames[row.status] || row.status}`),
          node("p", "rm-reference-text", row.reference?.text || ""), node("p", "rm-muted", `发送查询：${row.query || "未发送"}`));
        if (row.attempted_at) item.append(node("p", "rm-muted", `本次尝试时间：${row.attempted_at}${row.reused_query ? " · 复用本轮同一查询" : ""}`));
        if (row.retrieved_at) item.append(node("p", "rm-muted", `来源：${row.source || "未记录"} · 原检索时间：${row.retrieved_at} · ${row.cached === true ? "使用缓存" : row.cached === false ? "非缓存返回" : "缓存状态未记录"}`));
        if (row.error?.message || row.reason) item.append(node("p", "rm-warning", row.error?.message || row.reason));
        if (row.ignored_invalid_metadata) item.append(node("p", "rm-warning", `${row.ignored_invalid_metadata} 条返回记录未通过官方元数据校验，未作为可确认候选展示。`));
        for (const candidate of row.candidates || []) {
          const paper = candidate.paper || {};
          const card = node("div", "rr-candidate rm-candidate");
          card.append(node("span", "rm-badge", matchNames[candidate.match_status] || "身份待核对"),
            node("h4", "", paper.title || "未命名公开记录"), node("p", "rm-muted", `${(paper.authors || []).join("、") || "作者未记录"} · 发表时间：${paper.published || "未记录"}`));
          for (const reason of candidate.reasons || []) card.append(node("p", "rm-muted", reason.detail || ""));
          if (candidate.arxiv_version_match === "different") card.append(node("p", "rm-warning", "arXiv编号相同，版本号不同；不能替代当前原文证据。"));
          const link = officialLink(paper);
          card.append(link || node("p", "rm-warning", "官方链接未通过校验，仅展示返回文字。"));
          item.append(card);
        }
        results.append(item);
      }
      results.hidden = false;
    }
    async function search() {
      if (!alive || searching || ranking || !selected.size) return;
      const epoch = ++searchEpoch;
      const referenceIds = [...selected];
      if (referenceIds.length > 5) { message("一次最多选择5条书目。", true); return; }
      searching = true; controls(); message("正在按所选书目逐条查询 arXiv；网络请求沿用共享间隔与缓存。");
      try {
        const body = { reference_ids: referenceIds, max_searches: 5 };
        if (runId) body.run_id = runId;
        const result = await options.api(`${endpoint}/search`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal: abort.signal });
        if (!alive || epoch !== searchEpoch) return;
        renderSearch(result); message("检索已结束，请按每条状态核对结果。未自动下载任何候选。");
      } catch (error) { if (alive && epoch === searchEpoch) message(error.message || "公开检索未完成，请稍后重试。", true); }
      finally { if (alive && epoch === searchEpoch) { searching = false; controls(); } }
    }
    form.addEventListener("submit", event => { event.preventDefault(); if (!searching) rank(); });
    includeFiltered.addEventListener("change", () => { if (!searching) rank(); });
    const controller = { ready: null, destroy() {
      if (!alive) return;
      alive = false; rankEpoch++; searchEpoch++; abort.abort(); shell.remove();
      if (instances.get(container) === controller) instances.delete(container);
    } };
    instances.set(container, controller); controller.ready = rank();
    return controller;
  }
  window.ResearchReferences = Object.freeze({ mount });
})();
