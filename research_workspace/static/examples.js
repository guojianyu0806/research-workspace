"use strict";

/* Curated, source-linked inputs. Research output is always produced by the real service. */
(() => {
  const node = (tag, className = "", text) => {
    const result = document.createElement(tag); result.className = className;
    if (text !== undefined) result.textContent = String(text);
    return result;
  };
  const externalUrl = value => {
    const url = new URL(value, location.origin);
    if (url.protocol !== "https:") throw new Error("示例来源链接无效。");
    return url.href;
  };
  function sourceLink(label, href) {
    const link = node("a", "examples-link", label); link.href = externalUrl(href);
    link.target = "_blank"; link.rel = "noopener noreferrer"; return link;
  }
  const normalizeTitle = value => String(value || "").normalize("NFKC").toLowerCase().replace(/[^\p{L}\p{N}]/gu, "");
  const normalizeFile = value => String(value || "").normalize("NFKC").toLowerCase().split(/[\\/]/).pop().trim();
  function paperState(paper, documents) {
    const named = documents.filter(doc => !doc.archived_at && (normalizeFile(doc.filename) === normalizeFile(paper.filename)
      || normalizeTitle(doc.title) === normalizeTitle(paper.title)));
    const candidates = paper.sha256 ? documents.filter(doc => !doc.archived_at && doc.sha256 === paper.sha256) : named.filter(doc => {
      const version = `${doc.filename || ""} ${doc.title || ""}`.match(/\b\d{4}\.\d{4,5}v\d+\b/gi);
      return !version?.length || version.every(id => id.toLowerCase() === paper.arxiv_id.toLowerCase());
    });
    const active = candidates.filter(doc => doc.is_current_version !== false);
    const exact = active.filter(doc => normalizeFile(doc.filename) === normalizeFile(paper.filename));
    const matches = exact.length ? exact : active;
    if (matches.length > 1) return {message: "检测到多份同名资料，请先在文献管理中核对"};
    const document = matches[0];
    if (!document) return {message: candidates.length ? "仅存在非活动版本，请先在文献管理中核对" : named.length ? "已有同名资料，但文件与示例固定版本不一致" : "当前空间未导入"};
    if (!document.available_run) return {message: "已导入，尚无可用解析文字"};
    return {document, message: `已就绪 · ${document.page_count} 页`};
  }
  function mount({api, activate, onEvidence}) {
    const trigger = document.getElementById("examplesButton"); if (!trigger) return;
    const dialog = node("dialog", "examples-dialog"); dialog.id = "examplesDialog";
    dialog.setAttribute("aria-labelledby", "examplesHeading");
    const header = node("div", "examples-heading"), headingText = node("div");
    const heading = node("h2", "", "演示示例"); heading.id = "examplesHeading";
    headingText.append(node("span", "examples-eyebrow", "REAL PAPERS · REAL DATA"), heading);
    const close = node("button", "icon-button", "×"); close.type = "button"; close.setAttribute("aria-label", "关闭演示示例");
    header.append(headingText, close);
    const notice = node("p", "examples-notice"); notice.setAttribute("role", "status"); notice.hidden = true;
    const body = node("div", "examples-body");
    dialog.append(header, notice, body); document.body.append(dialog);
    let catalog = null, documents = [], busy = false, loadRevision = 0, actionRevision = 0;
    const show = (text, error = false) => { notice.textContent = text; notice.hidden = !text; notice.classList.toggle("is-error", error); };
    const setBusy = value => {
      busy = value; dialog.setAttribute("aria-busy", String(value));
      for (const control of body.querySelectorAll("button")) control.disabled = value || control.dataset.unavailable === "true";
    };
    close.addEventListener("click", () => dialog.close());
    dialog.addEventListener("close", () => { loadRevision += 1; actionRevision += 1; });
    async function action(work, pending) {
      if (busy) return;
      const revision = ++actionRevision;
      const current = () => revision === actionRevision && dialog.open;
      setBusy(true); show(pending || "正在准备示例…");
      try { const complete = await work(current); if (!current()) return; if (complete === false) throw new Error("页面已切换，示例准备已停止；请重新选择。"); show(""); dialog.close(); }
      catch (error) { if (current()) show(error.message || "示例准备失败，请重试。", true); }
      finally { setBusy(false); if (dialog.open && !current()) load(); }
    }
    function button(label, work, unavailable, pending) {
      const control = node("button", "button button-secondary", label); control.type = "button";
      control.dataset.unavailable = String(Boolean(unavailable)); control.disabled = Boolean(unavailable);
      control.addEventListener("click", () => action(work, pending)); return control;
    }
    async function resolveDocuments(keys) {
      documents = (await api("/api/documents")).documents || [];
      return keys.map(key => {
        const paper = catalog.papers.find(item => item.key === key);
        if (!paper) throw new Error("示例资料目录已变更，请刷新后重试。");
        const result = paperState(paper, documents);
        if (!result.document) throw new Error(`${paper.title}：${result.message}。请导入官方 PDF 并完成解析后重试。`);
        return result.document;
      });
    }
    async function openPaper(paper, module, current) {
      const [doc] = await resolveDocuments([paper.key]);
      if (!current()) return false;
      const page = Number(paper.read_page || 1);
      if (!Number.isInteger(page) || page < 1 || page > doc.page_count) throw new Error("示例定位页码与当前版本不一致，请核对论文文件。");
      await onEvidence({document_id: doc.id, run_id: doc.available_run.id, page});
      return module && current() ? activate(module) : true;
    }
    async function showChart(current) {
      const example = catalog.dataset;
      const url = new URL(example.csv_path, location.origin);
      if (url.origin !== location.origin || !url.pathname.startsWith("/static/examples/")) throw new Error("示例数据文件路径无效。");
      const response = await fetch(url, {credentials: "same-origin", cache: "no-store"});
      if (!response.ok) throw new Error("示例 CSV 未能加载，请刷新后重试。");
      const content = await response.arrayBuffer();
      if (!content.byteLength || content.byteLength > 5 * 1024 * 1024) throw new Error("示例 CSV 大小无效。");
      const hash = [...new Uint8Array(await crypto.subtle.digest("SHA-256", content))].map(value => value.toString(16).padStart(2, "0")).join("");
      if (!current()) return false;
      if (example.sha256 && example.sha256 !== hash) throw new Error("示例 CSV 与来源目录的校验值不一致，请刷新后重试。");
      async function ensureDataset() {
        const existing = (await api("/api/datasets")).datasets || [];
        if (!current()) return null;
        const match = existing.find(item => item.sha256 === hash); if (match) return match;
        const form = new FormData(); form.append("file", new Blob([content], {type: "text/csv"}), example.filename);
        return (await api("/api/datasets", {method: "POST", body: form})).dataset;
      }
      const dataset = navigator.locks ? await navigator.locks.request(`research-example-${hash}`, ensureDataset) : await ensureDataset();
      if (!current()) return false;
      if (!dataset?.id) throw new Error("示例数据导入未完成，请重试。");
      return activate("data", {example: {datasetId: dataset.id, chart: example.chart, sourceUrl: externalUrl(example.source_url), sourceLabel: example.source_label, generate: true}});
    }
    function render() {
      body.replaceChildren();
      body.append(node("p", "examples-intro", catalog.description));
      const paperSection = node("section", "examples-section");
      const paperHeading = node("div", "examples-section-heading"); paperHeading.append(node("h3", "", "01 · 阅读真实论文"));
      const allReady = catalog.papers.every(paper => paperState(paper, documents).document);
      paperHeading.append(button("查看示例引用图谱", async current => {
        const docs = await resolveDocuments(catalog.papers.map(paper => paper.key));
        if (!current()) return false;
        return activate("graph", {documentIds: docs.map(doc => doc.id)});
      }, !allReady));
      paperSection.append(paperHeading);
      const papers = node("div", "examples-papers");
      for (const paper of catalog.papers) {
        const state = paperState(paper, documents), item = node("article", "examples-paper");
        const authors = Array.isArray(paper.authors) ? paper.authors.slice(0, 3).join("、") + (paper.authors.length > 3 ? " 等" : "") : paper.authors;
        const title = node("h4", "", paper.title), meta = node("p", "examples-meta", `${authors} · ${paper.year} · arXiv:${paper.arxiv_id}`);
        item.append(title, meta, node("p", "examples-description", paper.summary));
        if (paper.publication_note) item.append(node("p", "examples-meta", paper.publication_note));
        const row = node("div", "examples-actions");
        row.append(node("span", `examples-state${state.document ? " is-ready" : ""}`, state.message),
          button(`阅读第 ${paper.read_page || 1} 页`, current => openPaper(paper, null, current), !state.document),
          button("文献信息", current => openPaper(paper, "manage", current), !state.document),
          sourceLink("论文来源 ↗", paper.source_url), sourceLink("官方 PDF ↗", paper.pdf_url));
        item.append(row); papers.append(item);
      }
      paperSection.append(papers, node("p", "examples-help", "示例按当前工作空间匹配文献。未导入时，可从官方 PDF 获取文件，再用页头“导入 PDF”添加；归档资料和非活动版本不参与默认演示。"));
      body.append(paperSection);
      const taskSection = node("section", "examples-section"); taskSection.append(node("h3", "", "02 · 准备研究任务"),
        node("p", "examples-help", "选择下方示例将填好文献与问题。点击任务页“创建研究任务”后才调用模型并消耗服务额度。"));
      const scenarios = node("div", "examples-scenarios");
      for (const scenario of catalog.scenarios || []) {
        const keys = scenario.document_keys || [];
        const ready = keys.length && keys.every(key => { const paper = catalog.papers.find(item => item.key === key); return paper && paperState(paper, documents).document; });
        const item = node("article", "examples-scenario"); item.append(node("h4", "", scenario.title), node("p", "examples-description", scenario.description));
        const prepare = button("准备此任务", async current => {
          const docs = await resolveDocuments(keys);
          if (!current()) return false;
          return activate("research", {example: {...scenario, documentIds: docs.map(doc => doc.id)}});
        }, !ready);
        item.append(prepare); if (!ready) item.append(node("span", "examples-meta", "所需文献尚未全部就绪"));
        scenarios.append(item);
      }
      taskSection.append(scenarios); body.append(taskSection);
      if (catalog.dataset) {
        const data = node("section", "examples-section examples-data"); data.append(node("h3", "", "03 · 从真实数据生成图表"),
          node("p", "examples-description", catalog.dataset.description),
          node("p", "examples-help", "使用论文报告的实验结果演示数据分析；本项目未重新训练或复现这些实验。重复使用会复用相同内容的数据集。"));
        const actions = node("div", "examples-actions");
        const chart = button("展示图表", showChart, false, "正在载入真实 CSV 并生成图表…"); chart.classList.replace("button-secondary", "button-primary");
        actions.append(chart, sourceLink(catalog.dataset.source_label || "查看数据来源 ↗", catalog.dataset.source_url)); data.append(actions); body.append(data);
      }
      const footer = node("div", "examples-footer");
      footer.append(node("span", "examples-help", "也可在“发现与追踪”中搜索 Transformer、BERT 或 retrieval augmented generation。"),
        button("打开发现与追踪", () => activate("discovery")));
      body.append(footer);
    }
    async function load() {
      if (busy) return;
      const revision = ++loadRevision; show("正在读取示例与当前空间文献…"); body.replaceChildren();
      try {
        const [response, library] = await Promise.all([fetch("/static/examples/catalog.json", {cache: "no-store", credentials: "same-origin"}), api("/api/documents")]);
        if (!response.ok) throw new Error("示例目录暂时不可用，请重试。");
        const result = await response.json();
        if (revision !== loadRevision) return;
        if (!Array.isArray(result.papers) || !Array.isArray(result.scenarios)) throw new Error("示例目录格式无效。");
        catalog = result; documents = library.documents || []; render(); show("");
      } catch (error) {
        if (revision !== loadRevision) return;
        show(error.message || "示例暂时无法加载。", true);
        const retry = node("button", "button button-secondary", "重新加载"); retry.type = "button"; retry.addEventListener("click", load); body.replaceChildren(retry);
      }
    }
    trigger.addEventListener("click", () => { if (!dialog.open) dialog.showModal(); load(); });
  }
  window.ResearchExamples = Object.freeze({mount});
})();
