"use strict";
(() => {
  const bridge = window.ResearchWorkspace;
  const root = document.getElementById('moduleRoot');
  const reader = document.getElementById('workspace');
  const mobileNavigation = document.querySelector('.mobile-navigation');
  let controller = null;
  let revision = 0;
  let activeModule = 'reader';
  function error(message) {
    const element = document.createElement('p');
    element.className = 'module-error';
    element.textContent = message;
    root.replaceChildren(element);
  }
  async function activate(name, intent) {
    bridge.invalidateEvidenceNavigation?.();
    activeModule = name;
    document.body.classList.toggle('reader-active', name === 'reader');
    window.dispatchEvent(new CustomEvent('research:module-change', {detail: {module: name}}));
    const request = ++revision;
    controller?.destroy?.();
    controller = null;
    root.replaceChildren();
    root.scrollTop = 0;
    root.scrollLeft = 0;
    document.querySelectorAll('[data-module]').forEach(button => button.setAttribute('aria-pressed',String(button.dataset.module===name)));
    reader.hidden = name !== 'reader';
    mobileNavigation.hidden = name !== 'reader';
    root.hidden = name === 'reader';
    if (name === 'reader') return true;
    const options = {...bridge, async onTranslate(selection) {
      try {
        const response = await bridge.api('/api/research/tasks',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({kind:'translate',target_language:'zh',...selection})});
        sessionStorage.setItem('research-last-job',response.job.id);
        await activate('research');
      } catch (problem) { window.alert(problem.message); }
    }};
    if (name === 'graph' && Array.isArray(intent?.documentIds)) options.selectedIds = () => [...intent.documentIds];
    try {
      if(name==='manage') controller=window.ResearchManage.mount(root,options);
      if(name==='research') controller=window.ResearchTasks.mount(root,options);
      if(name==='discovery') controller=window.ResearchExplore.mountDiscovery(root,options);
      if(name==='graph') controller=window.ResearchExplore.mountGraph(root,options);
      if(name==='data') controller=window.ResearchData.mount(root,options);
      await controller?.ready;
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
  window.addEventListener('research:reader',()=>activate('reader'));
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
