"use strict";
(() => {
  const bridge = window.ResearchWorkspace;
  const root = document.getElementById('moduleRoot');
  const reader = document.getElementById('workspace');
  const mobileNavigation = document.querySelector('.mobile-navigation');
  let controller = null;
  let revision = 0;
  let activeModule = 'reader';
  let retainedManage = null;
  let evidenceOrigin = null;
  const returnButton = document.getElementById('readerReturnButton');
  function clearRetainedManage() {
    retainedManage?.controller?.destroy?.();
    retainedManage = null;
    if (returnButton) returnButton.hidden = true;
  }
  function retainManage() {
    const selected = window.getSelection();
    const selection = selected?.rangeCount && root.contains(selected.anchorNode) && root.contains(selected.focusNode)
      ? {anchorNode:selected.anchorNode, anchorOffset:selected.anchorOffset, focusNode:selected.focusNode, focusOffset:selected.focusOffset} : null;
    const state = {
      controller, scrollTop:root.scrollTop, scrollLeft:root.scrollLeft,
      focus:root.contains(document.activeElement) ? document.activeElement : null,
      selection, nodes:document.createDocumentFragment(),
      isText:root.querySelector('[role="tab"][aria-selected="true"]')?.textContent === '本页文字',
    };
    controller?.suspend?.();
    while (root.firstChild) state.nodes.append(root.firstChild);
    retainedManage = state;
    controller = null;
    if (returnButton) {
      returnButton.textContent = state.isText ? '返回文字核对' : '返回文献管理';
      returnButton.hidden = false;
    }
  }
  function restoreManage(request) {
    const state = retainedManage;
    retainedManage = null;
    if (returnButton) returnButton.hidden = true;
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
    root.scrollTop = state.scrollTop;
    root.scrollLeft = state.scrollLeft;
    // A resumed preview may finish its layout on the next frame. Do not allow
    // this correction to affect a later navigation.
    requestAnimationFrame(() => {
      if (request !== revision || activeModule !== 'manage') return;
      root.scrollTop = state.scrollTop;
      root.scrollLeft = state.scrollLeft;
    });
  }
  function error(message) {
    const element = document.createElement('p');
    element.className = 'module-error';
    element.textContent = message;
    root.replaceChildren(element);
  }
  async function activate(name, intent) {
    const restoring = name === 'manage' && intent?.restoreManage && retainedManage;
    const retaining = name === 'reader' && intent?.retainManage && activeModule === 'manage' && controller;
    bridge.invalidateEvidenceNavigation?.();
    evidenceOrigin = null;
    if (retaining) retainManage();
    else {
      controller?.destroy?.();
      controller = null;
      if (!restoring) clearRetainedManage();
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
    if (name === 'reader') return true;
    if (restoring) { restoreManage(request); return true; }
    let mountedController = null;
    const options = {...bridge, async onTranslate(selection) {
      try {
        const response = await bridge.api('/api/research/tasks',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({kind:'translate',target_language:'zh',...selection})});
        sessionStorage.setItem('research-last-job',response.job.id);
        await activate('research');
      } catch (problem) { window.alert(problem.message); }
    }};
    if (name === 'manage') options.onEvidence = async evidence => {
      if (activeModule !== 'manage' || controller !== mountedController) return;
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
  document.querySelectorAll('[data-module]').forEach(button => button.addEventListener('click',()=>activate(button.dataset.module)));
  window.addEventListener('research:reader',()=>activate('reader', {
    retainManage:activeModule === 'manage' && evidenceOrigin?.controller === controller && evidenceOrigin?.request === revision,
  }));
  returnButton?.addEventListener('click',()=>{
    if (retainedManage && activeModule === 'reader') activate('manage',{restoreManage:true});
  });
  document.getElementById('modelSettingsButton').addEventListener('click',()=>window.ResearchModelSettings.open({api:bridge.api}));
  window.ResearchExamples?.mount({api:bridge.api, activate, onEvidence:bridge.onEvidence});
  window.addEventListener('research:model-configured',()=>{if(activeModule==='research') activate('research');});
  const dialog = document.getElementById('accountDialog');
  const form = document.getElementById('accountForm');
  const status = document.getElementById('accountStatus');
  const logout = document.getElementById('logoutButton');
  document.getElementById('closeAccount').addEventListener('click',()=>dialog.close());
  dialog.addEventListener('close',()=>{form.reset();status.textContent='';});
  async function refreshAccount() {
    try {
      const {user}=await bridge.api('/api/account');
      document.getElementById('accountButton').textContent=user.mode==='account'?user.name:'工作空间';
      document.getElementById('accountDescription').textContent=user.mode==='account'?`当前工作空间：${user.name}。文献、研究任务和偏好独立保存。`:'本机默认空间与各账号的文献、任务和偏好分别保存。';
      form.hidden=user.mode==='account';
      logout.hidden=user.mode!=='account';
    } catch(problem) { status.textContent=problem.message; logout.hidden=false; }
  }
  document.getElementById('accountButton').addEventListener('click',()=>{refreshAccount();dialog.showModal();});
  form.addEventListener('submit',async(event)=>{
    event.preventDefault();
    const action=event.submitter?.value || 'login';
    const values=new FormData(form);
    status.textContent='正在处理…';
    try {
      await bridge.api(`/api/account/${action}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:values.get('name'),password:values.get('password')})});
      form.reset();sessionStorage.removeItem('research-last-job');window.location.reload();
    } catch(problem) { status.textContent=problem.message; }
  });
  logout.addEventListener('click',async()=>{
    try {await bridge.api('/api/account/logout',{method:'POST'});sessionStorage.removeItem('research-last-job');window.location.reload();}
    catch(problem){status.textContent=problem.message;}
  });
  refreshAccount();
})();
