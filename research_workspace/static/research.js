"use strict";

/* Research tasks and deterministic data tools. All content comes from real APIs. */
(() => {
  const node = (tag, className, text) => {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = String(text);
    return element;
  };
  const button = (text, primary = false) => {
    const result = node("button", `rs-button${primary ? " rs-primary" : ""}`, text);
    result.type = "button";
    return result;
  };
  function field(text, control) {
    const label = node("label", "rs-field");
    label.append(node("span", "rs-label", text), control);
    return label;
  }
  function select(items) {
    const result = node("select", "rs-input");
    for (const [value, label] of items) {
      const option = node("option", "", label);
      option.value = value;
      result.append(option);
    }
    return result;
  }
  function input(type = "text", placeholder = "") {
    const result = node("input", "rs-input");
    result.type = type;
    result.placeholder = placeholder;
    return result;
  }
  function textarea(placeholder = "", rows = 5) {
    const result = node("textarea", "rs-input rs-textarea");
    result.rows = rows;
    result.placeholder = placeholder;
    return result;
  }
  function link(text, href, filename) {
    const result = node("a", "rs-link", text);
    result.href = href;
    if (filename) result.download = filename;
    return result;
  }
  const jsonOptions = (body) => ({ method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const pending = (job) => job && ["queued", "running"].includes(job.status);
  const kindLabels = { qa: "文献问答", compare: "多篇比较", translate: "文献翻译", translate_text: "学术文本翻译", review: "专题综述", future_work: "未来方向", future_filter: "全文方向筛选", draft: "论文框架" };
  const statusLabels = { queued: "排队中", running: "执行中", completed: "已完成", failed: "失败", cancelled: "已取消", interrupted: "调用中断，结果未知" };
  function dateLabel(value) {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? "" : new Intl.DateTimeFormat("zh-CN", {
      timeZone: "Asia/Shanghai", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
    }).format(date);
  }

  const futureStatusLabels = { completed: "本次有限核验已完成", partial: "部分方向已形成判定", evidence_insufficient: "证据不足" };
  const directionStatusLabels = { potentially_addressed: "摘要可能已覆盖", not_found_in_search: "本次检索未发现明确覆盖", evidence_insufficient: "证据不足" };
  const queryStatusLabels = { completed: "检索完成", failed: "检索失败", not_attempted: "未执行", pending: "尚未执行" };
  function safeArxivUrl(sourceId, rawUrl) {
    if (typeof sourceId !== "string" || typeof rawUrl !== "string") return null;
    const match = /^arxiv:((?:\d{4}\.\d{4,5}|[a-z][a-z.-]*\/\d{7})(?:v[1-9]\d*)?)$/.exec(sourceId);
    if (!match) return null;
    return rawUrl === `https://arxiv.org/abs/${match[1]}` || rawUrl === `http://arxiv.org/abs/${match[1]}` ? rawUrl : null;
  }
  function futureDirections(output) {
    const all = Array.isArray(output.future_verification?.directions) ? output.future_verification.directions : [];
    if (output.future_fulltext_pending) return { retained: all.map(d => ({...d, excluded:false})), excluded: [] };
    return {
      retained: Array.isArray(output.retained_directions) ? output.retained_directions : all.filter((direction) => !direction.excluded),
      excluded: Array.isArray(output.excluded_directions) ? output.excluded_directions : all.filter((direction) => direction.excluded),
    };
  }
  function futureSectionIndex(direction) {
    if (Number.isInteger(direction.section_index) && direction.section_index >= 0) return direction.section_index;
    const match = /^direction-([1-9]\d*)$/.exec(direction.direction_id || "");
    return match ? Number(match[1]) - 1 : -1;
  }
  function publicSourceLink(source) {
    const url = safeArxivUrl(source?.source_id, source?.url);
    if (!url) return node("span", "rs-muted", `${source?.source_id || "未知来源"} · 官方论文链接未通过校验`);
    const result = link(`${source.source_id} · ${source.title || "官方论文页面"}`, url);
    result.target = "_blank"; result.rel = "noopener noreferrer";
    return result;
  }
  function renderFutureVerification(container, output) {
    const audit = output.future_verification;
    if (!audit || typeof audit !== "object") {
      container.append(node("p", "rs-result-note", "候选方向已保存，公开摘要核验尚未完成；以下研究设想不能据此认定为未解决方向。"));
      return;
    }
    const panel = node("section", "rs-future-verification");
    panel.append(node("h4", "", "公开研究摘要核验"),
      node("p", "rs-future-status", `${futureStatusLabels[audit.status] || "核验状态未知"}（${audit.status || "unknown"}）`));
    if (audit.notice) panel.append(node("p", "rs-result-note", audit.notice));
    if(audit.date_filter)panel.append(node('p','rs-muted',`提交时间范围：${audit.date_filter.from} 至 ${audit.date_filter.to}；${audit.live_refresh_requested?'本次强制实时检索':'允许使用明确标记的缓存'}。`));
    panel.append(node("p", "rs-muted", "仅对本次返回的公开摘要进行有限判定；暂移出的方向仍需全文确认，保留方向也不构成研究空白或新颖性证明。"));
    const sources = new Map((Array.isArray(audit.sources) ? audit.sources : []).map((source) => [source.source_id, source]));
    const queries = node("section", "rs-future-queries"); queries.append(node("h5", "", "实际查询与检索记录"));
    if (!audit.queries?.length) queries.append(node("p", "rs-muted", "本次没有可展示的外部查询记录。"));
    for (const query of Array.isArray(audit.queries) ? audit.queries : []) {
      const item = node("article", "rs-future-query");
      item.append(node("p", "rs-small", `${query.query_id || "查询"} · ${queryStatusLabels[query.status] || query.status || "状态未知"}`),
        node("p", "rs-prose rs-query-text", query.query || "未记录查询词"),
        node("p", "rs-muted rs-small", `检索记录时间：${query.retrieved_at || "未取得返回记录"} · ${query.cached === true ? "使用缓存" : query.cached === false ? "非缓存返回" : "缓存状态未记录"}`));
      if (query.attempted_at) item.append(node("p", "rs-muted rs-small", `本次尝试时间：${query.attempted_at}`));
      if(query.receipt)item.append(node('p','rs-muted rs-small',`真实检索回执：${query.receipt.id} · SHA-256 ${query.receipt.sha256}`));
      item.append(node("p", "rs-muted rs-small", `字段：${query.field || "未记录"} · 排序：${query.sort_by || "未记录"} · 返回上限：${query.limit ?? "未记录"}`));
      if (query.strategy === "topic_only") item.append(node("p", "rs-muted rs-small", "本条只使用文献题名中的主题词，尚未覆盖方向的全部表达。"));
      if (query.reason || query.error) item.append(node("p", "rs-muted rs-small", query.reason || "本次查询失败，未形成可靠证据。"));
      const sourceList = node("ul", "rs-future-source-list");
      for (const sourceId of Array.isArray(query.source_ids) ? query.source_ids : []) {
        const source = sources.get(sourceId), row = node("li", "");
        row.append(source ? publicSourceLink(source) : node("span", "rs-muted", `${sourceId} · 来源记录缺失`)); sourceList.append(row);
      }
      if (sourceList.childElementCount) item.append(sourceList);
      queries.append(item);
    }
    panel.append(queries);
    const historical = Boolean(output.future_fulltext || output.future_fulltext_pending);
    const groups = historical ? {retained: (audit.directions || []).filter(d => !d.excluded),
      excluded: (audit.directions || []).filter(d => d.excluded)} : futureDirections(output);
    if (historical) panel.append(node("p", "rs-muted", output.future_direction_filter ? "以下为摘要初筛历史；当前保留与筛选结果以全文条件覆盖审核为准。" : "以下保留摘要初筛历史；正文核验阶段保留全部原始设想，未自动排除方向。"));
    for (const [key, title] of [["retained", "保留为待验证方向"], ["excluded", "暂移出重点候选（需全文确认）"]]) {
      const group = node("section", "rs-future-directions"); group.append(node("h5", "", `${historical ? (key === 'excluded' ? '摘要历史：曾提示可能覆盖（非当前排除结果）' : '摘要历史：未确认覆盖') : title} · ${groups[key].length}`));
      if (!groups[key].length) group.append(node("p", "rs-muted", key === "retained" ? "本次没有列入此组的方向；这不代表其余方向已被证明解决。" : "本次没有暂移出的方向。"));
      for (const direction of groups[key]) {
        const item = node("article", `rs-future-direction${key === "excluded" ? " is-excluded" : ""}`);
        item.append(node("h6", "", direction.heading || direction.direction_id || "研究方向"),
          node("p", "rs-muted rs-small", `${direction.direction_id || ""} · ${directionStatusLabels[direction.status] || direction.status || "状态未知"}`),
          node("p", "rs-prose", direction.reason || "未返回判定说明。"));
        if (direction.proposed_work) item.append(node("p", "rs-muted rs-future-proposal", "原始设想：" + direction.proposed_work));
        if (key === "excluded") item.append(node("p", "rs-future-excluded-note", "暂移出重点候选，需阅读全文确认；不等于已解决。"));
        if (!direction.citations?.length) item.append(node("p", "rs-muted rs-small", "本方向没有可展示的公开摘要引句。"));
        for (const citation of Array.isArray(direction.citations) ? direction.citations : []) {
          const source = sources.get(citation.source_id), evidence = node("div", "rs-future-evidence");
          evidence.append(source ? publicSourceLink(source) : node("p", "rs-muted", `${citation.source_id || "未知来源"} · 不在本次核验来源清单中`));
          if (source) evidence.append(node("p", "rs-muted rs-small", `发表时间：${source.published || "未记录"} · 来源记录时间：${source.retrieved_at || "未记录"}`));
          evidence.append(node("blockquote", "rs-future-quote", citation.quote || "未返回引句")); item.append(evidence);
        }
        group.append(item);
      }
      panel.append(group);
    }
    for (const error of Array.isArray(audit.errors) ? audit.errors : []) {
      panel.append(node("p", "rs-muted rs-small", `${error.code || "核验提示"}：${error.message || "本次核验存在未完成步骤。"}`));
    }
    container.append(panel);
  }

  function lifecycle(container, options) {
    if (!(container instanceof Element)) throw new TypeError("A DOM container is required.");
    const abort = new AbortController();
    const disposers = [], blobs = new Set(), timers = new Set();
    let destroyed = false;
    const shell = node("section", "rs-shell");
    const notice = node("p", "rs-notice");
    notice.setAttribute("role", "status");
    notice.hidden = true;
    shell.append(notice);
    container.replaceChildren(shell);
    const api = async (path, init = {}) => {
      if (destroyed) throw new Error("页面已关闭。");
      if (typeof options.api === "function") return options.api(path, { ...init, signal: abort.signal });
      const response = await fetch(path, { credentials: "same-origin", cache: "no-store", ...init, signal: abort.signal });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(typeof payload.detail === "string" ? payload.detail : `请求失败（${response.status}）。`);
      return payload;
    };
    const show = (message, error = false) => {
      if (destroyed) return;
      window.ResearchNotices.show(message, error, notice);
    };
    const svgBlob = async (path, init = {}) => {
      const response = await fetch(path, { credentials: "same-origin", cache: "no-store", ...init, signal: abort.signal });
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(typeof payload.detail === "string" ? payload.detail : `图形生成失败（${response.status}）。`);
      }
      const text = await response.text();
      if (!response.headers.get("content-type")?.includes("image/svg+xml") || !text.includes("<svg")) {
        throw new Error("服务未返回有效SVG图像。");
      }
      if (destroyed) throw new Error("页面已关闭。");
      const url = URL.createObjectURL(new Blob([text], { type: "image/svg+xml" }));
      blobs.add(url);
      return url;
    };
    return {
      shell, api, show, svgBlob, get alive() { return !destroyed; },
      on(target, event, handler) { target.addEventListener(event, handler); disposers.push(() => target.removeEventListener(event, handler)); },
      later(callback, delay) {
        const timer = setTimeout(() => { timers.delete(timer); if (!destroyed) callback(); }, delay);
        timers.add(timer); return timer;
      },
      stopTimer(timer) { clearTimeout(timer); timers.delete(timer); },
      own(callback) { disposers.push(callback); },
      blob(content, type) { const url = URL.createObjectURL(new Blob([content], { type })); blobs.add(url); return url; },
      revoke(url) { if (blobs.delete(url)) URL.revokeObjectURL(url); },
      destroy() {
        if (destroyed) return;
        destroyed = true; abort.abort();
        for (const timer of timers) clearTimeout(timer);
        for (const dispose of disposers) dispose();
        for (const url of blobs) URL.revokeObjectURL(url);
        blobs.clear(); timers.clear();
      },
    };
  }

  function mountTasks(container, options = {}) {
    const life = lifecycle(container, options);
    const state = { documents: [], jobs: [], selectedDocuments: new Set(), configured: false,
      selectedJob: null, refreshGeneration: 0, readGeneration: 0, pollTimer: null, resultStamp: "", bilingual: null, posting: false,
      suspended: false, refreshing: false, selectingJobId: null, refreshOnResume: false, readOnResume: null, deferredJob: null,
      initialJobId: typeof options.initialJobId === "string" && options.initialJobId ? options.initialJobId : null };
    const heading = node("header", "rs-heading");
    const headingText = node("div", "");
    headingText.append(node("span", "rs-eyebrow", "RESEARCH TASKS"), node("h2", "", "研究任务"),
      node("p", "rs-muted", "选择资料，执行问答、翻译与写作；结果保留来源与执行记录。"));
    const refreshButton = button("刷新资料与任务");
    heading.append(headingText, refreshButton);
    life.shell.append(heading);
    const configuration = node("p", "rs-configuration", "正在检查模型配置……");
    life.shell.append(configuration);
    const layout = node("div", "rs-task-layout");
    const sidebar = node("div", "rs-task-sidebar");
    const form = node("form", "rs-card rs-task-form");
    form.tabIndex = -1;
    const formHeading = node("div", "rs-task-form-heading");
    const returnToResult = button("返回任务结果"); returnToResult.classList.add("rs-task-return");
    formHeading.append(node("h3", "rs-card-title", "新建任务"), returnToResult);
    form.append(formHeading);
    kindLabels.discover_qa = "下载与原文问答";
    const kind = select(Object.entries(kindLabels).filter(([value]) => !["discover_qa","future_filter"].includes(value)));
    form.append(field("任务类型", kind));
    const documentsGroup = node("fieldset", "rs-fieldset");
    documentsGroup.append(node("legend", "rs-label", "选择参考文献 · 最多20篇"));
    const older = input("checkbox");
    const olderLabel = node("label", "rs-check-label");
    olderLabel.append(older, node("span", "", "同时显示非活动版本"));
    const documentChoices = node("div", "rs-document-choices");
    documentsGroup.append(olderLabel, documentChoices);
    form.append(documentsGroup);
    const single = select([]);
    const singleField = field("待翻译文献", single);
    form.append(singleField);
    const scope = select([["document", "整篇可用文本"], ["page", "指定页面"]]);
    const page = input("number"); page.min = "1"; page.step = "1"; page.value = "1";
    const pageField = field("物理页码", page);
    const scopeGroup = node("div", "rs-form-row"); scopeGroup.append(field("翻译范围", scope), pageField);
    form.append(scopeGroup);
    const target = select([["zh", "中文"], ["en", "学术英文"]]);
    const targetField = field("目标语言", target); form.append(targetField);
    const glossaryInput=textarea("每行一项，例如：perplexity => 困惑度\nn-gram => n-gram",3);
    glossaryInput.maxLength=10000;
    const glossaryField=field("统一术语（可选，最多50项）",glossaryInput);form.append(glossaryField);
    const prompt = textarea("输入希望从原文核验的问题。", 6);
    const promptField = field("研究问题", prompt); form.append(promptField);
    const futureSearchOptions=node('fieldset','rs-fieldset');
    futureSearchOptions.append(node('legend','rs-label','最新研究检索范围'));
    const liveSearch=input('checkbox');liveSearch.checked=true;
    const liveLabel=node('label','rs-check-label');liveLabel.append(liveSearch,node('span','','本次强制实时检索，不复用缓存'));
    const researchDays=select([['30','最近30天'],['90','最近90天'],['365','最近一年']]);researchDays.value='90';
    futureSearchOptions.append(liveLabel,field('论文提交时间',researchDays));form.append(futureSearchOptions);
    const inputNote = node("p", "rs-muted rs-small"); form.append(inputNote);
    const submit = button("创建研究任务", true); submit.type = "submit"; submit.disabled = true;
    form.append(submit, node("p", "rs-muted rs-small", "执行任务时，所选原文或输入文本将发送到你配置的模型服务。"));
    sidebar.append(form);
    const history = node("section", "rs-card");
    history.tabIndex = -1;
    history.append(node("h3", "rs-card-title", "任务历史"));
    const historyList = node("div", "rs-history"); history.append(historyList); sidebar.append(history);
    const detail = node("section", "rs-card rs-task-detail");
    detail.hidden = true; detail.tabIndex = -1;
    const shortcuts = node("div", "rs-task-shortcuts");
    const newTask = button("新建任务"), viewHistory = button("任务历史");
    shortcuts.append(newTask, viewHistory);
    const detailHeading = node("h3", "rs-card-title", "选择或创建一个任务");
    const meta = node("p", "rs-muted rs-small");
    const status = node("p", "rs-job-status"); status.setAttribute("role", "status");
    const usage = node("p", "rs-muted rs-small");
    const progress = node("progress", "rs-progress"); progress.hidden = true;
    const actions = node("div", "rs-actions");
    const cancel = button("取消任务"), retry = button("继续重试");
    const recheckDirections = button("按全文判定并筛选方向"); recheckDirections.hidden = true;
    const exportLink = link("导出任务与来源", "#");
    actions.append(cancel, retry, recheckDirections, exportLink); actions.hidden = true;
    life.on(recheckDirections,"click",async()=>{
      const sourceTask=state.selectedJob?.id;if(!sourceTask)return;
      recheckDirections.disabled=true;
      try {
        const response=await life.api('/api/research/tasks',jsonOptions({kind:'future_filter',source_task_id:sourceTask}));
        if(life.alive){displayJob(response.job);schedulePoll();revealTaskResult();}
      } catch(error){life.show(error.message,true);}
      finally{if(life.alive)recheckDirections.disabled=!state.configured;}
    });
    const resultArea = node("div", "rs-result");
    resultArea.append(node("p", "rs-empty", "这里将显示实际任务结果。模型未配置时不会生成示例答案。"));
    const eventDetails = node("details", "rs-events");
    eventDetails.append(node("summary", "", "执行记录"));
    const eventList = node("ol", "rs-event-list"); eventDetails.append(eventList); eventDetails.hidden = true;
    detail.append(shortcuts, detailHeading, meta, status, usage, progress, actions, resultArea, eventDetails);
    layout.append(sidebar, detail); life.shell.append(layout);

    function revealSection(section) {
      section.focus({ preventScroll: true });
      section.scrollIntoView({ block: "start", behavior: "auto" });
    }
    function revealTaskResult() {
      if (!life.alive || state.suspended) return;
      if (getComputedStyle(shortcuts).display !== "none") revealSection(detail);
    }
    life.on(newTask, "click", () => { revealSection(form); kind.focus({ preventScroll: true }); });
    life.on(viewHistory, "click", () => revealSection(history));
    life.on(returnToResult, "click", revealTaskResult);

    function visibleDocuments() { return state.documents.filter((doc) => !doc.archived_at && (older.checked || doc.is_current_version !== false)); }
    function renderDocuments() {
      const visible = visibleDocuments();
      const valid = new Set(visible.filter((doc) => doc.available_run).map((doc) => doc.id));
      state.selectedDocuments = new Set([...state.selectedDocuments].filter((id) => valid.has(id)));
      const singleBefore = single.value;
      single.replaceChildren(); documentChoices.replaceChildren();
      if (!visible.length) documentChoices.append(node("p", "rs-muted rs-small", "文献库尚无可选资料，请先导入并解析PDF。"));
      for (const doc of visible) {
        const choice = node("label", "rs-document-choice");
        const check = input("checkbox"); check.value = doc.id; check.disabled = !doc.available_run;
        check.checked = state.selectedDocuments.has(doc.id);
        check.addEventListener("change", () => {
          if (check.checked && state.selectedDocuments.size >= 20) { check.checked = false; life.show("单个任务最多选择20篇文献。", true); return; }
          if (check.checked) state.selectedDocuments.add(doc.id); else state.selectedDocuments.delete(doc.id);
        });
        const info = node("span", ""); info.append(node("span", "rs-document-title", doc.title || doc.filename || "未命名文献"),
          node("small", "rs-muted", doc.available_run ? `${doc.page_count}页 · ${doc.is_current_version === false ? "非活动版本" : "活动版本"}` : "尚无可用解析文字"));
        choice.append(check, info); documentChoices.append(choice);
        if (doc.available_run) { const option = node("option", "", doc.title || doc.filename); option.value = doc.id; single.append(option); }
      }
      if (valid.has(singleBefore)) single.value = singleBefore;
      updatePageLimit();
    }
    function updatePageLimit() {
      const doc = state.documents.find((item) => item.id === single.value);
      if (doc) page.max = String(doc.page_count); else page.removeAttribute("max");
    }
    function changeKind() {
      const taskKind = kind.value, isDocumentTranslation = taskKind === "translate", isTextTranslation = taskKind === "translate_text";
      documentsGroup.hidden = isDocumentTranslation || isTextTranslation;
      singleField.hidden = !isDocumentTranslation; scopeGroup.hidden = !isDocumentTranslation;
      pageField.hidden = scope.value !== "page"; page.required = isDocumentTranslation && scope.value === "page";
      targetField.hidden = !isDocumentTranslation && !isTextTranslation;
      glossaryField.hidden = !isDocumentTranslation && !isTextTranslation;
      futureSearchOptions.hidden=taskKind!=='future_work';
      promptField.hidden = isDocumentTranslation;
      prompt.required = ["qa", "compare", "draft", "translate_text"].includes(taskKind);
      const label = taskKind === "draft" ? "研究idea与论文框架要求" : isTextTranslation ? "待翻译学术文本" : ["review", "future_work"].includes(taskKind) ? "关注方向（可选）" : "研究问题";
      promptField.firstChild.textContent = label;
      prompt.maxLength = isTextTranslation ? 60000 : ["qa", "compare"].includes(taskKind) ? 4000 : 10000;
      prompt.placeholder = isTextTranslation ? "粘贴需要翻译的摘要或正文。" : taskKind === "draft" ? "描述研究目标、已有工作与准备验证的想法。" : "说明希望核验的问题或关注的研究方向。";
      inputNote.textContent = taskKind === "future_work" ? "按所选提交时间范围检索arXiv，保存真实检索回执，获取最多3篇公开PDF并逐条件审核。仅同一论文充分、共同覆盖全部条件且联合审核通过时移出候选；部分覆盖和证据不足保留，原始记录不删除。"
        : isDocumentTranslation ? "整篇翻译覆盖全部可用文本块；扫描或失败页面会单独标注，不会假装完成。"
        : taskKind === "draft" ? "生成的是待验证研究框架，不会把计划中的实验当成已完成结果。" : "";
    }
    function renderHistory() {
      if (!state.jobs.length) { historyList.replaceChildren(node("p", "rs-muted rs-small", "暂无研究任务。")); return; }
      const existing = new Map([...historyList.querySelectorAll('.rs-history-item')].map(entry => [entry.dataset.jobId, entry]));
      const retained = new Set();
      let index = 0;
      for (const job of state.jobs) {
        let entry = existing.get(job.id);
        if (!entry) {
          entry = button(""); entry.className = "rs-history-item"; entry.dataset.jobId = job.id;
          entry.append(node("strong", ""), node("span", ""));
          entry.addEventListener("click", () => selectJob(job.id, true));
        }
        entry.classList.toggle("is-selected", job.id === state.selectedJob?.id);
        entry.setAttribute("aria-pressed", String(job.id === state.selectedJob?.id));
        const title = kindLabels[job.kind] || job.kind;
        const summary = `${statusLabels[job.status] || job.status} · ${dateLabel(job.created_at)}`;
        if (entry.firstChild.textContent !== title) entry.firstChild.textContent = title;
        if (entry.lastChild.textContent !== summary) entry.lastChild.textContent = summary;
        if (historyList.children[index] !== entry) historyList.insertBefore(entry, historyList.children[index] || null);
        retained.add(entry); index++;
      }
      for (const entry of [...historyList.children]) if (!retained.has(entry)) entry.remove();
    }
    function openEvidence(source) {
      if (typeof options.onEvidence !== "function") return Promise.resolve();
      return Promise.resolve().then(() => options.onEvidence(source)).catch((error) => life.show(error.message || "无法打开原文出处。", true));
    }
    function citationButtons(claim, sources, contextLabel = "") {
      const row = node("div", "rs-citations");
      for (const [index,citation] of (Array.isArray(claim.citations) ? claim.citations : []).entries()) {
        const source = sources.get(citation.evidence_id); if (!source) continue;
        const caption=`第${source.page}页 · ${source.document_title || source.filename || "原文"}`;
        const cite = button(`${contextLabel ? `引用${index+1} · ` : ''}${caption}`);
        if(contextLabel)cite.setAttribute('aria-label',`${contextLabel}，引用${index+1}，${caption}`);
        cite.className = "rs-citation"; cite.title = citation.quote || "查看原文";
        cite.disabled = typeof options.onEvidence !== "function";
        cite.addEventListener("click", () => openEvidence(source)); row.append(cite);
      }
      return row;
    }
    function renderClaims(parent, claims, sources) {
      for (const claim of Array.isArray(claims) ? claims : []) {
        const item = node("div", "rs-claim");
        item.append(node("p", "rs-prose", claim.text || ""), citationButtons(claim, sources)); parent.append(item);
      }
    }
    function renderResult(job) {
      const signature = `${job.id}:${job.updated_at}:${job.completed_items?.length || 0}`;
      if (state.resultStamp === signature) return;
      state.resultStamp = signature;
      const checkpoints = ['future_work','future_filter'].includes(job.kind) ? new Map((job.completed_items || []).map((item) => [item.stage, item.result])) : new Map();
      let output = job.result || checkpoints.get("future_filter_result") || checkpoints.get("future_fulltext") || checkpoints.get("future_verification") || checkpoints.get("future_synthesis");
      if (output && job.kind === 'future_work' && job.payload?.full_text_verify && !output.future_direction_filter) output = {...output, future_fulltext_pending:true};
      const addGlossary=() => {
        const entries=Object.entries(output?.glossary || job.payload?.glossary || {});
        if(!entries.length) return;
        const details=node("details","rs-events"),list=node("ul","rs-event-list");
        details.append(node("summary","",`本任务统一术语（${entries.length}项）`));
        for(const [source,target] of entries) list.append(node("li","",`${source} → ${target}`));
        details.append(list);resultArea.prepend(details);
      };
      if (job.kind === "translate") {
        if (pending(job) && !output && !job.completed_items?.length) {
          state.bilingual?.destroy(); state.bilingual = null;
          resultArea.replaceChildren(node("p", "rs-empty", job.progress > 0
            ? `已保存${job.progress}${job.total ? `/${job.total}` : ""}段译文。任务结束后加载双语阅读；导出可查看当前已保存内容。`
            : "正在逐段翻译。进度与已保存结果由服务端记录，任务结束后加载双语阅读。"));
          return;
        }
        const value = output || { blocks: job.completed_items || [], status: "partial", total_blocks: job.total || undefined,
          target_language: job.payload?.target_language || "zh" };
        if (window.ResearchBilingual) {
          state.bilingual = window.ResearchBilingual.render(resultArea, value, { onEvidence: options.onEvidence });
          addGlossary();
        } else {
          resultArea.replaceChildren(node("p", "rs-muted", "双语阅读组件未加载；可通过任务导出查看已保存结果。"));
        }
        return;
      }
      state.bilingual?.destroy(); state.bilingual = null;
      resultArea.replaceChildren();
      addGlossary();
      if (!output) { resultArea.append(node("p", "rs-empty", pending(job) ? "任务正在处理，结果保存后会在这里显示。" : "此任务尚无可展示结果。")); return; }
      if (output.future_fulltext_pending) resultArea.append(node('p','rs-result-note',`全文核验或最终筛选尚未完成，全部原始方向继续保留（${futureDirections(output).retained.length} 项）；摘要初筛仅作为历史记录。`));
      if (output.status === "insufficient_evidence") resultArea.append(node("p", "rs-result-note", "证据不足：本次未生成确定性结论。"));
      const isFutureWork = job.kind === "future_work" || output.kind === "future_work";
      if (isFutureWork && !job.result) resultArea.append(node("p", "rs-muted", "以下展示已保存的阶段结果；任务的完成或中断状态以上方记录为准。"));
      if (output.future_direction_filter) {
        const audit=output.future_direction_filter,panel=node('section','rs-result-section rs-direction-filter');
        panel.append(node('h4','','全文条件覆盖与筛选'),node('p','rs-muted',audit.notice));
        if(output.future_fulltext?.status==='controlled_evidence_selection')panel.append(node('p','rs-small rs-muted',output.future_fulltext.notice));
        const labels={fully_addressed:'充分覆盖 · 移出重点候选',partially_addressed:'部分覆盖 · 保留',evidence_insufficient:'证据不足 · 保留'};
        const showFinding=(parent,finding,review,contextLabel)=>{
          if(!finding)return;
          parent.append(node('p','rs-prose',finding.claim.text),citationButtons(finding.claim,new Map(finding.sources.map(s=>[s.evidence_id,s])),contextLabel));
          for(const [index,cite] of finding.claim.citations.entries())parent.append(node('p','rs-small rs-muted',`引用${index+1}原文`),node('blockquote','rs-future-quote',cite.quote));
          const badge=node('p','rs-small rs-muted',`本条审核：${review?.verdict==='supported'?'通过':'未通过'} · ${finding.identity.claim_id.slice(0,14)}…`);
          badge.title=finding.identity.claim_id;parent.append(badge);
          if(review?.reason)parent.append(node('p','rs-small rs-muted',review.reason));
        };
        for(const direction of audit.directions || []) {
          const card=node('section',`rs-future-direction${direction.excluded?' is-excluded':''}`);
          card.append(node('h5','',`${direction.direction_id} · ${direction.heading}`),node('p','rs-future-status',labels[direction.status] || '保留待核验'),
            node('p','rs-proposal',direction.proposed_work),node('p','rs-muted',direction.reason));
          for(const condition of direction.conditions || []) {
            const item=node('article','rs-future-direction rs-filter-condition');
            item.append(node('h5','',`${condition.condition_id} · ${condition.source_text}`),
              node('p','rs-muted',condition.status==='fully_addressed'?'本条件已充分覆盖':condition.status==='partially_addressed'?'本条件仅部分覆盖':'本条件证据不足'));
            showFinding(item,condition.finding,condition.review,`${direction.direction_id}，${condition.condition_id}`);
            if(!condition.finding)item.append(node('p','rs-muted',condition.reason));
            if(!condition.review)item.append(node('p','rs-small rs-muted','未形成可审核引句，未执行支持度审核；按保守规则保留。检索与补证过程见本方向完整记录。'));
            card.append(item);
          }
          if(direction.excluded) {
            const proof=node('article','rs-future-direction rs-filter-joint');proof.append(node('h5','','全部条件的联合覆盖依据'));
            showFinding(proof,direction.joint_finding,direction.joint_review,`${direction.direction_id}，联合覆盖`);card.append(proof);
          } else if(direction.joint_review)card.append(node('p','rs-muted','联合审核未确认全部条件共同满足：'+direction.joint_review.reason));
          const ownStages=(job.completed_items || []).filter(item=>item.stage?.startsWith(`future_filter:${direction.direction_id}:`));
          const details=node('details','rs-events');details.append(node('summary','','查看本方向完整判定与审核记录'),node('pre','rs-prose',JSON.stringify({final_decision:direction,checkpoint_scope:'this direction only; includes actual retrieval and unreviewable evidence gates',checkpoints:ownStages},null,2)));
          card.append(details);panel.append(card);
        }
        resultArea.append(panel);
      }
      if (output.future_fulltext && output.future_fulltext.status !== 'controlled_evidence_selection') {
        const full = output.future_fulltext, panel = node("section", "rs-result-section");
        panel.append(node("h4", "", "公开论文正文核验"), node("p", "rs-muted", full.notice));
        for (const direction of full.directions || []) {
          const card = node("section", "rs-result-section");
          card.append(node("h5", "", `${direction.direction_id} · ${direction.heading}`),
            node("p", "rs-proposal", "待验证设想：" + direction.proposed_work),
            node("p", "rs-muted", `${direction.full_text_reviewed ? "正文引句审核通过" : "正文证据不足"} · ${direction.reason || '本阶段未形成完整正文判定，保留待核验。'}`));
          const answer = direction.answer || {};
          const sources = new Map((answer.sources || []).map(s => [s.evidence_id, s]));
          for (const [index, claim] of (answer.claims || []).entries()) {
            const item = node("article", "rs-future-direction"), identity = answer.grounding?.claim_catalog?.[index];
            const review = answer.grounding?.claim_reviews?.find(r => r.claim_id === identity?.claim_id && r.claim_index === index);
            item.append(node("p", "rs-small rs-muted", `结论 ${index+1} · ${identity?.claim_id || "未记录"} · ${review?.verdict || "未通过审核"}`));
            renderClaims(item, [claim], sources);
            for (const cite of claim.citations || []) item.append(node("blockquote", "rs-future-quote", cite.quote));
            if (review?.reason) item.append(node("p", "rs-small rs-muted", "本条审核：" + review.reason));
            card.append(item);
          }
          if (answer.message) card.append(node("p", "rs-muted", "模型保留的范围说明与证据缺口（不作为已核验事实结论）：" + answer.message));
          const details = node("details", "rs-events");
          details.append(node("summary", "", "核对本方向的引用与审核记录"),
            node("pre", "rs-prose", JSON.stringify({direction_id: direction.direction_id,
              section_index: direction.section_index, documents: direction.documents,
              grounding: answer.grounding || null}, null, 2)));
          card.append(details); panel.append(card);
        }
        const records = node("details", "rs-events");
        records.append(node("summary", "", "查看全文下载与解析记录"), node("pre", "rs-prose", JSON.stringify(full.sources || [], null, 2)));
        if (full.source_selection?.length) records.append(node("p", "rs-muted", "选源仅按标题与摘要中的明确主题匹配，不构成已解决或新颖性判定。"), node("pre", "rs-prose", JSON.stringify(full.source_selection, null, 2)));
        panel.append(records); resultArea.append(panel);
      }
      if (isFutureWork) renderFutureVerification(resultArea, output);
      const excludedSections = new Map((isFutureWork ? futureDirections(output).excluded : []).map((direction) => [futureSectionIndex(direction), direction]));
      const sources = new Map((output.sources || []).map((source) => [source.evidence_id, source]));
      if (Array.isArray(output.claims) && output.claims.length) renderClaims(resultArea, output.claims, sources);
      else if (Array.isArray(output.sections) && output.sections.length) {
        for (const [index, section] of output.sections.entries()) {
          const group = node("section", "rs-result-section"); group.append(node("h4", "", section.heading || "研究内容"));
          if (excludedSections.has(index)) {
            group.classList.add("is-excluded");
            group.append(node("p", "rs-future-excluded-note", output.future_direction_filter ? "本方向已有充分全文覆盖依据并通过联合审核，已移出重点候选。原始设想与记录保留。" : "此方向暂移出重点候选，需全文确认。以下保留原章节供核对，不作为当前推荐方向。"));
          }
          renderClaims(group, section.claims, sources);
          if (section.proposed_work) group.append(node("p", "rs-proposal", "待验证研究设想：" + section.proposed_work));
          resultArea.append(group);
        }
      } else if (output.translated_text) {
        const original = node("details", "rs-original"); original.append(node("summary", "", "查看输入原文"), node("p", "rs-prose", output.source_text || ""));
        resultArea.append(original, node("p", "rs-prose", output.translated_text));
      } else resultArea.append(node("p", "rs-prose", output.answer || output.content || output.message || "任务已结束，未返回文本结果。"));
      if (output.message && (output.claims?.length || output.sections?.length)) resultArea.append(node("p", "rs-muted", output.message));
      if (output.references?.length) {
        const references = node("section", "rs-references"); references.append(node("h4", "", "来源清单"));
        const list = node("ul", "");
        for (const reference of output.references) list.append(node("li", "", `${reference.title || reference.filename} · ${reference.filename || "本地文献"}`));
        references.append(list); resultArea.append(references);
      }
      if (output.limitations?.length) {
        const list = node("ul", "rs-limitations"); for (const text of output.limitations) list.append(node("li", "", text)); resultArea.append(list);
      }
      if (output.trace?.length) {
        const trace = node("details", "rs-events"); trace.append(node("summary", "", "查看结果验证步骤"));
        const list = node("ol", "rs-event-list"); for (const event of output.trace) list.append(node("li", "", event.detail || event.step || ""));
        trace.append(list); resultArea.append(trace);
      }
      if (output.architecture?.nodes?.length) {
        const diagram = node("section", "rs-diagram-output");
        const renderButton = button("生成方案结构图"); const preview = node("div", "rs-image-output");
        diagram.append(node("h4", "", "待验证的方案结构"), renderButton, preview); resultArea.append(diagram);
          let imageUrl = null, scriptUrl = null;
        renderButton.addEventListener("click", async () => {
          renderButton.disabled = true;
          try {
            const script = await life.api("/api/diagrams/script", jsonOptions(output.architecture));
            const url = await life.svgBlob("/api/diagrams", jsonOptions(output.architecture));
            if (!life.alive || state.selectedJob?.id !== job.id || !diagram.isConnected) { life.revoke(url); return; }
            if (imageUrl) life.revoke(imageUrl); imageUrl = url;
              if (scriptUrl) life.revoke(scriptUrl); scriptUrl = life.blob(script.script, "text/x-python");
            const image = node("img", "rs-chart-image"); image.src = url; image.alt = "根据任务中的方案节点实际渲染的结构图";
            const downloads = node("div", "rs-actions");
            downloads.append(link("下载SVG结构图", url, `architecture-${job.id}.svg`), link("下载Python复现脚本", scriptUrl, `architecture-${job.id}.py`));
            preview.replaceChildren(image, downloads);
          } catch (error) { life.show(error.message, true); } finally { if (life.alive) renderButton.disabled = false; }
        });
      }
    }
    function displayJob(job) {
      if (!life.alive) return;
      if (state.suspended) { state.deferredJob = job; return; }
      if (state.selectedJob?.id !== job.id) { state.bilingual?.destroy(); state.bilingual = null; state.resultStamp = ""; }
      state.selectedJob = job;
      layout.classList.add("rs-has-task"); detail.hidden = false;
      state.jobs = [job, ...state.jobs.filter((item) => item.id !== job.id)].sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)));
      detailHeading.textContent = kindLabels[job.kind] || "研究任务";
      meta.textContent = `${dateLabel(job.created_at)} · 任务 ${job.id.slice(0, 8)}`;
      const counter = job.total > 0 ? ` · 已保存 ${job.progress}/${job.total} ${['discover_qa','future_work','future_filter'].includes(job.kind) ? '步' : '段'}` : "";
      const balanceError = /HTTP\s*402/.test(job.error || "") && (job.model_calls || []).some(call => call.http_status === 402 && call.provider_host === "api.deepseek.com");
      const errorText = balanceError ? "DeepSeek账户余额不足（HTTP 402）。请检查或补充账户余额；已保存阶段会保留，恢复后可重试。" : job.error;
      status.textContent = (job.cancel_requested && pending(job) ? "已请求取消，等待当前操作结束" : statusLabels[job.status] || job.status) + counter + (errorText ? `：${errorText}` : "")
        + (job.status === "interrupted" ? "。上一次调用的结果与扣费状态未知，继续重试可能重复消耗模型额度。" : "");
      status.classList.toggle("rs-error-text", ["failed", "interrupted"].includes(job.status));
      const meter=job.usage;
      usage.textContent=meter?.calls ? `模型请求 ${meter.calls} 次 · 服务商已报告 ${meter.total_tokens ?? "未知"} Tokens（输入 ${meter.prompt_tokens ?? "未知"} / 输出 ${meter.completion_tokens ?? "未知"}） · 用量回执 ${meter.usage_reported_calls}/${meter.calls} 次${meter.unknown_outcomes ? ` · ${meter.unknown_outcomes} 次结果未知` : ""} · 金额未估算`
        : "尚无模型用量记录；历史调用不会补算。";
      if (meter?.cost) usage.textContent=usage.textContent.replace("金额未估算",`已报告调用估算 US$${Number(meter.cost.min).toFixed(6)}–${Number(meter.cost.max).toFixed(6)}（${meter.cost.estimated_calls}/${meter.calls} 次；公开价${meter.cost.price_checked_at}，非账单）`);
      if (pending(job) && job.events?.length) usage.textContent += ` · 当前阶段：${job.events.at(-1).message}`;
      progress.hidden = !pending(job) && !(job.total > 0);
      if (job.total > 0) { progress.max = job.total; progress.value = job.progress; } else progress.removeAttribute("value");
      actions.hidden = false; cancel.hidden = !pending(job); cancel.disabled = Boolean(job.cancel_requested);
      recheckDirections.hidden=job.status!=='completed' || !job.result?.future_fulltext;
      recheckDirections.disabled=!state.configured;
      retry.hidden = !["failed", "cancelled", "interrupted"].includes(job.status); retry.disabled = !state.configured;
      retry.textContent = job.status === "interrupted" ? "确认重试（可能重复消耗）" : "继续重试";
      exportLink.href = `/api/research/tasks/${encodeURIComponent(job.id)}/export`; exportLink.download = `research-${job.id}.md`;
      eventList.replaceChildren();
      for (const event of job.events || []) eventList.append(node("li", "", `${dateLabel(event.created_at)} ${event.message || event.stage || ""}`));
      for (const call of job.model_calls || []) eventList.append(node("li", "", `${call.model} · ${call.output_schema} · ${call.state} · ${call.duration_ms ?? "待返回"} ms · ${call.usage?.total_tokens ?? "未知"} Tokens · 请求 ${call.id.slice(0,8)}`));
      eventDetails.hidden = !job.events?.length;
      renderResult(job); renderHistory();
    }
    function schedulePoll() {
      life.stopTimer(state.pollTimer);
      state.pollTimer = null;
      if (!life.alive || state.suspended || !pending(state.selectedJob)) return;
      const id = state.selectedJob.id, generation = state.readGeneration;
      state.pollTimer = life.later(async () => {
        if (state.suspended || generation !== state.readGeneration) return;
        try {
          const response = await life.api(`/api/research/tasks/${encodeURIComponent(id)}`);
          if (state.selectedJob?.id !== id || !life.alive || state.suspended || generation !== state.readGeneration) return;
          displayJob(response.job); schedulePoll();
        } catch (error) { if (life.alive && !state.suspended && generation === state.readGeneration) life.show(`任务状态更新中断：${error.message} 可点击刷新继续查看。`, true); }
      }, 1500);
    }
    async function selectJob(id, reveal = false) {
      if (!life.alive) return;
      if (state.suspended) { state.readOnResume = id; return; }
      const generation = ++state.readGeneration;
      life.stopTimer(state.pollTimer);
      state.pollTimer = null;
      state.selectingJobId = id;
      state.selectedJob = state.jobs.find((item) => item.id === id) || (state.selectedJob?.id === id ? state.selectedJob : { id });
      try {
        const response = await life.api(`/api/research/tasks/${encodeURIComponent(id)}`);
        if (state.selectedJob?.id !== id || !life.alive || state.suspended || generation !== state.readGeneration) return;
        displayJob(response.job); schedulePoll();
        if (reveal) revealTaskResult();
      } catch (error) { if (life.alive && !state.suspended && generation === state.readGeneration) life.show(error.message, true); }
      finally { if (generation === state.readGeneration) state.selectingJobId = null; }
    }
    async function refresh(preferredJobId = null) {
      if (!life.alive) return;
      if (state.suspended) { state.refreshOnResume = true; return; }
      const generation = ++state.refreshGeneration;
      ++state.readGeneration;
      life.stopTimer(state.pollTimer); state.pollTimer = null;
      state.refreshing = true;
      refreshButton.disabled = true;
      try {
        const [capabilities, documents, jobs] = await Promise.all([
          life.api("/api/capabilities"), life.api("/api/documents"), life.api("/api/research/tasks"),
        ]);
        if (!life.alive || state.suspended || generation !== state.refreshGeneration) return;
        state.configured = Boolean(capabilities.intelligence?.configured ?? capabilities.llm);
        configuration.textContent = state.configured ? "模型配置已就绪。任务会调用实际服务；输出仍需结合原文核验。" : "尚未配置大模型服务。可以准备任务输入，配置完成后刷新；当前不会生成模拟回答。";
        configuration.classList.toggle("is-ready", state.configured);
        state.documents = documents.documents || []; state.jobs = jobs.jobs || [];
        renderDocuments(); renderHistory();
        submit.disabled = !state.configured || state.posting;
        const selectedId = state.initialJobId || preferredJobId || (state.jobs.find((job) => job.id === state.selectedJob?.id) || state.jobs[0])?.id;
        state.initialJobId = null;
        if (selectedId) await selectJob(selectedId);
        else {
          state.selectedJob = null; state.bilingual?.destroy(); state.bilingual = null; state.resultStamp = "";
          layout.classList.remove("rs-has-task"); detail.hidden = true;
          actions.hidden = true; eventDetails.hidden = true; progress.hidden = true;
          detailHeading.textContent = "选择或创建一个任务"; meta.textContent = ""; status.textContent = "";
          eventList.replaceChildren();
          resultArea.replaceChildren(node("p", "rs-empty", "当前工作空间暂无研究任务。"));
        }
        if (life.alive && !state.suspended && generation === state.refreshGeneration) life.show("");
      } catch (error) { if (life.alive && !state.suspended && generation === state.refreshGeneration) life.show(error.message, true); }
      finally { if (life.alive && generation === state.refreshGeneration) { state.refreshing = false; refreshButton.disabled = false; } }
    }
    life.on(refreshButton, "click", async () => {
      try { if (typeof options.refreshLibrary === "function") await options.refreshLibrary(); await refresh(); }
      catch (error) { life.show(error.message || "资料刷新失败，请重试。", true); }
    });
    life.on(older, "change", renderDocuments); life.on(single, "change", updatePageLimit);
    life.on(kind, "change", () => { if (kind.value === "translate_text") target.value = "en"; changeKind(); });
    life.on(scope, "change", changeKind);
    life.on(form, "submit", async (event) => {
      event.preventDefault(); if (state.posting || !form.reportValidity()) return;
      if (!state.configured) { life.show("请先配置真实模型服务。", true); return; }
      const payload = { kind: kind.value };
      if(kind.value==='future_work'){payload.refresh_search=liveSearch.checked;payload.research_days=Number(researchDays.value);}
      if (["translate","translate_text"].includes(kind.value) && glossaryInput.value.trim()) {
        const entries=glossaryInput.value.split(/\r?\n/).filter(line=>line.trim());
        if(entries.length>50 || entries.some(line=>line.split("=>").length!==2 || line.split("=>").some(part=>!part.trim()))) {life.show("术语表每行使用“原文术语 => 统一译法”，最多50项。",true);return;}
        payload.glossary=Object.fromEntries(entries.map(line=>line.split("=>").map(part=>part.trim())));
        if(Object.keys(payload.glossary).length!==entries.length){life.show("术语表存在重复词条，请合并后提交。",true);return;}
      }
      if (kind.value === "translate") {
        if (!single.value) { life.show("请先选择有可用文字的文献。", true); return; }
        payload.document_id = single.value; payload.target_language = target.value;
        const doc = state.documents.find((item) => item.id === single.value);
        payload.run_id = doc.available_run.id;
        if (scope.value === "page") payload.page = Number(page.value);
      } else if (kind.value === "translate_text") { payload.text = prompt.value.trim(); payload.target_language = target.value; }
      else {
        payload.document_ids = [...state.selectedDocuments];
        if (!payload.document_ids.length || (kind.value === "compare" && payload.document_ids.length < 2)) {
          life.show(kind.value === "compare" ? "比较任务需要至少两篇文献。" : "请至少选择一篇参考文献。", true); return;
        }
        payload[["qa", "compare"].includes(kind.value) ? "question" : "instruction"] = prompt.value.trim();
      }
      state.posting = true; submit.disabled = true;
      try { const response = await life.api("/api/research/tasks", jsonOptions(payload)); if (life.alive) { life.show("任务已创建，可查看进度与已保存结果。"); displayJob(response.job); schedulePoll(); revealTaskResult(); } }
      catch (error) { life.show(error.message, true); }
      finally { state.posting = false; if (life.alive) submit.disabled = !state.configured; }
    });
    for (const [control, action] of [[cancel, "cancel"], [retry, "retry"]]) life.on(control, "click", async () => {
      const id = state.selectedJob?.id; if (!id) return; control.disabled = true;
      try { const response = await life.api(`/api/research/tasks/${encodeURIComponent(id)}/${action}`, { method: "POST" }); if (life.alive && state.selectedJob?.id === id) { displayJob(response.job); schedulePoll(); } }
      catch (error) { life.show(error.message, true); }
      finally { if (life.alive) control.disabled = action === "retry" ? !state.configured : Boolean(state.selectedJob?.cancel_requested); }
    });
    life.own(() => state.bilingual?.destroy());
    async function prepareExample(example) {
      if (!life.alive) throw new Error("页面已切换，请重新打开示例。");
      const ids = [...new Set(example.documentIds || [])];
      if (!Object.hasOwn(kindLabels, example.kind) || example.kind === "translate_text") throw new Error("此示例任务类型不可用。");
      const documents = (await life.api("/api/documents")).documents || [];
      if (!life.alive) throw new Error("页面已切换，请重新打开示例。");
      const valid = ids.map(id => documents.find(doc => doc.id === id && !doc.archived_at && doc.is_current_version !== false && doc.available_run));
      if (!ids.length || valid.some(doc => !doc) || (example.kind === "compare" && ids.length < 2)) {
        throw new Error("示例所需文献尚未就绪或已变更，请重新打开演示示例检查导入状态。");
      }
      if (example.kind === "translate" && (ids.length !== 1 || !Number.isInteger(example.page) || example.page < 1 || example.page > valid[0].page_count)) {
        throw new Error("示例翻译页码与当前文献不匹配，请核对论文版本。");
      }
      state.documents = documents; older.checked = false; state.selectedDocuments = new Set(ids);
      renderDocuments(); kind.value = example.kind; single.value = ids[0];
      prompt.value = example.question || example.instruction || "";
      scope.value = "page"; page.value = String(example.page || 1); target.value = "zh";
      changeKind(); updatePageLimit();
      life.show(`已准备“${example.title || kindLabels[example.kind]}”。尚未创建任务；点击“创建研究任务”后才会调用模型并消耗服务额度。`);
      revealSection(form); kind.focus({preventScroll: true});
    }
    changeKind();
    const ready = refresh();
    return {
      ready, refresh, prepareExample,
      getReturnLabel() { return ["translate", "translate_text"].includes(state.selectedJob?.kind) ? "返回翻译结果" : "返回任务结果"; },
      suspend() {
        if (!life.alive || state.suspended) return;
        state.refreshOnResume = state.refreshOnResume || state.refreshing;
        state.readOnResume = state.selectingJobId || state.readOnResume;
        state.suspended = true;
        ++state.refreshGeneration; ++state.readGeneration;
        state.refreshing = false; state.selectingJobId = null;
        refreshButton.disabled = false;
        life.stopTimer(state.pollTimer); state.pollTimer = null;
        state.bilingual?.suspend?.();
      },
      resume() {
        if (!life.alive || !state.suspended) return;
        state.suspended = false;
        if (state.deferredJob) { const job = state.deferredJob; state.deferredJob = null; displayJob(job); }
        state.bilingual?.resume?.();
        const refreshAgain = state.refreshOnResume, readAgain = state.readOnResume;
        state.refreshOnResume = false; state.readOnResume = null;
        if (refreshAgain) void refresh(readAgain);
        else if (readAgain) void selectJob(readAgain);
        else schedulePoll();
      },
      destroy: () => life.destroy(),
    };
  }

  function mountData(container, options = {}) {
    const life = lifecycle(container, options);
    const state = { datasets: [], selected: null, generation: 0, chartUrl: null, diagramUrl: null, diagramSpecUrl: null, diagramScriptUrl: null, units: {}, chartPosting: false };
    const heading = node("header", "rs-heading");
    const text = node("div", ""); text.append(node("span", "rs-eyebrow", "DATA & FIGURES"), node("h2", "", "数据与图表"),
      node("p", "rs-muted", "从实际CSV生成图表、图注和复现脚本；结构图使用明确的节点与连接。"));
    const refreshButton = button("刷新数据集"); heading.append(text, refreshButton); life.shell.append(heading);
    const uploadForm = node("form", "rs-card rs-upload-form");
    const file = input("file"); file.accept = ".csv,text/csv"; file.required = true; file.setAttribute("aria-label", "选择CSV文件");
    const upload = button("导入CSV", true); upload.type = "submit";
    uploadForm.append(field("CSV文件 · 不超过5 MiB", file), upload, node("p", "rs-muted rs-small", "支持UTF-8或GB18030，首行为字段名；最多10000行、60列。"));
    life.shell.append(uploadForm);
    const dataSelect = select([]); life.shell.append(field("已导入数据集", dataSelect));
    const previewCard = node("section", "rs-card");
    const previewTitle = node("h3", "rs-card-title", "数据预览"); const datasetInfo = node("p", "rs-muted rs-small");
    const columnsInfo = node("div", "rs-column-info"); const preview = node("div", "rs-table-scroll");
    previewCard.append(previewTitle, datasetInfo, columnsInfo, preview); life.shell.append(previewCard);
    const chartForm = node("form", "rs-card rs-chart-form"); chartForm.append(node("h3", "rs-card-title", "生成图表"));
    chartForm.hidden = true;
    const chartEmpty = node("p", "rs-chart-empty rs-muted", "导入 CSV 后，这里会显示图表类型、字段和绘图设置。");
    life.shell.append(chartEmpty);
    const chartKind = select([["line", "折线图"], ["bar", "柱状图"], ["radar3d", "三维分层雷达图"]]), x = select([]), yChoices = node("div", "rs-y-choices");
    const title = input("text", "可选，留空时根据字段生成标题"); title.maxLength = 150;
    const missing = select([["reject", "遇到缺失或无效值时停止"], ["drop", "明确忽略含无效值的整行"]]);
    const normalize = input("checkbox"), normalLabel = node("label", "rs-check-label"), normalText = node("span", "", "各数值列按Min–Max归一化"); normalLabel.append(normalize, normalText);
    const row = node("div", "rs-form-row"); row.append(field("图表类型", chartKind), field("横轴字段", x)); chartForm.append(row);
    const yGroup = node("fieldset", "rs-fieldset"); yGroup.append(node("legend", "rs-label", "数值字段 · 选择1至6列"), yChoices); chartForm.append(yGroup);
    const unitsArea = node("div", "rs-unit-fields"); chartForm.append(unitsArea, field("图表标题", title), field("缺失值处理", missing), normalLabel);
    const create = button("生成图表与图注", true); create.type = "submit"; create.disabled = true;
    const chartHint = node("p", "rs-muted rs-small", "折线和柱状图需1至500条有效记录，按CSV原行顺序展示。");
    const camera = node("div", "rs-form-row"); camera.hidden = true;
    const yaw = input("number"), elevation = input("number"); yaw.min = "-180"; yaw.max = "180"; yaw.value = "35";
    elevation.min = "15"; elevation.max = "75"; elevation.value = "30";
    camera.append(field("雷达图方位角（度）", yaw), field("雷达图仰角（度）", elevation));
    chartForm.append(camera, create, chartHint); life.shell.append(chartForm);
    life.on(chartKind, "change", () => {
      const radar = chartKind.value === "radar3d"; camera.hidden = !radar;
      normalText.textContent = radar ? "每个指标行在所选系列间按Min–Max归一化（常数指标取0.5）" : "各数值列按Min–Max归一化";
      chartHint.textContent = radar ? "三维雷达图：3至12行分别为指标轴，2至6个数值列分别为高度层；横轴字段提供指标名称。未经归一化的数值必须在0至1之间。高度表示系列类别，半径大小不自动代表性能优劣。修改角度后重新生成可保存独立图表。" : "折线和柱状图需1至500条有效记录，按CSV原行顺序展示。";
    });
    const chartOutput = node("section", "rs-card rs-chart-output"); chartOutput.hidden = true; life.shell.append(chartOutput);
    const diagramForm = node("form", "rs-card rs-diagram-form"); diagramForm.append(node("h3", "rs-card-title", "结构图"),
      node("p", "rs-muted rs-small", "输入节点与连接，服务端会实际渲染SVG。此图表示输入的结构，不自动证明模型或实验已经实现。"));
    const nodes = textarea('[{"id":"input","label":"输入"}]', 5), edges = textarea('[{"source":"input","target":"model"}]', 5);
    nodes.required = true; edges.value = "[]";
    const diagramFields = node("div", "rs-form-row"); diagramFields.append(field("节点JSON数组 · 每项含id和label", nodes), field("连接JSON数组 · 每项含source和target", edges));
    const makeDiagram = button("渲染结构图", true); makeDiagram.type = "submit";
    const diagramOutput = node("div", "rs-image-output"); diagramForm.append(diagramFields, makeDiagram, diagramOutput); life.shell.append(diagramForm);

    function selectedY() { return [...yChoices.querySelectorAll('input[type="checkbox"]:checked')].map((item) => item.value); }
    function renderUnits() {
      unitsArea.replaceChildren();
      for (const name of selectedY()) {
        const unit = input("text", "例如 ms、%、个"); unit.maxLength = 40; unit.value = state.units[name] || "";
        unit.addEventListener("input", () => { state.units[name] = unit.value; }); unitsArea.append(field(`${name} 的单位（可选）`, unit));
      }
    }
    function renderDataset(data) {
      chartForm.hidden = false; chartEmpty.hidden = true;
      if (state.selected?.id !== data.id) {
        chartOutput.hidden = true;
        previewCard.querySelector(".rs-example-source")?.remove();
        if (state.chartUrl) { life.revoke(state.chartUrl); state.chartUrl = null; }
      }
      state.selected = data; state.units = {}; create.disabled = false;
      previewTitle.textContent = data.filename || "数据预览";
      const previewRows = Array.isArray(data.rows) ? data.rows : [];
      datasetInfo.textContent = `${data.row_count}条记录 · ${data.headers.length}列 · ${data.encoding} · 预览前${previewRows.length}条`;
      columnsInfo.replaceChildren(); x.replaceChildren(); yChoices.replaceChildren(); unitsArea.replaceChildren();
      for (const column of data.columns) {
        const information = node("span", "rs-column-chip", `${column.name} · ${column.type === "number" ? "数值" : "文本或混合"} · 缺失${column.missing}`); columnsInfo.append(information);
        const option = node("option", "", column.name); option.value = column.name; x.append(option);
        const label = node("label", "rs-check-label"), check = input("checkbox"); check.value = column.name; check.disabled = column.numeric_count === 0;
        check.addEventListener("change", () => {
          if (selectedY().length > 6) { check.checked = false; life.show("最多选择6个数值字段。", true); }
          renderUnits();
        }); label.append(check, node("span", "", `${column.name}${column.numeric_count === 0 ? "（无数值）" : ""}`)); yChoices.append(label);
      }
      const table = node("table", "rs-table"); const thead = node("thead", ""), headRow = node("tr", "");
      for (const header of data.headers) { const th = node("th", "", header); th.scope = "col"; headRow.append(th); }
      thead.append(headRow); table.append(thead); const body = node("tbody", "");
      for (const row of previewRows) { const tr = node("tr", ""); for (const value of row) tr.append(node("td", value === "" ? "rs-cell-missing" : "", value === "" ? "—" : value)); body.append(tr); }
      table.append(body); preview.replaceChildren(table);
    }
    async function chooseDataset(id) {
      const generation = ++state.generation;
      try { const response = await life.api(`/api/datasets/${encodeURIComponent(id)}`); if (life.alive && generation === state.generation) renderDataset(response.dataset); }
      catch (error) { life.show(error.message, true); }
    }
    async function refresh(preferredId) {
      refreshButton.disabled = true;
      try {
        const response = await life.api("/api/datasets"); if (!life.alive) return;
        state.datasets = response.datasets || []; const prior = typeof preferredId === "string" ? preferredId : state.selected?.id;
        dataSelect.replaceChildren();
        for (const data of state.datasets) { const option = node("option", "", `${data.filename} · ${dateLabel(data.created_at)}`); option.value = data.id; dataSelect.append(option); }
        const selected = state.datasets.find((item) => item.id === prior) || state.datasets[0];
        if (selected) { dataSelect.value = selected.id; await chooseDataset(selected.id); }
        else {
          state.selected = null; create.disabled = true; previewTitle.textContent = "数据预览";
          previewCard.querySelector(".rs-example-source")?.remove();
          chartForm.hidden = true; chartEmpty.hidden = false;
          preview.replaceChildren(node("p", "rs-empty", "导入CSV后，可在这里检查字段、缺失值和原始记录。"));
          datasetInfo.textContent = ""; columnsInfo.replaceChildren(); x.replaceChildren(); yChoices.replaceChildren(); unitsArea.replaceChildren();
          chartOutput.hidden = true; chartOutput.replaceChildren();
          if (state.chartUrl) { life.revoke(state.chartUrl); state.chartUrl = null; }
        }
      } catch (error) { life.show(error.message, true); }
      finally { if (life.alive) refreshButton.disabled = false; }
    }
    life.on(refreshButton, "click", () => refresh()); life.on(dataSelect, "change", () => chooseDataset(dataSelect.value));
    life.on(uploadForm, "submit", async (event) => {
      event.preventDefault(); const selected = file.files?.[0]; if (!selected) return;
      if (selected.size > 5 * 1024 * 1024) { life.show("CSV文件不能超过5 MiB。", true); return; }
      upload.disabled = true;
      try { const body = new FormData(); body.append("file", selected); const response = await life.api("/api/datasets", { method: "POST", body }); if (life.alive) { life.show("CSV已导入，请核对字段与缺失值后绘图。"); file.value = ""; await refresh(response.dataset.id); } }
      catch (error) { life.show(error.message, true); }
      finally { if (life.alive) upload.disabled = false; }
    });
    async function generateChart(propagate = false) {
      if (!state.selected || state.chartPosting) return;
      const y = selectedY(); if (y.length < 1 || y.length > 6) { life.show("请选择1至6个数值字段。", true); return; }
      const datasetId = state.selected.id;
      state.chartPosting = true; create.disabled = true;
      try {
        const response = await life.api(`/api/datasets/${encodeURIComponent(datasetId)}/charts`, jsonOptions({ kind: chartKind.value,
          x: x.value, y, missing: missing.value, normalize: normalize.checked, title: title.value.trim(),
          units: Object.fromEntries(y.map((name) => [name, state.units[name] || ""])),
          yaw: Number(yaw.value), elevation: Number(elevation.value) }));
        const chart = response.chart; const imageUrl = await life.svgBlob(`/api/charts/${encodeURIComponent(chart.id)}/svg`);
        if (!life.alive) { life.revoke(imageUrl); return; }
        if (state.selected?.id !== datasetId) { life.revoke(imageUrl); life.show("数据集已切换，本次结果未替换当前视图。"); return; }
        if (state.chartUrl) life.revoke(state.chartUrl); state.chartUrl = imageUrl;
        const image = node("img", "rs-chart-image"); image.src = imageUrl; image.alt = chart.spec.title || "CSV数据图表";
        const downloads = node("div", "rs-actions");
        for (const [format, label] of [["svg", "下载SVG"], ["py", "下载Python复现脚本"], ["json", "下载数据与转换记录"]]) downloads.append(link(label, `/api/charts/${encodeURIComponent(chart.id)}/${format}`, `chart-${chart.id}.${format}`));
        const dropped = chart.transformations?.dropped_csv_rows || [];
        chartOutput.replaceChildren(node("h3", "rs-card-title", chart.spec.title), image, node("p", "rs-caption", chart.caption),
          node("p", "rs-muted rs-small", `忽略无效行：${dropped.length}${dropped.length ? `（CSV行号 ${dropped.join("、")}）` : ""}；归一化：${chart.transformations?.minmax_normalized ? "已执行" : "未执行"}。`), downloads);
        chartOutput.hidden = false; life.show("图表已按实际数据生成，转换记录与脚本可下载。");
      } catch (error) { life.show(error.message, true); if (propagate) throw error; }
      finally { state.chartPosting = false; if (life.alive) create.disabled = !state.selected; }
    }
    life.on(chartForm, "submit", (event) => { event.preventDefault(); generateChart(); });
    life.on(diagramForm, "submit", async (event) => {
      event.preventDefault(); makeDiagram.disabled = true;
      try {
        let spec; try { spec = { nodes: JSON.parse(nodes.value), edges: JSON.parse(edges.value || "[]") }; } catch { throw new Error("节点或连接不是有效JSON，请检查逗号、引号和数组格式。"); }
        if (!Array.isArray(spec.nodes) || !Array.isArray(spec.edges)) throw new Error("节点与连接均须为JSON数组。");
        const script = await life.api("/api/diagrams/script", jsonOptions(spec));
        const imageUrl = await life.svgBlob("/api/diagrams", jsonOptions(spec));
        if (!life.alive) { life.revoke(imageUrl); return; }
        if (state.diagramUrl) life.revoke(state.diagramUrl); if (state.diagramSpecUrl) life.revoke(state.diagramSpecUrl);
        if (state.diagramScriptUrl) life.revoke(state.diagramScriptUrl);
        state.diagramScriptUrl = life.blob(script.script, "text/x-python");
        state.diagramUrl = imageUrl; state.diagramSpecUrl = life.blob(JSON.stringify(spec, null, 2), "application/json");
        const image = node("img", "rs-chart-image"); image.src = imageUrl; image.alt = "根据输入节点和连接实际渲染的结构图";
        const downloads = node("div", "rs-actions"); downloads.append(link("下载SVG图形源文件", imageUrl, "architecture.svg"), link("下载节点与连接JSON", state.diagramSpecUrl, "architecture.json"), link("下载Python复现脚本", state.diagramScriptUrl, "architecture.py"));
        diagramOutput.replaceChildren(image, downloads); life.show("结构图已生成。");
      } catch (error) { life.show(error.message, true); }
      finally { if (life.alive) makeDiagram.disabled = false; }
    });
    async function prepareExample(example) {
      if (!life.alive) throw new Error("页面已切换，请重新打开示例。");
      await refresh(example.datasetId);
      if (!life.alive || state.selected?.id !== example.datasetId) throw new Error("示例数据集未能加载，请重试。");
      const spec = example.chart || {}, headers = state.selected.headers || [];
      if (!["line", "bar", "radar3d"].includes(spec.kind) || !headers.includes(spec.x) || !Array.isArray(spec.y) || !spec.y.length || spec.y.length > 6 || spec.y.some(name => !state.selected.columns.some(col => col.name === name && col.numeric_count > 0))) {
        throw new Error("示例图表字段与导入的数据不一致，请检查示例文件。");
      }
      chartKind.value = spec.kind; chartKind.dispatchEvent(new Event("change")); x.value = spec.x;
      for (const check of yChoices.querySelectorAll('input[type="checkbox"]')) check.checked = spec.y.includes(check.value);
      state.units = {...spec.units}; renderUnits(); title.value = spec.title || "";
      missing.value = ["reject", "drop"].includes(spec.missing) ? spec.missing : "reject"; normalize.checked = Boolean(spec.normalize);
      let source = previewCard.querySelector(".rs-example-source");
      if (!source) { source = node("p", "rs-example-source rs-muted rs-small"); previewCard.append(source); }
      source.replaceChildren(node("span", "", "数据来源："));
      const sourceLink = link(example.sourceLabel || "论文原始表格", example.sourceUrl); sourceLink.target = "_blank"; sourceLink.rel = "noopener noreferrer";
      source.append(sourceLink, node("span", "", "。数值为论文报告结果，本项目用于真实数据绘图演示，未重新训练或复现实验。"));
      life.show("真实示例数据与图表设置已加载。");
      if (example.generate) { await generateChart(true); if (life.alive) chartOutput.scrollIntoView({block: "start", behavior: "auto"}); }
    }
    const ready = refresh();
    return { ready, refresh, prepareExample, destroy: () => life.destroy() };
  }

  window.ResearchTasks = Object.freeze({ mount: mountTasks });
  window.ResearchData = Object.freeze({ mount: mountData });
})();
