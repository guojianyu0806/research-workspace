"use strict";
(() => {
  const bridge = window.ResearchWorkspace;
  const root = document.getElementById('moduleRoot');
  const reader = document.getElementById('workspace');
  const mobileNavigation = document.querySelector('.mobile-navigation');
  let controller = null;
  let revision = 0;
  let activeModule = 'reader';
  const returnHistory = [];
  let evidenceOrigin = null;
  const returnButton = document.getElementById('readerReturnButton');
  const moduleReturnBar = document.getElementById('moduleReturnBar');
  const moduleReturnButton = document.getElementById('moduleReturnButton');
  function updateReturnButtons() {
    const previous = returnHistory.at(-1);
    if (returnButton) {
      returnButton.hidden = activeModule !== 'reader' || !previous;
      if (previous) returnButton.textContent = previous.returnLabel;
    }
    if (moduleReturnBar) moduleReturnBar.hidden = activeModule === 'reader' || !previous;
    if (moduleReturnButton && previous) moduleReturnButton.textContent = previous.returnLabel;
  }
  function clearReturnHistory() {
    for (const saved of returnHistory.splice(0)) saved.controller?.destroy?.();
  }
  function retainModule() {
    const selected = window.getSelection();
    const selection = selected?.rangeCount && root.contains(selected.anchorNode) && root.contains(selected.focusNode)
      ? {anchorNode:selected.anchorNode, anchorOffset:selected.anchorOffset, focusNode:selected.focusNode, focusOffset:selected.focusOffset} : null;
    const state = {
      controller, module:activeModule, scrollTop:root.scrollTop, scrollLeft:root.scrollLeft,
      focus:root.contains(document.activeElement) ? document.activeElement : null,
      selection, nodes:document.createDocumentFragment(),
      // The bilingual controller restores its paragraph anchor after resizing.
      scrolls:[...root.querySelectorAll('*')].filter(element => !element.classList.contains('rb-scroll') && (element.scrollTop || element.scrollLeft))
        .map(element => ({element, top:element.scrollTop, left:element.scrollLeft})),
      returnLabel:activeModule === 'manage'
        ? (root.querySelector('[role="tab"][aria-selected="true"]')?.textContent === '本页文字' ? '返回文字核对' : '返回文献管理')
        : (controller?.getReturnLabel?.() || '返回任务结果'),
    };
    controller?.suspend?.();
    while (root.firstChild) state.nodes.append(root.firstChild);
    returnHistory.push(state);
    // The supported chain is text → translation → source; never keep an
    // unbounded collection of live controllers after repeated navigation.
    if (returnHistory.length > 2) returnHistory.shift().controller?.destroy?.();
    controller = null;
  }
  function restoreModule(request) {
    const state = returnHistory.pop();
    controller = state.controller;
    root.append(state.nodes);
    controller?.resume?.();
    if (state.focus?.isConnected) state.focus.focus({preventScroll:true});
    if (state.selection) {
      const selected = window.getSelection();
      try {
        const {anchorNode,anchorOffset,focusNode,focusOffset} = state.selection;
        if (root.contains(anchorNode) && root.contains(focusNode)) selected.setBaseAndExtent(anchorNode,anchorOffset,focusNode,focusOffset);
      } catch { /* A source refresh may have replaced the saved text node. */ }
    }
    const restoreScroll = () => {
      for (const saved of state.scrolls) if (root.contains(saved.element)) {
        saved.element.scrollTop = saved.top; saved.element.scrollLeft = saved.left;
      }
      root.scrollTop = state.scrollTop;
      root.scrollLeft = state.scrollLeft;
    };
    updateReturnButtons();
    restoreScroll();
    // A resumed preview may finish its layout on the next frame. Do not allow
    // this correction to affect a later navigation.
    requestAnimationFrame(() => {
      if (request !== revision || activeModule !== state.module) return;
      restoreScroll();
    });
  }
  function error(message) {
    const element = document.createElement('p');
    element.className = 'module-error';
    element.textContent = message;
    root.replaceChildren(element);
  }
  async function activate(name, intent) {
    const restoring = intent?.restorePrevious && returnHistory.at(-1)?.module === name;
    const retaining = intent?.retainCurrent && ['manage','research'].includes(activeModule) && controller;
    bridge.invalidateEvidenceNavigation?.();
    evidenceOrigin = null;
    if (retaining) retainModule();
    else {
      controller?.destroy?.();
      controller = null;
      if (!restoring) clearReturnHistory();
    }
    activeModule = name;
    document.body.classList.toggle('reader-active', name === 'reader');
    window.dispatchEvent(new CustomEvent('research:module-change', {detail: {module: name}}));
    const request = ++revision;
    root.replaceChildren();
    root.scrollTop = 0;
    root.scrollLeft = 0;
    document.querySelectorAll('[data-module]').forEach(button => button.setAttribute('aria-pressed',String(button.dataset.module===name)));
    reader.hidden = name !== 'reader';
    mobileNavigation.hidden = name !== 'reader';
    root.hidden = name === 'reader';
    updateReturnButtons();
    if (name === 'reader') return true;
    if (restoring) { restoreModule(request); return true; }
    let mountedController = null;
    const options = {...bridge, initialJobId:intent?.jobId, async onTranslate(selection) {
      if (activeModule !== 'manage' || controller !== mountedController) return;
      const originRevision = revision;
      try {
        const response = await bridge.api('/api/research/tasks',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({kind:'translate',target_language:'zh',...selection})});
        if (originRevision !== revision || activeModule !== 'manage' || controller !== mountedController) return;
        try { sessionStorage.setItem('research-last-job',response.job.id); } catch { /* The task ID is also passed directly. */ }
        await activate('research',{retainCurrent:true,jobId:response.job.id});
      } catch (problem) {
        if (originRevision === revision && controller === mountedController) throw problem;
      }
    }};
    if (['manage','research'].includes(name)) options.onEvidence = async evidence => {
      if (activeModule !== name || controller !== mountedController) return;
      const origin = {controller:mountedController, request:revision};
      evidenceOrigin = origin;
      try { await bridge.onEvidence(evidence); }
      finally { if (evidenceOrigin === origin) evidenceOrigin = null; }
    };
    if (name === 'graph' && Array.isArray(intent?.documentIds)) options.selectedIds = () => [...intent.documentIds];
    try {
      if(name==='manage') controller=window.ResearchManage.mount(root,options);
      if(name==='research') controller=window.ResearchTasks.mount(root,options);
      if(name==='discovery') controller=window.ResearchExplore.mountDiscovery(root,options);
      if(name==='graph') controller=window.ResearchExplore.mountGraph(root,options);
      if(name==='data') controller=window.ResearchData.mount(root,options);
      mountedController = controller;
      await mountedController?.ready;
      if (request !== revision) return false;
      if (intent?.example) {
        if (!controller?.prepareExample) throw new Error('当前页面无法准备此示例，请刷新后重试。');
        await controller.prepareExample(intent.example);
      }
      return request === revision;
    } catch(problem) {
      if (intent) throw problem;
      if(request===revision) error(problem.message);
      return false;
    }
  }
  window.ResearchShell = Object.freeze({activate});
  document.querySelectorAll('[data-module]').forEach(button => button.addEventListener('click',()=>{
    if (button.dataset.module !== activeModule) activate(button.dataset.module);
    else {
      bridge.invalidateEvidenceNavigation?.();
      evidenceOrigin = null;
      clearReturnHistory();
      updateReturnButtons();
    }
  }));
  window.addEventListener('research:reader',()=>activate('reader', {
    retainCurrent:['manage','research'].includes(activeModule) && evidenceOrigin?.controller === controller && evidenceOrigin?.request === revision,
  }));
  function returnToPrevious() {
    const previous = returnHistory.at(-1);
    if (previous) activate(previous.module,{restorePrevious:true});
  }
  returnButton?.addEventListener('click',returnToPrevious);
  moduleReturnButton?.addEventListener('click',returnToPrevious);
  document.getElementById('modelSettingsButton').addEventListener('click',()=>window.ResearchModelSettings.open({api:bridge.api}));
  window.ResearchExamples?.mount({api:bridge.api, activate, onEvidence:bridge.onEvidence});
  window.addEventListener('research:model-configured',()=>{if(activeModule==='research') activate('research');});
  const dialog = document.getElementById('accountDialog');
  const form = document.getElementById('accountForm');
  const status = document.getElementById('accountStatus');
  const logout = document.getElementById('logoutButton');
  status.hidden = true;
  const accountNotice = (text, error = false) => window.ResearchNotices.show(text, error, status);
  document.getElementById('closeAccount').addEventListener('click',()=>dialog.close());
  dialog.addEventListener('close',()=>{form.reset();accountNotice('');});
  async function refreshAccount() {
    try {
      const {user}=await bridge.api('/api/account');
      document.getElementById('accountButton').textContent=user.mode==='account'?user.name:'工作空间';
      document.getElementById('accountDescription').textContent=user.mode==='account'?`当前工作空间：${user.name}。文献、研究任务和偏好独立保存。`:'本机默认空间与各账号的文献、任务和偏好分别保存。';
      form.hidden=user.mode==='account';
      logout.hidden=user.mode!=='account';
    } catch(problem) { accountNotice(problem.message, true); logout.hidden=false; }
  }
  document.getElementById('accountButton').addEventListener('click',()=>{refreshAccount();dialog.showModal();});
  form.addEventListener('submit',async(event)=>{
    event.preventDefault();
    const action=event.submitter?.value || 'login';
    const values=new FormData(form);
    accountNotice('正在处理…');
    try {
      await bridge.api(`/api/account/${action}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:values.get('name'),password:values.get('password')})});
      form.reset();sessionStorage.removeItem('research-last-job');window.location.reload();
    } catch(problem) { accountNotice(problem.message, true); }
  });
  logout.addEventListener('click',async()=>{
    try {await bridge.api('/api/account/logout',{method:'POST'});sessionStorage.removeItem('research-last-job');window.location.reload();}
    catch(problem){accountNotice(problem.message, true);}
  });
  refreshAccount();
})();
