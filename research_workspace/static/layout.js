"use strict";

/* Native layout is requested only by the explicit analyze action. */
(() => {
  const instances = new WeakMap();
  const element = (tag, className = "", text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = String(text);
    return node;
  };
  const button = (text, action, className = "rm-button") => {
    const node = element("button", className, text);
    node.type = "button";
    if (action) node.addEventListener("click", action);
    return node;
  };

  function mount(container, options = {}) {
    if (!(container instanceof Element) || typeof options.api !== "function") throw new Error("版面结构需要容器与工作空间接口。");
    instances.get(container)?.destroy();
    const source = typeof options.document === "function" ? options.document() : options.document;
    let alive = true, generation = 0, requestAbort = null;
    const run = source?.available_run || source?.current_run;
    const root = element("section", "rm-layout-inspector");
    const form = element("form", "rm-card rm-form");
    form.append(element("h3", "", "逐页版面结构"), element("p", "rm-muted", "分析PDF中的真实文字、表格与图像区域；候选标签需结合原页复核。"));
    const page = element("input", "rm-input");
    page.type = "number"; page.min = "1"; page.step = "1"; page.value = "1";
    page.max = String(source?.page_count || 1); page.required = true;
    const pageField = element("label", "rm-field");
    pageField.append(element("span", "rm-field-title", `物理页码 · 共 ${source?.page_count || 0} 页`), page);
    const controls = element("div", "rm-actions");
    const analyze = button("分析本页结构", null, "rm-button rm-primary"); analyze.type = "submit";
    const download = element("a", "rm-small-button", "下载本页结构JSON"); download.hidden = true;
    controls.append(analyze, download);
    form.append(pageField, controls);
    const notice = element("p", "rm-notice"); notice.hidden = true; notice.setAttribute("role", "status"); notice.setAttribute("aria-live", "polite");
    const output = element("div", "rm-layout-output");
    output.append(element("p", "rm-empty", "选择页码后点击分析。系统不会自动扫描整篇文献，也不会调用模型服务。"));
    root.append(form, notice, output); container.replaceChildren(root);
    if (!source?.id || !source.page_count) {
      analyze.disabled = true; page.disabled = true;
      output.replaceChildren(element("p", "rm-empty", "请先选择已导入的PDF文献。"));
    }
    function show(message, error = false) {
      if (!alive) return;
      notice.textContent = message; notice.classList.toggle("is-error", error); notice.hidden = !message;
    }
    function panel(title, description) {
      const card = element("section", "rm-card");
      card.append(element("h3", "", title));
      if (description) card.append(element("p", "rm-muted", description));
      return card;
    }
    function evidenceControl(evidence, label = "定位原文 →") {
      const control = button(label, async () => {
        if (!alive) return;
        try { await options.onEvidence(evidence); }
        catch (error) { show(error.message || "暂时无法定位原文。", true); }
      }, "rm-link-button");
      control.disabled = typeof options.onEvidence !== "function" || !evidence?.document_id
        || !evidence?.run_id || !evidence?.page || !Array.isArray(evidence?.bbox);
      return control;
    }
    function candidateList(card, values, absent) {
      if (!values?.length) { card.append(element("p", "rm-muted", absent)); return; }
      for (const value of values) {
        const item = element("article", "rm-text-block");
        item.append(element("p", "rm-original-text", value.text || ""), evidenceControl(value.evidence));
        card.append(item);
      }
    }
    function render(data) {
      output.replaceChildren();
      const overview = panel(`第 ${data.page} 页`, data.status === "no_native_text"
        ? "本页没有原生可提取文字；不会把扫描图像的内容当成已识别文字。"
        : "位置对应所选解析记录；标题、作者机构、阅读顺序、图注及公式均需复核。");
      if (data.truncated) overview.append(element("p", "rm-warning", "本页文字量超过分析上限，当前结构已截断。"));
      if (["queued", "running"].includes(data.processing_status)) overview.append(element("p", "rm-warning", "当前解析记录尚未结束；此处展示的是原始PDF的原生版面观察。"));
      output.append(overview);
      const metadata = panel("标题、作者与机构候选", "候选内容不是已确认的出版元数据；作者和机构仅在首页尝试识别。");
      for (const [key, title] of [["title", "标题候选"], ["authors", "作者候选"], ["affiliations", "机构候选"]]) {
        metadata.append(element("h4", "", title));
        candidateList(metadata, data.metadata_candidates?.[key], "本页未检出候选。");
      }
      output.append(metadata);

      const tables = panel("表格与单元格", "来自实际表格线和单元格提取；无边框、跨页与合并单元格仍可能不完整。");
      if (!data.tables?.length) tables.append(element("p", "rm-muted", data.table_status === "unsupported_or_failed"
        ? "本页表格检测未成功，没有输出猜测的单元格。" : "本页未检测到可提取的线框表格。"));
      for (const [index, table] of (data.tables || []).entries()) {
        const section = element("section", "rm-text-block");
        section.append(element("h4", "", `表格区域 ${index + 1} · ${table.row_count} 行 × ${table.column_count} 列`), evidenceControl(table.evidence, "定位表格区域 →"));
        const scroll = element("div", "rs-table-scroll");
        const grid = element("table", "rs-table");
        const body = element("tbody"); grid.append(body); scroll.append(grid); section.append(scroll);
        const cells = new Map((table.cells || []).map(cell => [`${cell.row}:${cell.column}`, cell]));
        let displayed = 0;
        const more = button("显示更多表格行", appendRows, "rm-small-button");
        function appendRows() {
          const end = Math.min(displayed + 50, table.row_count);
          for (; displayed < end; displayed++) {
            const row = element("tr");
            for (let column = 0; column < table.column_count; column++) {
              const cell = cells.get(`${displayed}:${column}`);
              const td = element("td");
              td.append(element("span", "rm-original-text", cell?.text ?? "合并或缺失单元格"));
              if (cell?.evidence) td.append(evidenceControl(cell.evidence, "定位"));
              row.append(td);
            }
            body.append(row);
          }
          more.hidden = displayed >= table.row_count;
        }
        appendRows(); section.append(more); tables.append(section);
      }
      output.append(tables);

      const figures = panel("图像区域与图注候选", "区域来自PDF中嵌入的图像；尚未识别图中内容，图注关联是位置建议。");
      if (!data.figures?.length) figures.append(element("p", "rm-muted", "本页未检测到嵌入图像区域；矢量图语义识别尚不支持。"));
      for (const [index, figure] of (data.figures || []).entries()) {
        const item = element("article", "rm-text-block");
        item.append(element("h4", "", `图像区域 ${index + 1}`), evidenceControl(figure.evidence, "定位图像区域 →"));
        for (const caption of (data.captions || []).filter(value => value.target_id === figure.id)) {
          item.append(element("p", "rm-original-text", caption.text), evidenceControl(caption.evidence, "定位图注原文 →"));
        }
        figures.append(item);
      }
      const otherCaptions = (data.captions || []).filter(value => value.kind === "table" || !value.target_id);
      if (otherCaptions.length) {
        figures.append(element("h4", "", "其他图表标题候选"));
        candidateList(figures, otherCaptions, "");
      }
      output.append(figures);

      const formulas = panel("公式候选", "原文始终保留；LaTeX仅为受支持线性符号的有限转写，数学含义仍需核验。");
      if (!data.formulas?.length) formulas.append(element("p", "rm-muted", "本页未检测到原生文字中的公式候选；图片公式不会被猜测转写。"));
      for (const formula of data.formulas || []) {
        const item = element("article", "rm-text-block");
        item.append(element("span", "rm-badge", "需复核"), element("p", "rm-original-text", formula.original_text || formula.text), evidenceControl(formula.evidence));
        if (formula.latex) {
          item.append(element("p", "rm-field-title", "有限LaTeX转写"), element("pre", "rm-original-text", formula.latex));
        } else item.append(element("p", "rm-muted", "该公式不在可靠转写子集中，仅保留原文。"));
        formulas.append(item);
      }
      output.append(formulas);

      const reading = panel("阅读顺序建议", data.reading_order?.columns === 2 ? "检测到双栏间隙，建议按区段先左栏后右栏阅读。"
        : data.reading_order?.columns === 1 ? "采用由上到下、同排从左到右的保守顺序。" : "缺少原生文字，无法建议栏序。");
      reading.append(element("p", "rm-warning", data.reading_order?.limitations || "顺序为启发式建议，需人工复核。"));
      const details = element("details"); details.append(element("summary", "", "查看文字顺序与原文位置"));
      const list = element("ol"); details.append(list);
      const regions = new Map((data.text_regions || []).map(value => [value.id, value]));
      const ids = data.reading_order?.region_ids || [];
      let displayed = 0;
      const more = button("显示更多文字行", appendLines, "rm-small-button");
      function appendLines() {
        const end = Math.min(displayed + 50, ids.length);
        for (; displayed < end; displayed++) {
          const region = regions.get(ids[displayed]); if (!region) continue;
          const item = element("li");
          item.append(element("span", "rm-original-text", region.text), evidenceControl(region.evidence)); list.append(item);
        }
        more.hidden = displayed >= ids.length;
      }
      appendLines(); details.append(more); reading.append(details); output.append(reading);
      const appendix = panel("附录标记", "仅展示当前页实际检出的附录标题候选。");
      candidateList(appendix, data.appendix_markers, "本页未检出附录标记。"); output.append(appendix);
      const limits = panel("使用范围与限制");
      const limitations = element("ul", "rm-muted");
      for (const value of data.limitations || []) limitations.append(element("li", "", value));
      limits.append(limitations); output.append(limits);
    }
    function invalidate() {
      generation++; requestAbort?.abort(); requestAbort = null;
      analyze.disabled = !source?.id; download.hidden = true; download.removeAttribute("href");
      output.replaceChildren(element("p", "rm-empty", "页码已变更，请点击分析以读取这一页。"));
      show("");
    }
    page.addEventListener("input", invalidate);
    async function submit(event) {
      event.preventDefault();
      const number = Number(page.value);
      if (!Number.isInteger(number) || number < 1 || number > source.page_count) { show(`请输入1至${source.page_count}之间的页码。`, true); return; }
      const current = ++generation;
      requestAbort?.abort(); requestAbort = new AbortController();
      analyze.disabled = true; download.hidden = true; show("正在分析本页原生版面……");
      const query = run?.id ? `?run_id=${encodeURIComponent(run.id)}` : "";
      const path = `/api/documents/${encodeURIComponent(source.id)}/layout/pages/${number}`;
      try {
        const data = await options.api(path + query, { signal: requestAbort.signal });
        if (!alive || current !== generation) return;
        if (data.document_id !== source.id || data.page !== number || (run?.id && data.run_id !== run.id)) throw new Error("返回的版面记录与当前文献、页码或版本不一致。");
        render(data);
        download.href = `${path}/export?run_id=${encodeURIComponent(data.run_id)}`;
        download.download = `layout-${source.id}-page-${number}.json`; download.hidden = false;
        show("本页结构已提取；请通过原文位置核对候选内容。");
      } catch (error) { if (alive && current === generation) show(error.message || "分析未完成，请重试。", true); }
      finally { if (alive && current === generation) analyze.disabled = false; }
    }
    form.addEventListener("submit", submit);
    const controller = { ready: Promise.resolve(), destroy() {
      if (!alive) return;
      alive = false; generation++; requestAbort?.abort();
      page.removeEventListener("input", invalidate); form.removeEventListener("submit", submit); root.remove();
      if (instances.get(container) === controller) instances.delete(container);
    } };
    instances.set(container, controller);
    return controller;
  }
  window.ResearchLayout = Object.freeze({ mount });
})();
