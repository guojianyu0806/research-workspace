"use strict";

/* Local layout / OCR is requested only by the explicit analyze action. */
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
    form.append(element("h3", "", "逐页版面结构"), element("p", "rm-muted", "分析原生公式与表格；扫描表格使用本地OCR，结果和位置均需核对原页。"));
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
      window.ResearchNotices.show(message, error, notice);
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

      const tables = panel("表格与单元格", "原生表格来自文字与线条；扫描表格按可见网格和本地OCR恢复。数值、数学表头与空格均需复核。");
      if (!data.tables?.length) tables.append(element("p", "rm-muted", data.table_status === "unsupported_or_failed"
        ? "本页表格检测未成功，没有输出猜测的单元格。" : "本页未检测到可提取的线框表格。"));
      for (const [index, table] of (data.tables || []).entries()) {
        const section = element("section", "rm-text-block");
        section.append(element("h4", "", `表格区域 ${index + 1} · ${table.row_count} 行 × ${table.column_count} 列`), evidenceControl(table.evidence, "定位表格区域 →"));
        if (table.text_method === 'ocr_cells_joined_by_row') section.append(element('p','rm-warning','扫描表格 · 本地OCR候选，尚未人工确认数值；空白可能是未识别。'));
        if (table.truncated_cells) section.append(element('p','rm-warning','已达到本页200个单元格的OCR上限，其余格保留为未处理。'));
        const scroll = element("div", "rs-table-scroll");
        const grid = element("table", "rs-table");
        const body = element("tbody"); grid.append(body); scroll.append(grid); section.append(scroll);
        const cells = new Map((table.cells || []).map(cell => [`${cell.row}:${cell.column}`, cell]));
        const covered = new Set();
        let displayed = 0;
        const more = button("显示更多表格行", appendRows, "rm-small-button");
        function appendRows() {
          const end = Math.min(displayed + 50, table.row_count);
          for (; displayed < end; displayed++) {
            const row = element("tr");
            for (let column = 0; column < table.column_count; column++) {
              if (covered.has(`${displayed}:${column}`)) continue;
              const cell = cells.get(`${displayed}:${column}`);
              const td = element("td");
              if (cell?.row_span || cell?.column_span) {
                td.rowSpan=cell.row_span || 1;td.colSpan=cell.column_span || 1;
                for(let r=displayed;r<displayed+td.rowSpan;r++)for(let c=column;c<column+td.colSpan;c++)if(r!==displayed || c!==column)covered.add(`${r}:${c}`);
              }
              td.append(element("span", "rm-original-text", cell?.text ?? "合并或缺失单元格"));
              if (cell?.status === 'empty_or_ocr_missed') td.append(element('small','rm-muted','空白或OCR未识别'));
              if (cell?.status === 'not_processed_budget') td.append(element('small','rm-muted','未处理：达到OCR上限'));
              for (const token of cell?.math_tokens || []) {
                if (!token.latex) continue;
                const math = element('div'); math.append(element('code','',token.latex), evidenceControl(token.evidence,'核对数学表头 →'));
                td.append(math);
              }
              if (cell?.evidence) {
                const actions = element('div');
                actions.append(evidenceControl(cell.evidence, "定位"));
                td.append(actions);
              }
              if (cell?.ocr_audit?.length) {
                const audit = element('details'); audit.append(element('summary','','查看OCR复核记录'));
                audit.append(element('p','rm-muted',`本次初始识别：${cell.raw_ocr_text || '空白'}`));
                const labels = {sparse_mode_candidate:'稀疏文字模式候选',two_view_candidate:'两种分辨率的原图识别一致',
                  empty_crop_artifact:'单元格内部对应裁图为空白',math_header_candidate:'主字符与上下标分区识别候选',unresolved:'复核未取得一致结果',not_processed_budget:'达到复核上限，未处理'};
                for (const entry of cell.ocr_audit) {
                  audit.append(element('p','rm-muted',`${entry.original_text} → ${entry.text || (entry.text === '' ? '已排除' : entry.original_text)} · ${labels[entry.status] || '待复核'}`));
                  if (entry.evidence) audit.append(evidenceControl(entry.evidence,'核对该处原图 →'));
                }
                td.append(audit);
              }
              row.append(td);
            }
            body.append(row);
          }
          more.hidden = displayed >= table.row_count;
        }
        appendRows(); section.append(more); tables.append(section);
      }
      output.append(tables);

      const matrices = panel('矩阵与逐项依据','保留原页括号、行列及每个元素的位置；缺失或无法确认的元素不会补成零。结果均需核对原图。');
      if (!data.matrices?.length) matrices.append(element('p','rm-muted','本页未检出受支持的矩阵结构。'));
      for (const matrix of data.matrices || []) {
        const card = element('article','rm-text-block');
        card.append(element('h4','',matrix.latex ? `${matrix.row_count} 行 × ${matrix.column_count} 列 · 待核对` : '矩阵区域 · 未完整识别'),evidenceControl(matrix.evidence,'核对矩阵原图 →'));
        if (matrix.latex) card.append(element('pre','rm-original-text',matrix.latex));
        else card.append(element('p','rm-warning',`未输出完整LaTeX：${matrix.conversion || '识别依据不足'}`));
        const audit = element('details'); audit.append(element('summary','','查看矩阵审核记录'));
        audit.append(element('p','rm-muted',matrix.method === 'raster-math-v1' ? '来自原图像素的括号与行列观察；每个元素保留两次OCR结果。' : `方法：${matrix.method}；已核实括号字形 ${matrix.bracket_audit?.length || 0} 个。`));
        if (matrix.segmentation_audit) audit.append(element('p','rm-muted',`分区阈值 ${matrix.segmentation_audit.threshold}；原始墨迹片段 ${matrix.segmentation_audit.initial_component_count} 个，位置相接的分组 ${matrix.segmentation_audit.joined_component_count} 个。`));
        for (const cell of matrix.cells || []) {
          const row=element('p');row.append(element('span','',`${matrix.conversion === 'matrix_row_spacing_ambiguous' ? '观察行' : '第'} ${cell.row+1} 行，第 ${cell.column+1} 列：${cell.latex || cell.text || '未确认'} `),evidenceControl(cell.evidence,'核对元素 →'));audit.append(row);
          for (const view of cell.ocr_audit?.views || []) audit.append(element('p','rm-muted',`阈值 ${view.threshold}，模式 ${view.mode}：${view.text || '未识别'}`));
          for (const view of cell.ocr_audit?.recheck_views || []) audit.append(element('p','rm-muted',`局部复核 ${view.dpi} DPI，阈值 ${view.threshold}：${view.text || '未识别'}`));
          for (const view of cell.ocr_audit?.letter_recheck_views || []) audit.append(element('p','rm-muted',`字母复核 ${view.dpi} DPI，阈值 ${view.threshold}：${view.text || '未识别'}`));
          if (cell.ocr_audit?.case_geometry) {
            const geometry=cell.ocr_audit.case_geometry;
            audit.append(element('p','rm-muted',`大小写几何核对：本元素字高 ${geometry.target_height}；小写主体字高 ${geometry.body_height}；上伸字母字高 ${geometry.ascender_height}。参照仅用于字高，不提供目标字母。`));
            for (const reference of geometry.reference_regions || []) audit.append(evidenceControl(reference.evidence,'核对字高参照 →'));
          }
        }
        card.append(audit);matrices.append(card);
      }
      output.append(matrices);
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

      const formulas = panel("公式候选", "扫描公式可按原图位置组合有限分式、根号和单字符上下标，并尝试恢复具有完整依据的等式。整式、片段和未完成结果分别标记；嵌套子项可逐层核对。多次OCR一致仍需人工复核，任意扫描公式和嵌套矩阵尚不支持。");
      if (data.formula_audit?.truncated || ['glyph_budget_exceeded','vector_budget_exceeded'].includes(data.formula_audit?.status))
        formulas.append(element('p','rm-warning','本页公式或字形超过本地分析上限；此处不是完整公式清单，请核对原页。'));
      if (!data.formulas?.length) formulas.append(element("p", "rm-muted", "本页未检测到受支持的公式候选；未知图片符号不会被猜测转写。"));
      const recursiveAudit=data.raster_math_audit?.composition_audit?.recursive_audit;
      if (recursiveAudit?.candidate_regions?.length) {
        const candidates=element('details');candidates.append(element('summary','','查看递归结构候选与未识别区域'));
        candidates.append(element('p','rm-muted',`结构候选 ${recursiveAudit.candidate_count} 个；局部OCR ${recursiveAudit.ocr_calls}/${recursiveAudit.ocr_budget} 次。子候选可能由父结构包含，尚未恢复的区域不能作为完整结果。`));
        for (const region of recursiveAudit.candidate_regions.slice(0,64)) candidates.append(element('p','rm-muted',`${region.kind==='radical' ? '根号' : '分式'} · ${region.parsed ? '已保留解析观察' : region.reason || '子候选或待复核区域'}`),evidenceControl(region.evidence,'核对结构候选 →'));
        if (recursiveAudit.candidate_regions.length>64) candidates.append(element('p','rm-muted','其余候选位置保存在结构JSON中。'));
        formulas.append(candidates);
      }
      for (const formula of data.formulas || []) {
        const item = element("article", "rm-text-block");
        item.append(element("span", "rm-badge", "需复核"), element("p", "rm-original-text", formula.original_text || formula.text), evidenceControl(formula.evidence));
        if (formula.latex) {
          item.append(element("p", "rm-field-title", formula.scope === 'whole_equation' ? (formula.structure_kind==='aligned_sum_equations' ? '含续行的完整求和等式转写候选 · 待核对' : '整条等式转写候选 · 待核对') : formula.scope==='equality_continuation' ? '等号续行片段 · 需结合已确认的前式核对' : formula.scope === 'summation_expression' ? '求和项转写候选 · 未恢复整条等式 · 待核对' : formula.scope === 'operator_with_limits' ? '求和符号与上下限片段 · 未恢复被求和项 · 待核对' : formula.scope === 'subexpression' ? '公式子表达式 · 未恢复整条等式 · 待核对' : "LaTeX结构转写 · 待核对"), element("pre", "rm-original-text", formula.latex));
        } else item.append(element("p", "rm-muted", "该公式不在可靠转写子集中，仅保留原文。"));
        if (formula.method?.startsWith('native-math-geometry-')) {
          const audit = element('details'); audit.append(element('summary', '', '查看转写依据'));
          audit.append(element('p', 'rm-muted', `原生字形 ${formula.glyph_count} 个 · 水平线段 ${formula.rule_evidence?.length || 0} 条。全部位置随结构JSON导出。`));
          const reasons = {unmapped_native_glyph:'存在未映射字形', unassigned_glyph_inside_formula_region:'区域内有未归属字形',
            radical_extent_not_observed:'未找到明确的根号覆盖线', multiple_unresolved_math_rows:'多行结构尚未可靠分解',
            unattached_math_script:'上下标归属不明确', unbalanced_math_delimiters:'括号结构不完整',
            formula_complexity_limit:'超过有限解析的复杂度上限', prose_mixed_with_math:'公式与说明文字混排',
            large_operator_limits_require_review:'求和、积分的上下限尚需人工核对', orphan_or_ambiguous_continuation:'等式续行没有明确可连接的前式', operator_baseline_ambiguous:'大型运算符的主行不明确', multiline_continuation_requires_review:'多行等式尚未完整恢复'};
          if (!formula.latex) audit.append(element('p','rm-warning', reasons[formula.conversion] || '结构超出当前支持范围，保留原文供核对。'));
          for (const glyph of formula.glyph_evidence || []) if (glyph.glyph_mapping) {
            const mapping = glyph.glyph_mapping;
            audit.append(element('p','rm-muted',`嵌入字形 ${mapping.glyph_name}：原文本层 ${mapping.native_text} → ${mapping.mapped_text}`));
          }
          for (const [rowIndex,row] of (formula.source_rows || []).entries()) {
            audit.append(element('p','rm-muted',`第 ${rowIndex+1} 行 · ${row.original_text}`),evidenceControl(row.evidence,`核对第 ${rowIndex+1} 行 →`));
          }
          item.append(audit);
        }
        if (formula.method?.startsWith('raster-')) {
          const audit=element('details');audit.append(element('summary','','查看扫描公式审核记录'));
          audit.append(element('p','rm-muted',formula.latex ? '多次OCR观察一致仍可能出错，请核对原图。' : `未输出完整结果：${formula.conversion}；保留原图与不同识别观察。`));
          if (formula.alignment_audit) audit.append(element('p','rm-muted',`已保留 ${formula.alignment_audit.row_count} 行独立原文与审核；左侧表达式、等号和每行内容均已转写。数学等价关系尚未由程序验证。`));
          if (formula.operator_audit) {
            audit.append(evidenceControl(formula.operator_audit.evidence,'核对运算符原图 →'));
            for (const view of formula.operator_audit.views || []) audit.append(element('p','rm-muted',`语言 ${view.language}，${view.dpi} DPI：${view.text || '未识别'}`));
          }
          const roles={base:'主字符',subscript:'下标',supscript:'上标',radicand:'根号内内容',upper_limit:'求和上限',lower_limit:'求和下限',numerator:'分子',denominator:'分母',nested_structure:'嵌套结构',observed_text_run:'文字片段',observed_delimiter:'括号',delimiter_or_relation:'标点或关系符',sum_operator_limits:'求和符号及上下限',summand:'被求和项',left_hand_side:'左侧函数或表达式',right_hand_side:'右侧求和表达式',equality:'等号',equation_row:'等式原文行'};
          function appendObservation(value, depth=0) {
            const own=element('div','');
            const role=value.role==='aligned_row' ? '对齐续行' : roles[value.role] || '操作数';
            if (value !== formula) own.append(element('p','rm-field-title',`${role}：${value.latex || '未完成'}`),evidenceControl(value.evidence,`核对${role} →`));
            if (value.equality_audit) own.append(element('p','rm-muted','本行等号由两条对齐的水平墨迹确认；尚未恢复完整推导。'),evidenceControl(value.equality_audit.evidence,'核对本行等号 →'));
            for (const rejected of value.rejected_regions || []) own.append(element('p','rm-muted','跨越本行边界的候选未并入该行。'),evidenceControl(rejected.evidence,'核对跨行候选 →'));
            if (value !== formula && value.operator_audit) {
              own.append(evidenceControl(value.operator_audit.evidence,'核对子项运算符 →'));
              for (const view of value.operator_audit.views || []) own.append(element('p','rm-muted',`运算符观察 ${view.language || ''}，${view.dpi} DPI，阈值 ${view.threshold}：${view.text || '未识别'}`));
            }
            for (const view of [...(value.ocr_audit?.views || []),...(value.ocr_audit?.recheck_views || []),...(value.ocr_audit?.additional_recheck_views || []),...(value.ocr_audit?.greek_views || [])]) own.append(element('p','rm-muted',`阈值 ${view.threshold}，模式 ${view.mode}${view.dpi ? `，${view.dpi} DPI` : ''}${view.language ? `，${view.language}` : ''}：${view.text || '未识别'}`));
            const greek=value.ocr_audit?.source_greek_recheck;
            if (greek) {
              own.append(evidenceControl(greek.evidence,'核对希腊字符局部复核 →'));
              for (const view of greek.views || []) own.append(element('p','rm-muted',`希腊文复核 ${view.dpi} DPI，阈值 ${view.threshold}：${view.text || '未识别'}`));
            }
            const beta=value.ocr_audit?.greek_beta_recheck;
            if (beta) {
              own.append(evidenceControl(beta.evidence,'核对 B 与希腊字符歧义 →'));
              for (const view of beta.views || []) own.append(element('p','rm-muted',`希腊文复核 ${view.dpi} DPI，阈值 ${view.threshold}：${view.text || '未识别'}`));
              for (const view of beta.descender_views || []) own.append(element('p','rm-muted',`阈值 ${view.threshold}：${view.descender_observed ? '观察到下伸笔画' : '下伸笔画未确认'}；保留字形歧义。`));
            }
            for (const view of value.ocr_audit?.bracket_geometry_views || []) own.append(element('p','rm-muted',`括号像素观察 ${view.observed_bracket}：${view.dpi} DPI，阈值 ${view.threshold}，竖干与上下横臂已观察。`));
            const reference=value.ocr_audit?.glyph_reference;
            if (reference) own.append(element('p','rm-muted',`本字符自身OCR已观察到 ${reference.original_text}；同页独立字形余弦相似度 ${reference.cosine_similarity.toFixed(3)}，仍待核对。`),evidenceControl(reference.evidence,'核对同页字形参照 →'));
            for (const view of value.ocr_audit?.geometry_views || []) own.append(element('p','rm-muted',`标点像素观察：${view.observed_punctuation}，${view.dpi} DPI，阈值 ${view.threshold}，墨迹 ${view.ink_width} × ${view.ink_height} 像素`));
            if (value.ocr_audit?.method) own.append(element('p','rm-muted',`像素几何依据：${value.ocr_audit.method}`));
            if (value.coverage_audit) own.append(element('p','rm-muted',`未归属墨迹：${value.coverage_audit.unassigned_ink_pixels} 像素；子项${value.coverage_audit.all_children_resolved ? '已分别转写，仍待复核' : '尚未全部确认'}`));
            if (value.coverage_audit?.observed_operator_ink_pixels) own.append(element('p','rm-muted',`已分离本层运算符墨迹 ${value.coverage_audit.observed_operator_ink_pixels} 像素，原图保留。`));
            if (value !== formula) for (const line of value.associated_line_observations || []) own.append(element('p','rm-muted',`归属本子项的行观察：${line.original_text}`),evidenceControl(line.evidence,'核对子项行观察 →'));
            audit.append(own);
            if (depth<8) for (const child of value.operands || []) appendObservation(child,depth+1);
          }
          appendObservation(formula);
          for (const line of formula.associated_line_observations || []) {
            audit.append(element('p','rm-muted',`归属本求和片段的行观察：${line.original_text}`),evidenceControl(line.evidence,'核对限值行观察 →'));
          }
          item.append(audit);
        }
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
      analyze.disabled = true; download.hidden = true; show("正在分析本页结构；扫描表格将使用本地OCR……");
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
