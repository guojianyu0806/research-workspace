"use strict";
(() => {
  const labels={same_work_candidate:"同一论文候选",text_copy_candidate:"正文副本候选",related_only:"相近但身份未确认",identity_conflict:"标识冲突 · 保持分开",insufficient_evidence:"证据不足",already_grouped:"已有版本关系",existing_group:"涉及现有版本组"};
  const el=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=String(text);return n;};
  const json=body=>({method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
  function mount(container,{api,document:doc,onEvidence,refreshLibrary}){
    let alive=true,epoch=0,report=null,busy=false;
    const root=el("section","rm-dedup"),intro=el("div","rm-section-heading");
    intro.append(el("h3","","核对重复项，保留每一份原文"),el("p","rm-muted","语义相似只用于发现候选。主题相近、标识冲突或证据不足时保持分开；确认分组不会删除原文和历史引用。"));
    const controls=el("div","rm-actions"),scope=el("select","rm-select");scope.setAttribute("aria-label","去重扫描范围");
    for(const [value,text] of [["current","当前文献与文献库"],["all","整个文献库"]]){const o=el("option","",text);o.value=value;scope.append(o);}
    const scan=button("开始本地去重扫描",()=>scanNow(),"rm-button rm-primary");
    const history=el("select","rm-select");history.setAttribute("aria-label","去重扫描历史");
    history.addEventListener("change",()=>{if(history.value)load(history.value);});
    controls.append(scope,scan);const historyField=el("label","rm-field");historyField.append(el("span","rm-field-title","历史扫描与审核"),history);
    const status=el("p","rm-muted");status.setAttribute("role","status");const output=el("div","dedup-results");
    root.append(intro,controls,historyField,status,output);container.replaceChildren(root);
    function button(text,fn,cls="rm-button"){const b=el("button",cls,text);b.type="button";b.addEventListener("click",fn);return b;}
    function notice(text,error=false){window.ResearchNotices.show(text,error);}
    function setBusy(value){busy=value;scan.disabled=value;scope.disabled=value;history.disabled=value;output.inert=value;output.setAttribute("aria-busy",String(value));}
    async function refreshHistory(selected){
      const data=await api('/api/dedup/scans');if(!alive)return;
      history.replaceChildren(el('option','','选择已保存的扫描'));history.firstChild.value='';
      for(const item of data.scans || []){const o=el('option','',`${new Date(item.created_at).toLocaleString()} · ${item.pair_count} 对 · ${item.complete?'完成':'范围未完整'}`);o.value=item.id;history.append(o);}
      if(selected)history.value=selected;
    }
    async function scanNow(){
      if(busy)return;const request=++epoch;setBusy(true);status.textContent='正在读取固定来源并计算本地语义候选，首次索引较慢……';
      try{
        const data=await api('/api/dedup/scans',json(scope.value==='current'?{document_id:doc.id}:{}));
        if(!alive || request!==epoch)return;
        report=data;render();await refreshHistory(data.id);notice('去重扫描已保存；尚未自动合并任何文献。');
      }catch(error){if(alive && request===epoch){status.textContent='扫描未完成，原文和版本关系未改变。';notice(error.message,true);}}
      finally{if(alive && request===epoch)setBusy(false);}
    }
    async function load(id){
      if(busy)return;const request=++epoch;setBusy(true);status.textContent='正在读取已保存的来源与审核记录……';
      try{const data=await api(`/api/dedup/scans/${encodeURIComponent(id)}`);if(alive && request===epoch){report=data;render();}}
      catch(error){if(alive)notice(error.message,true);}
      finally{if(alive && request===epoch)setBusy(false);}
    }
    function sourceButton(evidence,label){
      const b=button(label,()=>Promise.resolve(onEvidence?.(evidence)).catch(e=>notice(e.message,true)),'rm-link-button');
      b.disabled=!onEvidence;return b;
    }
    function render(){
      output.replaceChildren();
      status.textContent=`已固定 ${report.sources.length} 篇文献，检查 ${report.pairs_evaluated} 对，记录 ${report.pairs.length} 对候选。${report.complete?'本地扫描完成。':'扫描范围未完整，请查看限制。'}没有自动合并。`;
      const summary=el('div','rm-card dedup-summary');
      summary.append(el('p','rm-muted',`语义状态：${report.semantic.status==='applied'?'本地向量已使用':'未完成，仅使用可用的标识和正文依据'}。语义分数和正文重合率不是论文身份的证明。`));
      if(!report.complete)summary.append(el('p','rm-warning',`未读取来源 ${report.skipped_sources.length} 篇；部分可读 ${report.partial_sources?.length || 0} 篇；缺少合适语义抽样 ${report.unsampled_sources?.length || 0} 篇；文献上限${report.documents_truncated?'已触发':'未触发'}；未显示候选 ${report.pairs_omitted} 对。不能据此宣布全库没有重复项。`));
      const exportButton=button('导出扫描与审核记录',()=>{
        const url=URL.createObjectURL(new Blob([JSON.stringify(report,null,2)],{type:'application/json;charset=utf-8'}));
        const a=document.createElement('a');a.href=url;a.download=`dedup-${report.id}.json`;a.click();window.setTimeout(()=>URL.revokeObjectURL(url),1000);
      });summary.append(exportButton);output.append(summary);
      if(!report.pairs.length)output.append(el('p','rm-muted','当前范围没有记录到满足候选条件的配对；这不证明未知来源中不存在重复。'));
      for(const pair of report.pairs){
        const card=el('article','rm-card dedup-pair');card.dataset.pairId=pair.pair_id;
        card.append(el('h4','',labels[pair.classification] || pair.classification),el('p','rm-muted',pair.reason));
        const meters=el('p','rm-muted');meters.textContent=`正文指纹重合 ${(pair.body_overlap*100).toFixed(1)}% · 语义相似度 ${Number.isFinite(pair.semantic_similarity)?pair.semantic_similarity.toFixed(3):'未启用'}`;card.append(meters);
        const sides=el('div','dedup-sides');
        for(const side of ['left','right']){
          const source=pair[side],box=el('section','dedup-side');box.append(el('strong','',source.title || source.filename),el('p','rm-muted',`${source.filename} · 解析${source.parse_version} · ${source.parse_status==='completed'?'完整可读':'部分可读'} · ${source.body_tokens} 个正文词项`));
          const identity=pair.identities[side];box.append(el('p','rm-muted',`自身标识：${[...identity.arxiv,...identity.doi].join(' / ') || '未确认'}${identity.filename_arxiv?'；文件名标注 '+identity.filename_arxiv:''}`));
          for(const evidence of identity.evidence || [])box.append(sourceButton(evidence,`核对标识原文 · 第${evidence.page}页`));
          const samples=el('details','dedup-samples');samples.append(el('summary','','查看正文抽样依据'));
          for(const evidence of pair.source_samples[side] || []){
            const fragment=el('div','dedup-fragment');fragment.append(el('p','rm-original-text',evidence.text),sourceButton(evidence,`核对正文原文 · 第${evidence.page}页`));
            if(evidence.truncated)fragment.append(el('p','rm-muted',`显示该原块前1600字符，完整原块有${evidence.source_text_length}字符。`));samples.append(fragment);
          }
          box.append(samples);sides.append(box);
        }
        card.append(sides);
        const active=(report.actions || []).find(a=>a.pair_id===pair.pair_id && !a.reverted_at);
        const actions=el('div','rm-actions');
        if(active){
          actions.append(el('span','rm-muted',active.decision==='group'?'已确认分组；原文与旧引用仍保留。':'已审核为保持分开。'),button('撤销这次审核',()=>mutate(()=>api(`/api/dedup/actions/${active.id}/undo`,{method:'POST'}))));
        }else{
          if(pair.can_group){
            actions.append(button('确认同一论文，常用左侧',()=>askGroup(pair,'left')),button('确认同一论文，常用右侧',()=>askGroup(pair,'right')));
          }
          actions.append(button('审核为保持分开',()=>mutate(()=>api(`/api/dedup/scans/${report.id}/decisions`,json({pair_id:pair.pair_id,decision:'keep_separate'})))));
        }
        card.append(actions);output.append(card);
      }
      const audits=el('details','rm-card dedup-audits');audits.append(el('summary','',`审核记录（${report.actions?.length || 0}）`));
      for(const action of report.actions || []){
        const row=el('div','dedup-audit-row');row.append(el('p','rm-muted',`${action.created_at} · ${action.decision==='group'?'确认同一论文':'保持分开'} · ${action.reverted_at?'已撤销':'有效'} · ${action.id}`));
        if(!action.reverted_at)row.append(button('撤销审核 '+action.id.slice(0,8),()=>mutate(()=>api(`/api/dedup/actions/${action.id}/undo`,{method:'POST'}))));
        audits.append(row);
      }
      output.append(audits);
    }
    let dialog=null;
    function askGroup(pair,side){
      const chosen=pair[side];dialog=el('dialog','dedup-dialog');const title=el('h3','','确认论文身份与常用版本');
      dialog.append(title,el('p','rm-muted',`将“${chosen.title || chosen.filename}”设为常用版本，另一份在常用列表中折叠。两份PDF、解析和旧引用均保留，可撤销。请先核对两侧原文。`));
      const row=el('div','rm-actions');row.append(button('返回核对',()=>dialog.close()),button('确认并保留全部原文',()=>{
        dialog.close();mutate(()=>api(`/api/dedup/scans/${report.id}/decisions`,json({pair_id:pair.pair_id,decision:'group',preferred_id:chosen.document_id})));
      },'rm-button rm-primary'));dialog.append(row);dialog.addEventListener('close',()=>{dialog?.remove();dialog=null;},{once:true});root.append(dialog);dialog.showModal();
    }
    async function mutate(fn){
      if(busy)return;setBusy(true);
      try{await fn();if(!alive)return;report=await api(`/api/dedup/scans/${report.id}`);if(!alive)return;render();await refreshLibrary?.();notice('审核已保存，原文与历史引用保持可访问。');}
      catch(error){if(alive)notice(error.message,true);}
      finally{if(alive)setBusy(false);}
    }
    refreshHistory().catch(error=>{if(alive)notice(error.message,true);});
    status.textContent='点击扫描后使用本地向量和正文依据；不调用付费模型，不会自动合并。';
    return {destroy(){alive=false;epoch++;dialog?.close();root.remove();}};
  }
  window.ResearchDedup={mount};
})();
