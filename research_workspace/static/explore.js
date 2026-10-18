"use strict";

(() => {
  const mounted = new WeakMap();
  let graphSequence = 0;
  const node = (tag, className = "", text) => {
    const result = document.createElement(tag);
    if (className) result.className = className;
    if (text !== undefined) result.textContent = String(text);
    return result;
  };
  const button = (text, action, className = "ex-button") => {
    const result = node("button", className, text);
    result.type = "button";
    if (action) result.addEventListener("click", action);
    return result;
  };
  const json = (method, body) => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const date = (value) => {
    if (!value) return "尚未检查";
    const parsed = new Date(value);
    return Number.isNaN(parsed.valueOf()) ? value : parsed.toLocaleString("zh-CN", { hour12: false });
  };
  const safeLink = (value, label) => {
    try {
      const url = new URL(value);
      if (url.protocol !== "https:" && url.protocol !== "http:") return null;
      const result = node("a", "ex-link", label);
      result.href = url.href;
      result.target = "_blank";
      result.rel = "noopener noreferrer";
      return result;
    } catch { return null; }
  };
  function empty(container, title, text) {
    const box = node("div", "ex-empty");
    box.append(node("span", "ex-empty-symbol", "↗"), node("h3", "", title), node("p", "", text));
    container.replaceChildren(box);
  }
  function noticeBox() {
    const box = node("div", "ex-notice");
    box.hidden = true;
    box.setAttribute("role", "status");
    return { box, show(text, error = false) { window.ResearchNotices.show(text, error, box); } };
  }
  function heading(kicker, title, description) {
    const header = node("header", "ex-heading");
    const words = node("div");
    words.append(node("span", "ex-kicker", kicker), node("h2", "", title), node("p", "", description));
    header.append(words);
    return header;
  }
  function label(text, control) {
    const wrapper = node("label", "ex-field");
    wrapper.append(node("span", "", text), control);
    return wrapper;
  }
  function option(value, text) {
    const result = node("option", "", text);
    result.value = value;
    return result;
  }
  function evidenceList(container, evidence, onEvidence) {
    if (!evidence?.length) {
      container.append(node("p", "ex-muted", "暂无可直接定位的本地原文依据。"));
      return;
    }
    evidence.forEach((item) => {
      const card = node("div", "ex-evidence-card");
      card.append(node("p", "ex-evidence-text", item.text || "原文位置记录"));
      if (item.document_id && item.page && typeof onEvidence === "function") {
        card.append(button(`查看第 ${item.page} 页的原文依据 →`, () => onEvidence(item), "ex-text-button"));
      } else card.append(node("span", "ex-muted", "该记录没有可打开的本地页面。"));
      container.append(card);
    });
  }

  function mountDiscovery(container, { api, onEvidence, refreshLibrary, onResearchJob } = {}) {
    mounted.get(container)?.destroy();
    let alive = true;
    let searchSequence = 0;
    let recommendationSequence = 0;
    let subscriptionSequence = 0;
    let activeView = "search";
    let searchItems = [];
    let recommendedItems = [];
    let collaboration = {can_join:false,enabled:false,status:'disabled'};
    let collaborationBusy = false;
    let resultMeta = null;
    let subscriptions = [];
    const feedback = new Map();
    const shell = node("section", "explore-discovery ex-shell");
    const header = heading("DISCOVER & FOLLOW", "发现下一篇值得读的论文", "公开来源检索、独立保存的追踪订阅，以及基于你文献库的推荐。");
    const notice = noticeBox();
    const layout = node("div", "ex-discovery-layout");
    const main = node("div", "ex-discovery-main");
    const tabs = node("div", "ex-tabs");
    const searchTab = button("公开检索", () => { if (activeView !== "search") switchView("search"); }, "ex-tab is-active");
    const recommendTab = button("本地推荐", () => { if (activeView !== "recommendations") switchView("recommendations"); }, "ex-tab");
    searchTab.setAttribute("aria-pressed", "true");
    recommendTab.setAttribute("aria-pressed", "false");
    tabs.append(searchTab, recommendTab);

    const searchForm = node("form", "ex-search-form");
    const query = node("input", "ex-input");
    query.type = "search";
    query.placeholder = "公开论文标题、arXiv 编号或关键词";
    query.minLength = 2; query.maxLength = 300; query.required = true;
    query.setAttribute("aria-label", "公开检索关键词");
    const field = node("select", "ex-select");
    field.append(option("all", "标题与摘要"), option("title", "仅标题"));
    field.setAttribute("aria-label", "公开检索范围");
    const searchButton = node("button", "ex-button ex-primary", "检索 arXiv");
    searchButton.type = "submit";
    const searchRow = node("div", "ex-query-row");
    searchRow.append(query, field, searchButton);
    searchForm.append(searchRow, node("p", "ex-context-note", "检索时会将输入内容发送至 arXiv；不会自动发送已导入论文的全文。"));

    const recommendationForm = node("form", "ex-search-form");
    recommendationForm.hidden = true;
    const keywords = node("input", "ex-input");
    keywords.type = "search"; keywords.maxLength = 300;
    keywords.placeholder = "可选：输入你关注的研究关键词";
    keywords.setAttribute("aria-label", "本地推荐关键词");
    const recommendRefresh = node("button", "ex-button ex-primary", "更新推荐");
    recommendRefresh.type = "submit";
    const recommendationRow = node("div", "ex-query-row");
    recommendationRow.append(keywords, recommendRefresh);
    recommendationForm.append(recommendationRow, node("p", "ex-context-note", "仅对已检索并保存在本工作区的元数据排序，不会发起新的外部检索。"));
    const collaborative = node('section','ex-collaboration');
    const collaborativeTop = node('div','ex-collaboration-top');
    const collaborativeStatus = node('p','ex-muted','协同推荐默认关闭。');
    const collaborativeToggle = button('加入协同推荐', async () => {
      if (collaborationBusy || !collaboration.can_join || collaboration.status==='unavailable') return;
      collaborationBusy=true;renderCollaboration();
      try {
        const result=await api('/api/recommendations/collaboration',json('PUT',{enabled:!collaboration.enabled}));
        if(!alive)return;
        collaboration=result.collaboration;renderCollaboration();
        notice.show(collaboration.enabled ? '已加入协同推荐；仅使用公开论文标识与反馈，样本不足时保留个人推荐。' : '已退出协同推荐并清除本账号共享投影；个人反馈和原文保留。');
        await loadRecommendations();
      } catch(error){if(alive)notice.show(error.message,true);}
      finally{if(alive){collaborationBusy=false;renderCollaboration();}}
    },'ex-button ex-small');
    const collaborativeDetails=node('details','ex-collaboration-policy');
    collaborativeDetails.append(node('summary','','参与范围与隐私说明'),node('p','ex-fine-print',
      '加入后，仅将本账号已有及后续的公开 arXiv 论文标识、喜欢/不喜欢反馈用于本机协同计算，最多使用最近 500 篇论文的当前反馈；同一论文各版本按最新反馈合并。不会共享私有文献、原文、笔记、检索词、订阅或任务。'),
      node('p','ex-fine-print','只有至少 3 个相近兴趣参与账号提供足够一致的反馈时才调整排序；仅重排本空间已有公开候选，不提供其他账号身份或逐人反馈。退出后清除本账号共享投影，个人反馈保留。群体汇总仍可能反映参与者兴趣，不保证完全匿名。'));
    collaborativeTop.append(collaborativeStatus,collaborativeToggle);
    collaborative.append(collaborativeTop,node('p','ex-fine-print','主动加入后，仅共享公开论文标识和喜欢/不喜欢反馈，用于本机协同计算。私有文献及内容不共享；可随时退出并清除共享反馈。'),collaborativeDetails);recommendationForm.append(collaborative);

    function renderCollaboration(){
      const states={insufficient_profile:'共同兴趣依据不足，当前使用个人推荐。',insufficient_cohort:'相近兴趣群组不足，当前使用个人推荐。',
        no_eligible_signals:'候选论文的群体依据不足，当前使用个人推荐。',applied:'已结合群体反馈适度调整排序，具体依据见论文卡片。',
        unavailable:'协同服务暂不可用，当前仅提供个人推荐。'};
      collaborativeStatus.textContent=collaboration.status==='unavailable' ? states.unavailable : collaboration.enabled
        ? `协同推荐已加入 · 共享 ${collaboration.shared_feedback_count ?? 0} 篇公开论文的反馈。${states[collaboration.status] || ''}`
        : collaboration.can_join ? '协同推荐未加入；当前仅使用本账号资料和反馈。' : '本机默认空间不参与协同；登录独立账号后可主动加入。';
      collaborativeToggle.textContent=collaboration.enabled ? '退出并清除共享反馈' : '加入协同推荐';
      collaborativeToggle.disabled=collaborationBusy || !collaboration.can_join || collaboration.status==='unavailable';
    }
    renderCollaboration();
    const resultsHeader = node("div", "ex-results-header");
    const results = node("div", "ex-paper-list");
    results.setAttribute("aria-live", "polite");
    main.append(tabs, searchForm, recommendationForm, resultsHeader, results);

    const side = node("aside", "ex-subscription-panel");
    const sideHeader = node("div", "ex-section-header");
    sideHeader.append(node("h3", "", "持续追踪"), button("刷新", () => loadSubscriptions(), "ex-text-button"));
    side.append(sideHeader, node("p", "ex-muted", "保存订阅后，后台将按设置的间隔查询公开论文更新。"));
    const subscriptionForm = node("form", "ex-subscription-form");
    const subQuery = node("input", "ex-input");
    subQuery.placeholder = "要追踪的公开关键词"; subQuery.minLength = 2; subQuery.maxLength = 300; subQuery.required = true;
    const interval = node("input", "ex-input");
    interval.type = "number"; interval.min = "1"; interval.max = "720"; interval.value = "24"; interval.required = true;
    const subField = node("select", "ex-select");
    subField.append(option("all", "标题与摘要"), option("title", "仅标题"));
    const settings = node("div", "ex-subscription-settings");
    settings.append(label("间隔（小时）", interval), label("范围", subField));
    const subscribeButton = node("button", "ex-button ex-primary", "保存并启用订阅");
    subscribeButton.type = "submit";
    subscriptionForm.append(label("追踪内容", subQuery), settings, subscribeButton,
      node("p", "ex-fine-print", "同一查询在 24 小时内复用来源缓存；立即检查会明确标注缓存状态。"));
    const subscriptionList = node("div", "ex-subscription-list");
    side.append(subscriptionForm, subscriptionList);
    layout.append(main, side);
    shell.append(header, notice.box, layout);
    container.replaceChildren(shell);

    function switchView(view) {
      activeView = view;
      searchTab.classList.toggle("is-active", view === "search");
      recommendTab.classList.toggle("is-active", view === "recommendations");
      searchTab.setAttribute("aria-pressed", String(view === "search"));
      recommendTab.setAttribute("aria-pressed", String(view === "recommendations"));
      searchForm.hidden = view !== "search";
      recommendationForm.hidden = view !== "recommendations";
      renderResults();
    }

    function rememberFeedback(items) {
      items.forEach((paper) => {
        if (typeof paper.feedback === "number") feedback.set(paper.id, paper.feedback);
      });
    }

    async function importPaper(paper, trigger) {
      trigger.disabled = true;
      trigger.textContent = "正在下载并入库…";
      try {
        const result = await api(`/api/discover/import/${encodeURIComponent(paper.id)}`, { method: "POST" });
        if (!alive) return;
        Object.assign(paper, { document_id: result.document.id, fulltext_status: "downloaded", parse_status: result.document.current_run?.status });
        [searchItems, recommendedItems].flat().filter((item) => item.id === paper.id).forEach((item) => Object.assign(item, paper));
        notice.show(result.duplicate ? "这份原文已经在文献库中，保留了已有文件。" : "原文已入库，文字处理进度可在文献库查看。");
        renderResults();
        try { await refreshLibrary?.(); } catch { if (alive) notice.show("原文已入库，文献列表暂未刷新，可稍后重新加载。", true); }
      } catch (error) {
        if (alive) { notice.show(error.message, true); trigger.disabled = false; trigger.textContent = "下载原文入库"; }
      }
    }

    async function openPaper(paper, trigger) {
      trigger.disabled = true;
      try {
        const result = await api(`/api/documents/${encodeURIComponent(paper.document_id)}`);
        if (!alive) return;
        const run = result.document.available_run || result.document.current_run;
        onEvidence?.({ document_id: paper.document_id, run_id: run?.id, page: 1 });
      } catch (error) { if (alive) notice.show(error.message, true); }
      finally { trigger.disabled = false; }
    }

    async function setFeedback(paper, value, controls) {
      controls.forEach((control) => { control.disabled = true; });
      const updated = feedback.get(paper.id) === value ? 0 : value;
      try {
        const saved = await api(`/api/discover/feedback/${encodeURIComponent(paper.id)}`, json("POST", { value: updated }));
        if (!alive) return;
        feedback.set(paper.id, updated);
        notice.show(updated === 1 ? "已保存喜欢反馈，后续本地推荐会参考它。" : updated === -1 ? "已保存负反馈，后续本地推荐将降低相关条目的排序。" : "已清除这条反馈。");
        if(saved.collaboration_sync==='pending')notice.show('个人反馈已保存，协同更新暂未完成；下次更新推荐时会再次同步。',true);
        if (activeView === "recommendations") await loadRecommendations();
        else renderResults();
      } catch (error) { if (alive) { notice.show(error.message, true); controls.forEach((control) => { control.disabled = false; }); } }
    }

    function paperCard(paper) {
      const card = node("article", "ex-paper-card");
      const top = node("div", "ex-paper-top");
      top.append(node("span", "ex-source-label", "arXiv"), node("span", "ex-paper-id", paper.arxiv_id || paper.id));
      const status = paper.document_id ? "原文已入库" : "仅元数据";
      top.append(node("span", `ex-status ${paper.document_id ? "is-ready" : ""}`, status));
      const title = node("h3", "ex-paper-title", paper.title);
      const authors = Array.isArray(paper.authors) ? paper.authors.filter(Boolean).join(" · ") : "";
      const authorLine = node("p", "ex-paper-authors", authors || "来源未提供作者信息");
      const summary = node("details", "ex-paper-summary");
      summary.append(node("summary", "", "查看来源摘要"), node("p", "", paper.summary || "来源未提供摘要。"));
      card.append(top, title, authorLine, summary);
      if (paper.reasons?.length) {
        const reasons = node("div", "ex-recommendation-reasons");
        paper.reasons.forEach((reason) => reasons.append(node("p", "", reason)));
        card.append(reasons);
      }
      if (paper.code_links?.length) {
        const code = node("div", "ex-code-hints");
        code.append(node("span", "", paper.verified_code ? "开源代码关联已核对 · 未执行复现实验" : "代码线索 · 未确认关联或核验已过期"));
        paper.code_links.forEach((item) => {
          const link = safeLink(item.url, item.source_type ? "查看原文所列仓库 ↗" : "查看元数据所列仓库 ↗");
          if (link) code.append(link);
          if (item.source_evidence && typeof onEvidence === "function") code.append(button("核对原文代码链接", () => onEvidence(item.source_evidence), "ex-text-button"));
          if (item.check) code.append(node("p", "ex-muted", `${item.check.reason} 核验于 ${date(item.check.checked_at)}`));
        });
        const verify = button("核验代码关联", async () => {
          verify.disabled = true;
          try {
            const result = await api(`/api/discover/code/${encodeURIComponent(paper.id)}`, json("POST", {}));
            if (!alive) return;
            [searchItems, recommendedItems].flat().filter(item => item.id === paper.id).forEach(item => Object.assign(item, result.paper));
            if (activeView === "recommendations") await loadRecommendations(); else renderResults();
            notice.show(result.paper.verified_code ? "开源关联已核对，尚未执行复现实验。" : "核验已记录，请查看仓库关联说明。");
          } catch (error) { if (alive) notice.show(error.message, true); }
          finally { if (alive) verify.disabled = false; }
        }, "ex-button ex-small");
        code.append(verify, node("p", "ex-muted", "仅访问公开GitHub接口，最多检查两个仓库；不会下载或运行仓库代码。"));
        card.append(code);
      }
      const provenance = node("div", "ex-paper-provenance");
      if (paper.published) provenance.append(node("span", "", `首次发布 ${String(paper.published).slice(0, 10)}`));
      provenance.append(node("span", "", `检索于 ${date(paper.retrieved_at)}`));
      card.append(provenance);
      const bottom = node("div", "ex-paper-bottom");
      const links = node("div", "ex-paper-links");
      const source = safeLink(paper.source_url, "核对来源 ↗");
      if (source) links.append(source);
      if (paper.document_id && typeof onEvidence === "function") {
        const read = button("阅读原文", () => openPaper(paper, read), "ex-button ex-small");
        links.append(read);
      } else if (paper.pdf_url && !paper.document_id) {
        const download = button("下载原文入库", () => importPaper(paper, download), "ex-button ex-small");
        links.append(download);
      } else if (!paper.document_id) links.append(node("span", "ex-muted", "暂无已核验全文链接"));
      const choices = node("div", "ex-feedback");
      const controls = [];
      [[1, "喜欢"], [-1, "不感兴趣"]].forEach(([value, text]) => {
        const control = button(text, () => setFeedback(paper, value, controls), "ex-feedback-button");
        control.classList.toggle("is-active", feedback.get(paper.id) === value);
        control.setAttribute("aria-pressed", String(feedback.get(paper.id) === value));
        controls.push(control); choices.append(control);
      });
      bottom.append(links, choices);
      card.append(bottom);
      if (paper.pdf_url && onResearchJob) {
        const work = node("details", "ex-paper-summary");
        work.append(node("summary", "", "下载、解析并回答问题"));
        const form=node("form","ex-search-form"), question=node("textarea","ex-input");
        question.required=true; question.maxLength=4000; question.rows=2; question.setAttribute("aria-label",`关于 ${paper.title} 的研究问题`);
        question.placeholder="填写问题，后台会保存原文、完成解析并用原文引用回答。";
        const start=node("button","ex-button ex-primary","开始下载与问答"); start.type="submit";
        form.append(question,node("p","ex-muted","仅处理这篇已选论文；问答会发送原文片段至已配置模型并消耗额度。"),start);
        form.addEventListener("submit",async event => {
          event.preventDefault(); start.disabled=true;
          try {
            const created=await api('/api/research/tasks',json('POST',{kind:'discover_qa',paper_id:paper.id,question:question.value.trim()}));
            if(alive) await onResearchJob(created.job.id);
          } catch(error) {if(alive) notice.show(error.message,true);}
          finally {if(alive) start.disabled=false;}
        });
        work.append(form); card.append(work);
      }
      return card;
    }

    function renderResults() {
      if (!alive) return;
      const items = activeView === "search" ? searchItems : recommendedItems;
      resultsHeader.replaceChildren();
      if (activeView === "search" && resultMeta) {
        resultsHeader.append(node("span", "", `${items.length} 条来源记录`), node("span", "ex-muted", `${resultMeta.cached ? "来源缓存 · " : ""}${date(resultMeta.retrieved_at)}`));
      } else if (activeView === "recommendations") resultsHeader.append(node("span", "", `${items.length} 条本地推荐`), node("span", "ex-muted", collaboration.status==='applied' ? '个人关联与群体反馈排序' : '标题关联与持久反馈排序'));
      if (!items.length) {
        if (activeView === "recommendations") empty(results, "还没有可推荐的来源记录", "先检索你关注的公开论文。结果保存在本工作区后，会结合本地文献标题和反馈进行排序。");
        else if (resultMeta) empty(results, "没有找到匹配的公开论文", "尝试更短的英文标题、研究术语或准确的 arXiv 编号。");
        else empty(results, "从一个研究问题出发", "输入公开标题或关键词后开始检索。找到的元数据需要另行下载原文，才能进入全文阅读和问答。");
        return;
      }
      results.replaceChildren(...items.map(paperCard));
    }

    async function loadRecommendations() {
      const request = ++recommendationSequence;
      recommendRefresh.disabled = true;
      try {
        const result = await api(`/api/recommendations?${new URLSearchParams({ q: keywords.value.trim() })}`);
        if (!alive || request !== recommendationSequence) return;
        recommendedItems = result.items || [];
        collaboration=result.collaboration || {can_join:false,enabled:false,status:'disabled'};
        renderCollaboration();
        rememberFeedback(recommendedItems);
        if (activeView === "recommendations") renderResults();
      } catch (error) { if (alive && request === recommendationSequence) notice.show(error.message, true); }
      finally { if (alive && request === recommendationSequence) recommendRefresh.disabled = false; }
    }

    function renderSubscriptions() {
      subscriptionList.replaceChildren();
      if (!subscriptions.length) {
        subscriptionList.append(node("p", "ex-subscription-empty", "尚未设置追踪订阅。"));
        return;
      }
      subscriptions.forEach((subscription) => {
        const card = node("article", "ex-subscription-card");
        const title = node("div", "ex-subscription-title");
        title.append(node("h4", "", subscription.query), node("span", `ex-status ${subscription.enabled ? "is-ready" : ""}`, subscription.enabled ? "已启用" : "已暂停"));
        card.append(title, node("p", "ex-muted", `每 ${subscription.interval_hours} 小时 · ${subscription.field === "title" ? "标题" : "标题与摘要"}`));
        const timing = node("div", "ex-subscription-times");
        timing.append(node("span", "", `上次检查：${date(subscription.last_checked_at)}`));
        if (subscription.enabled) timing.append(node("span", "", `下次检查：${date(subscription.next_check_at)}`));
        if (subscription.status === "checking") timing.append(node("span", "", "正在检查公开来源…"));
        card.append(timing);
        if (subscription.last_error) card.append(node("p", "ex-inline-error", subscription.last_error));
        const actions = node("div", "ex-subscription-actions");
        const check = button("立即检查", async () => {
          check.disabled = true; check.textContent = "正在检查…";
          try {
            const result = await api(`/api/subscriptions/${encodeURIComponent(subscription.id)}/check`, { method: "POST" });
            if (!alive) return;
            if (result.error) notice.show(result.error, true);
            else {
              searchSequence += 1;
              searchButton.disabled = false;
              searchItems = result.items || [];
              resultMeta = result;
              rememberFeedback(searchItems);
              query.value = subscription.query;
              field.value = subscription.field;
              switchView("search");
              notice.show(`检查完成，新增 ${result.new_items?.length || 0} 条来源记录${result.cached ? "；本次使用来源缓存" : ""}。`);
            }
            await loadSubscriptions();
            loadRecommendations();
          } catch (error) { if (alive) notice.show(error.message, true); }
          finally { check.disabled = false; check.textContent = "立即检查"; }
        }, "ex-button ex-small");
        const toggle = button(subscription.enabled ? "暂停" : "启用", async () => {
          toggle.disabled = true;
          try {
            await api(`/api/subscriptions/${encodeURIComponent(subscription.id)}`, json("PATCH", { enabled: !subscription.enabled }));
            if (alive) await loadSubscriptions();
          } catch (error) { if (alive) { notice.show(error.message, true); toggle.disabled = false; } }
        }, "ex-text-button");
        actions.append(check, toggle); card.append(actions); subscriptionList.append(card);
      });
    }

    async function loadSubscriptions() {
      const request = ++subscriptionSequence;
      try {
        const result = await api("/api/subscriptions");
        if (!alive || request !== subscriptionSequence) return;
        subscriptions = result.subscriptions || [];
        renderSubscriptions();
      } catch (error) { if (alive && request === subscriptionSequence) notice.show(error.message, true); }
    }

    searchForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (!query.value.trim()) return;
      const request = ++searchSequence;
      searchButton.disabled = true;
      notice.show("");
      empty(results, "正在检索公开论文", "正在查询 arXiv；连接较慢时可以保留关键词后重试。");
      try {
        const result = await api(`/api/discover/search?${new URLSearchParams({ q: query.value.trim(), field: field.value })}`);
        if (!alive || request !== searchSequence) return;
        searchItems = result.items || [];
        resultMeta = result;
        rememberFeedback(searchItems);
        subQuery.value = query.value.trim();
        subField.value = field.value;
        renderResults();
        loadRecommendations();
      } catch (error) {
        if (alive && request === searchSequence) { renderResults(); notice.show(error.message, true); }
      } finally { if (alive && request === searchSequence) searchButton.disabled = false; }
    });
    query.addEventListener("input", () => {
      if (searchButton.disabled) { searchSequence += 1; searchButton.disabled = false; renderResults(); }
    });
    field.addEventListener("change", () => {
      if (searchButton.disabled) { searchSequence += 1; searchButton.disabled = false; renderResults(); }
    });
    recommendationForm.addEventListener("submit", (event) => { event.preventDefault(); loadRecommendations(); });
    subscriptionForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      subscribeButton.disabled = true;
      try {
        const result = await api("/api/subscriptions", json("POST", { query: subQuery.value.trim(), field: subField.value, interval_hours: Number(interval.value) }));
        if (!alive) return;
        notice.show(result.subscription.enabled ? "订阅已保存并启用，后台将按间隔检查公开来源。" : "该订阅已经存在，目前已暂停，可在订阅卡片中启用。");
        await loadSubscriptions();
      } catch (error) { if (alive) notice.show(error.message, true); }
      finally { if (alive) subscribeButton.disabled = false; }
    });
    renderResults();
    const controller = {
      refresh: () => Promise.allSettled([loadRecommendations(), loadSubscriptions()]),
      destroy() { alive = false; searchSequence += 1; recommendationSequence += 1; subscriptionSequence += 1; },
    };
    mounted.set(container, controller);
    controller.ready = controller.refresh();
    return controller;
  }

  function mountGraph(container, { api, onEvidence, selectedIds } = {}) {
    mounted.get(container)?.destroy();
    let alive = true;
    let requestSequence = 0;
    let graph = null;
    let selectedNode = null;
    let selectedEdge = null;
    let zoom = 1;
    let pan = { x: 0, y: 0 };
    let drag = null;
    const uid = `ex-graph-${++graphSequence}`;
    const shell = node("section", "explore-graph ex-shell");
    const header = heading("CITATION NETWORK", "把论文放回引用关系中", "连线只来自可核验的参考书目。点击节点或连线，查看原文依据。");
    const rebuild = button("更新引用图谱", () => refresh(), "ex-button ex-primary");
    header.append(rebuild);
    const notice = noticeBox();
    const scope = node("p", "ex-graph-scope", "正在读取当前文献集合…");
    const layout = node("div", "ex-graph-layout");
    const visual = node("div", "ex-graph-visual");
    const toolbar = node("div", "ex-graph-toolbar");
    const zoomLabel = node("span", "", "100%");
    const smaller = button("−", () => setZoom(zoom / 1.25), "ex-zoom-button");
    const larger = button("+", () => setZoom(zoom * 1.25), "ex-zoom-button");
    smaller.setAttribute("aria-label", "缩小图谱"); larger.setAttribute("aria-label", "放大图谱");
    toolbar.append(smaller, zoomLabel, larger, button("复位", () => { zoom = 1; pan = { x: 0, y: 0 }; transform(); }, "ex-text-button"));
    const graphHost = node("div", "ex-graph-host");
    const legend = node("div", "ex-graph-legend");
    legend.append(node("span", "ex-legend-local", "本地原文"), node("span", "ex-legend-external", "外部书目"), node("span", "", "节点大小：局域 PageRank"));
    visual.append(toolbar, graphHost, legend);
    const aside = node("aside", "ex-graph-details");
    const detail = node("div", "ex-graph-selection");
    const method = node("details", "ex-method");
    method.open = true;
    method.append(node("summary", "", "如何理解这个图谱"));
    const methodText = node("div");
    method.append(methodText);
    const unresolved = node("details", "ex-unresolved");
    aside.append(detail, method, unresolved);
    layout.append(visual, aside);
    shell.append(header, notice.box, scope, layout);
    container.replaceChildren(shell);
    let svg = null;
    let viewport = null;
    let graphText = [];
    let typographyKey = "";
    let typographyFrame = 0;
    const svgNode = (tag, attributes = {}, text) => {
      const result = document.createElementNS("http://www.w3.org/2000/svg", tag);
      Object.entries(attributes).forEach(([name, value]) => result.setAttribute(name, String(value)));
      if (text !== undefined) result.textContent = text;
      return result;
    };

    function updateTypography() {
      if (!alive || !svg?.isConnected || !viewport) return;
      const screenWidth = svg.getBoundingClientRect().width;
      // Read the stable root viewBox scale and apply our current zoom explicitly.
      // Some renderers expose the previous frame's group CTM just after setAttribute.
      const matrix = svg.getScreenCTM();
      const screenScale = matrix && Math.hypot(matrix.a, matrix.b) * zoom;
      if (!screenScale || !Number.isFinite(screenScale) || screenWidth <= 0) return;
      const key = `${screenScale.toFixed(6)}:${Math.round(screenWidth)}`;
      if (typographyKey === key) return;
      typographyKey = key;
      const maximumLabelWidth = Math.max(72, Math.min(240, screenWidth * .3));
      for (const entry of graphText) {
        // The viewBox and interactive zoom must not shrink text below readable screen pixels.
        entry.label.style.fontSize = `${14.25 / screenScale}px`;
        entry.label.style.strokeWidth = `${3 / screenScale}px`;
        entry.rank.style.fontSize = `${15.25 / screenScale}px`;
        const rankBox = entry.rank.getBBox();
        entry.label.setAttribute("y", String(Math.max(entry.radius, rankBox.y + rankBox.height) + 18 / screenScale));
        entry.label.textContent = entry.title;
        if (entry.label.getComputedTextLength() * screenScale <= maximumLabelWidth) continue;
        const characters = Array.from(entry.title);
        let low = 0, high = characters.length;
        while (low < high) {
          const middle = Math.ceil((low + high) / 2);
          entry.label.textContent = `${characters.slice(0, middle).join("")}…`;
          if (entry.label.getComputedTextLength() * screenScale <= maximumLabelWidth) low = middle;
          else high = middle - 1;
        }
        entry.label.textContent = `${characters.slice(0, low).join("")}…`;
      }
      // Keep readable type without covering a neighbouring node or another label.
      // These boxes share viewport coordinates; panning does not change overlap.
      const prioritized = [...graphText].sort((left, right) => {
        const priority = (entry) => entry.id === selectedNode ? 2
          : selectedEdge && [selectedEdge.source, selectedEdge.target].includes(entry.id) ? 1 : 0;
        return priority(right) - priority(left);
      });
      const bounds = (element, entry) => {
        const box = element.getBBox();
        return { left: entry.x + box.x, top: entry.y + box.y,
          right: entry.x + box.x + box.width, bottom: entry.y + box.y + box.height };
      };
      const gap = 2 / screenScale;
      const overlaps = (left, right) => left.left < right.right + gap && left.right > right.left - gap
        && left.top < right.bottom + gap && left.bottom > right.top - gap;
      const visibleRanks = [];
      for (const entry of prioritized) {
        const box = bounds(entry.rank, entry);
        const hidden = visibleRanks.some((other) => overlaps(box, other));
        entry.rank.style.visibility = hidden ? "hidden" : "visible";
        if (!hidden) visibleRanks.push(box);
      }
      const nodeBounds = graphText.map((entry) => ({ left: entry.x - entry.radius, top: entry.y - entry.radius,
        right: entry.x + entry.radius, bottom: entry.y + entry.radius }));
      const visibleLabels = [];
      for (const entry of prioritized) {
        const box = bounds(entry.label, entry);
        const hidden = [...nodeBounds, ...visibleRanks, ...visibleLabels].some((other) => overlaps(box, other));
        entry.label.style.visibility = hidden ? "hidden" : "visible";
        if (!hidden) visibleLabels.push(box);
      }
    }
    function scheduleTypography() {
      if (!alive || typographyFrame) return;
      typographyFrame = requestAnimationFrame(() => { typographyFrame = 0; updateTypography(); });
    }
    const graphResizeObserver = typeof ResizeObserver === "function" ? new ResizeObserver(scheduleTypography) : null;
    graphResizeObserver?.observe(graphHost);
    if (!graphResizeObserver) window.addEventListener("resize", scheduleTypography);

    function transform() {
      if (!alive) return;
      viewport?.setAttribute("transform", `translate(${pan.x} ${pan.y}) scale(${zoom})`);
      zoomLabel.textContent = `${Math.round(zoom * 100)}%`;
      smaller.disabled = !viewport || zoom <= .4;
      larger.disabled = !viewport || zoom >= 4;
      updateTypography();
      scheduleTypography();
    }
    function setZoom(next, point = { x: 500, y: 350 }) {
      if (!viewport) return;
      next = Math.min(4, Math.max(.4, next));
      pan = { x: point.x - (point.x - pan.x) * next / zoom, y: point.y - (point.y - pan.y) * next / zoom };
      zoom = next;
      transform();
    }
    function pointer(event) {
      const point = svg.createSVGPoint();
      point.x = event.clientX; point.y = event.clientY;
      return point.matrixTransform(svg.getScreenCTM().inverse());
    }
    function highlightSelection() {
      svg?.querySelectorAll(".ex-network-node").forEach((item) => {
        const active = item.dataset.nodeId === selectedNode || (selectedEdge && [selectedEdge.source, selectedEdge.target].includes(item.dataset.nodeId));
        item.classList.toggle("is-selected", Boolean(active));
        item.setAttribute("aria-pressed", String(Boolean(active)));
      });
      svg?.querySelectorAll(".ex-network-edge").forEach((item) => item.classList.toggle("is-selected", item.dataset.edgeId === selectedEdge?.id));
      typographyKey = "";
      updateTypography();
      scheduleTypography();
    }
    function openEdge(edge) {
      selectedEdge = edge; selectedNode = null;
      highlightSelection(); renderDetail();
    }
    function openNode(identifier) {
      selectedNode = identifier; selectedEdge = null;
      highlightSelection(); renderDetail();
    }
    function renderDetail() {
      detail.replaceChildren();
      if (!graph) return;
      const nodes = new Map(graph.nodes.map((item) => [item.id, item]));
      if (selectedEdge) {
        detail.append(node("span", "ex-kicker", "CITATION EVIDENCE"), node("h3", "", "这条引用如何确认"));
        detail.append(node("p", "ex-edge-names", `${nodes.get(selectedEdge.source)?.title || selectedEdge.source} → ${nodes.get(selectedEdge.target)?.title || selectedEdge.target}`));
        const methods = { arxiv_identifier: "参考书目中的 arXiv 标识", doi: "参考书目中的 DOI", exact_title_in_reference: "参考书目中的完整标题" };
        detail.append(node("p", "ex-muted", `匹配依据：${methods[selectedEdge.match_method] || selectedEdge.match_method}`));
        evidenceList(detail, selectedEdge.evidence, onEvidence);
      } else if (selectedNode) {
        const paper = nodes.get(selectedNode);
        if (!paper) return;
        detail.append(node("span", "ex-kicker", paper.kind === "external_metadata" ? "EXTERNAL METADATA" : "LOCAL DOCUMENT"), node("h3", "", paper.title));
        detail.append(node("p", "ex-muted", paper.kind === "external_metadata" ? "此节点只有公开书目记录，未提供本地全文。" : paper.fulltext_available ? "本地文献，已有可用文字解析。" : "本地文献，尚无可用文字解析。"));
        const numbers = node("div", "ex-node-values");
        numbers.append(node("span", "", `局域 PageRank ${Number(paper.pagerank || 0).toFixed(4)}`), node("span", "", `被引 ${paper.in_degree || 0} · 引用 ${paper.out_degree || 0}`));
        detail.append(numbers);
        (paper.metadata_sources || []).forEach((source) => {
          const link = safeLink(source.source_url, "核对公开书目来源 ↗");
          if (link) detail.append(link);
        });
        if (paper.evidence?.length) {
          const original = node("details", "ex-node-evidence");
          original.append(node("summary", "", "查看文献身份依据"));
          evidenceList(original, paper.evidence, onEvidence);
          detail.append(original);
        }
        const connections = graph.edges.filter((edge) => edge.source === paper.id || edge.target === paper.id);
        detail.append(node("h4", "ex-detail-subheading", `相关引用 · ${connections.length}`));
        connections.forEach((edge) => {
          const outgoing = edge.source === paper.id;
          detail.append(button(`${outgoing ? "引用 → " : "被引自 ← "}${nodes.get(outgoing ? edge.target : edge.source)?.title || "文献"}`, () => openEdge(edge), "ex-edge-item"));
        });
        if (!connections.length) detail.append(node("p", "ex-muted", "当前集合中尚无已确认引用关系，不能据此认定其没有学术影响。"));
      } else {
        detail.append(node("span", "ex-kicker", "FOLLOW THE EVIDENCE"), node("h3", "", "选择一个节点或一条连线"), node("p", "ex-muted", "这里会显示文献、引用方向和可定位的原文依据。拖动空白区域平移，使用滚轮或按钮缩放。"));
      }
    }

    function drawGraph() {
      graphText = [];
      typographyKey = "";
      if (!graph.nodes.length) {
        viewport = null; svg = null; transform();
        empty(graphHost, "尚无可构建图谱的文献", "先导入并解析论文，再更新引用图谱。没有依据的关系不会被自动补成连线。");
        renderDetail(); return;
      }
      svg = svgNode("svg", { viewBox: "0 0 1000 700", class: "ex-network-svg", "aria-label": "可交互的局域论文引用图谱" });
      const defs = svgNode("defs");
      const marker = svgNode("marker", { id: `${uid}-arrow`, markerWidth: 7, markerHeight: 7, refX: 6, refY: 3.5, orient: "auto", markerUnits: "userSpaceOnUse" });
      marker.append(svgNode("path", { d: "M0 0 L7 3.5 L0 7 Z", fill: "#6b9798" }));
      defs.append(marker); svg.append(defs);
      viewport = svgNode("g"); svg.append(viewport);
      const positions = new Map();
      const maxScore = Math.max(...graph.nodes.map((item) => Number(item.pagerank || 0)), .001);
      const count = graph.nodes.length;
      let ring = 1; let ringUsed = 0; let ringSize = 12;
      graph.nodes.forEach((paper, index) => {
        let x = 500; let y = 350;
        if (index > 0) {
          if (ringUsed >= ringSize) { ring += 1; ringUsed = 0; ringSize = 12 + (ring - 1) * 10; }
          const remaining = count - index + ringUsed;
          const size = Math.min(ringSize, remaining);
          const angle = ringUsed / size * Math.PI * 2 - Math.PI / 2 + (ring % 2 ? 0 : .13);
          const radius = ring === 1 ? Math.min(200, 95 + count * 5) : 200 + (ring - 1) * 145;
          x += Math.cos(angle) * radius * 1.15; y += Math.sin(angle) * radius * .9;
          ringUsed += 1;
        }
        positions.set(paper.id, { x, y, radius: 13 + Math.sqrt(Number(paper.pagerank || 0) / maxScore) * 21 });
      });
      if (ring > 2) { zoom = Math.max(.4, 300 / (200 + (ring - 1) * 145)); pan = { x: 500 * (1 - zoom), y: 350 * (1 - zoom) }; }
      graph.edges.forEach((edge) => {
        const source = positions.get(edge.source); const target = positions.get(edge.target);
        if (!source || !target) return;
        const dx = target.x - source.x; const dy = target.y - source.y;
        const distance = Math.hypot(dx, dy) || 1;
        const x0 = source.x + dx / distance * source.radius; const y0 = source.y + dy / distance * source.radius;
        const x1 = target.x - dx / distance * (target.radius + 4); const y1 = target.y - dy / distance * (target.radius + 4);
        const mx = (x0 + x1) / 2 - dy / distance * 12; const my = (y0 + y1) / 2 + dx / distance * 12;
        const d = `M ${x0} ${y0} Q ${mx} ${my} ${x1} ${y1}`;
        const group = svgNode("g", { class: "ex-network-edge", tabindex: 0, role: "button", "aria-label": "查看引用依据" });
        group.dataset.edgeId = edge.id;
        group.append(svgNode("path", { d, class: "ex-edge-hit" }), svgNode("path", { d, class: "ex-edge-line", "marker-end": `url(#${uid}-arrow)` }));
        group.addEventListener("click", () => openEdge(edge));
        group.addEventListener("keydown", (event) => { if (["Enter", " "].includes(event.key)) { event.preventDefault(); openEdge(edge); } });
        viewport.append(group);
      });
      graph.nodes.forEach((paper, index) => {
        const point = positions.get(paper.id);
        const group = svgNode("g", { class: `ex-network-node${paper.kind === "external_metadata" ? " is-external" : ""}`, transform: `translate(${point.x} ${point.y})`, tabindex: 0, role: "button", "aria-label": `${paper.title}，局域排名 ${paper.rank || index + 1}，查看文献与引用依据` });
        group.dataset.nodeId = paper.id;
        const rank = svgNode("text", { class: "ex-node-rank", "text-anchor": "middle", dy: ".35em" }, paper.rank || index + 1);
        group.append(svgNode("title", {}, paper.title), svgNode("circle", { r: point.radius, class: "ex-node-circle" }), rank);
        const title = String(paper.title || "");
        const label = svgNode("text", { class: "ex-node-label", "text-anchor": "middle", y: point.radius + 19 }, title);
        group.append(label);
        graphText.push({ label, rank, title, id: paper.id, x: point.x, y: point.y, radius: point.radius });
        group.addEventListener("click", () => openNode(paper.id));
        group.addEventListener("keydown", (event) => { if (["Enter", " "].includes(event.key)) { event.preventDefault(); openNode(paper.id); } });
        viewport.append(group);
      });
      svg.addEventListener("wheel", (event) => { event.preventDefault(); setZoom(zoom * (event.deltaY < 0 ? 1.1 : 1 / 1.1), pointer(event)); }, { passive: false });
      svg.addEventListener("pointerdown", (event) => {
        if (event.button !== 0 || event.target.closest(".ex-network-node,.ex-network-edge")) return;
        drag = { point: pointer(event), origin: { ...pan }, pointerId: event.pointerId };
        svg.setPointerCapture(event.pointerId); svg.classList.add("is-dragging");
      });
      svg.addEventListener("pointermove", (event) => {
        if (!drag || drag.pointerId !== event.pointerId) return;
        const point = pointer(event); pan = { x: drag.origin.x + point.x - drag.point.x, y: drag.origin.y + point.y - drag.point.y }; transform();
      });
      const stopDrag = (event) => { if (drag?.pointerId === event.pointerId) { drag = null; svg.classList.remove("is-dragging"); if (svg.hasPointerCapture(event.pointerId)) svg.releasePointerCapture(event.pointerId); } };
      svg.addEventListener("pointerup", stopDrag); svg.addEventListener("pointercancel", stopDrag);
      graphHost.replaceChildren(svg); transform(); renderDetail();
    }

    async function refresh() {
      const request = ++requestSequence;
      rebuild.disabled = true;
      rebuild.textContent = "正在核对引用…";
      notice.show("");
      try {
        const ids = typeof selectedIds === "function" ? selectedIds() : selectedIds;
        const params = new URLSearchParams();
        (Array.isArray(ids) ? ids : []).forEach((id) => params.append("document_id", id));
        const result = await api(`/api/graph${params.size ? `?${params}` : ""}`);
        if (!alive || request !== requestSequence) return;
        graph = result;
        graph.nodes = graph.nodes || []; graph.edges = graph.edges || [];
        selectedNode = null; selectedEdge = null; zoom = 1; pan = { x: 0, y: 0 };
        const localCount = graph.nodes.filter((item) => item.kind === "local_document").length;
        const externalCount = graph.nodes.length - localCount;
        scope.textContent = `${localCount} 篇本地文献 · ${externalCount} 个外部书目 · ${graph.edges.length} 条已确认引用 · ${date(graph.generated_at)}`;
        methodText.replaceChildren(node("p", "", graph.method?.direction || "A → B 表示 A 的参考书目引用 B。"), node("p", "", graph.method?.explanation || "中心性只反映当前文献集合。"), node("p", "", "圈内数字为当前图的 PageRank 排名。布局仅为便于阅读，不表示时间、研究质量或主题距离。"));
        unresolved.replaceChildren(node("summary", "", `尚未确认的关系 · ${graph.unresolved_count || 0}`));
        if ((graph.unresolved_count || 0) > (graph.unresolved?.length || 0)) unresolved.append(node("p", "ex-muted", `仅展示前 ${graph.unresolved.length} 条未确认记录。`));
        (graph.unresolved || []).forEach((item) => {
          const box = node("div", "ex-unresolved-item");
          const document = graph.nodes.find((candidate) => candidate.id === item.document_id);
          if (document) box.append(node("strong", "", document.title));
          box.append(node("p", "", item.reason));
          if (item.reference_text) box.append(node("p", "ex-reference-text", item.reference_text));
          if (item.evidence?.length) evidenceList(box, item.evidence, onEvidence);
          unresolved.append(box);
        });
        drawGraph();
      } catch (error) {
        if (alive && request === requestSequence) {
          notice.show(error.message, true);
          if (!graph) { scope.textContent = "引用图谱尚未加载。"; empty(graphHost, "暂时无法构建图谱", "保留了当前文献，请稍后点击更新重试。"); }
        }
      } finally { if (alive && request === requestSequence) { rebuild.disabled = false; rebuild.textContent = "更新引用图谱"; } }
    }
    empty(graphHost, "正在核对参考书目", "只对有原文依据的引用关系连线。");
    const controller = { refresh, destroy() {
      alive = false; requestSequence += 1; drag = null;
      graphResizeObserver?.disconnect();
      if (!graphResizeObserver) window.removeEventListener("resize", scheduleTypography);
      cancelAnimationFrame(typographyFrame);
      typographyFrame = 0; graphText = [];
    } };
    mounted.set(container, controller);
    controller.ready = refresh();
    return controller;
  }

  window.ResearchExplore = Object.freeze({ mountDiscovery, mountGraph });
})();
