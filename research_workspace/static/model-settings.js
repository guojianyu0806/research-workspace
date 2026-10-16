"use strict";

(() => {
  let activeDialog = null;
  let number = 0;
  const node = (tag, className, text) => {
    const result = document.createElement(tag);
    if (className) result.className = className;
    if (text !== undefined) result.textContent = String(text);
    return result;
  };

  function open(options = {}) {
    activeDialog?.destroy();
    let destroyed = false;
    let saving = false;
    let accessible = false;
    const requestAbort = new AbortController();
    const listeners = [];
    const id = `research-model-${++number}`;
    const dialog = node("dialog", "ms-dialog");
    dialog.setAttribute("aria-labelledby", `${id}-title`);
    dialog.setAttribute("aria-describedby", `${id}-description`);
    const header = node("div", "ms-header");
    const title = node("h2", "ms-title", "连接 DeepSeek"); title.id = `${id}-title`;
    const close = node("button", "ms-close", "关闭"); close.type = "button";
    close.setAttribute("aria-label", "关闭模型设置并清空密钥输入");
    header.append(title, close); dialog.append(header);
    const description = node("p", "ms-description", "服务地址已固定为DeepSeek官方接口，只需选择模型并填写API密钥。");
    description.id = `${id}-description`; dialog.append(description);
    const endpoint = node("div", "ms-endpoint");
    endpoint.append(node("span", "", "官方地址"), node("code", "", "https://api.deepseek.com"));
    const docs = node("a", "ms-docs", "查看官方文档");
    docs.href = "https://api-docs.deepseek.com/en/"; docs.target = "_blank"; docs.rel = "noopener noreferrer";
    endpoint.append(docs); dialog.append(endpoint);
    const form = node("form", "ms-form"); form.autocomplete = "off";
    const modelLabel = node("label", "ms-field");
    const model = node("select", "ms-input"); model.disabled = true;
    for (const [value, label] of [["deepseek-flash", "DeepSeek Flash"], ["deepseek-v4-pro", "DeepSeek V4 Pro"]]) {
      const option = node("option", "", label); option.value = value; model.append(option);
    }
    modelLabel.append(node("span", "ms-label", "模型"), model); form.append(modelLabel);
    const keyLabel = node("label", "ms-field");
    const key = node("input", "ms-input"); key.type = "password"; key.required = true;
    key.minLength = 8; key.maxLength = 300; key.autocomplete = "off"; key.spellcheck = false;
    key.setAttribute("autocapitalize", "none"); key.setAttribute("aria-describedby", `${id}-retention`);
    key.placeholder = "粘贴API密钥"; key.disabled = true;
    keyLabel.append(node("span", "ms-label", "API密钥"), key); form.append(keyLabel);
    const retention = node("p", "ms-retention", "密钥仅在本次本机服务运行期间保留，不写入项目文件。重启服务后需重新填写；关闭此窗口会清空输入框。");
    retention.id = `${id}-retention`; form.append(retention);
    form.append(node("p", "ms-disclosure", "保存不会自动发起测试。之后执行研究任务时，所选资料会发送至DeepSeek，并可能消耗你的API额度。"));
    const status = node("p", "ms-status", "正在读取本机模型设置……"); status.setAttribute("role", "status");
    const save = node("button", "ms-save", "保存本次配置"); save.type = "submit"; save.disabled = true;
    form.append(status, save); dialog.append(form); document.body.append(dialog);

    function listen(target, event, handler) {
      target.addEventListener(event, handler);
      listeners.push(() => target.removeEventListener(event, handler));
    }
    function message(text, error = false) {
      if (destroyed) return;
      window.ResearchNotices.show(text, error, status);
    }
    async function api(path, init = {}) {
      if (typeof options.api === "function") return options.api(path, init);
      const response = await fetch(path, { credentials: "same-origin", cache: "no-store", ...init });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(typeof payload.detail === "string" ? payload.detail : "模型设置请求未完成。");
      return payload;
    }
    const controller = {
      destroy() {
        if (destroyed) return;
        destroyed = true; key.value = ""; requestAbort.abort();
        for (const remove of listeners) remove();
        if (dialog.open && typeof dialog.close === "function") dialog.close();
        dialog.remove();
        if (activeDialog === controller) activeDialog = null;
      },
    };
    activeDialog = controller;
    listen(close, "click", () => controller.destroy());
    listen(dialog, "cancel", (event) => { event.preventDefault(); controller.destroy(); });
    listen(dialog, "close", () => controller.destroy());
    listen(dialog, "click", (event) => {
      if (event.target !== dialog) return;
      const bounds = dialog.getBoundingClientRect();
      if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) controller.destroy();
    });
    listen(form, "submit", async (event) => {
      event.preventDefault(); if (saving || !accessible || !form.reportValidity()) return;
      if (key.value.length < 8 || key.value.length > 300 || /[\s\p{C}]/u.test(key.value)) {
        message("密钥须为8至300个字符，不能含空白或控制字符。", true); return;
      }
      saving = true; save.disabled = true; model.disabled = true;
      const selectedModel = model.value;
      const body = JSON.stringify({ api_key: key.value, model: selectedModel });
      key.value = ""; key.disabled = true;
      message("正在更新本次服务配置……");
      try {
        // The posted value is never stored in localStorage, sessionStorage, DOM
        // attributes, event details or logs. No automatic provider test follows.
        await api("/api/model", { method: "POST", headers: { "Content-Type": "application/json" }, body });
        window.dispatchEvent(new CustomEvent("research:model-configured", {
          detail: { configured: true, provider: "DeepSeek", model: selectedModel },
        }));
        controller.destroy();
        window.ResearchNotices.show("本次模型配置已保存。");
      } catch {
        message("配置未能保存。请确认已处于本机默认空间且没有运行中的模型任务，再重新输入密钥；必要时重启本机服务。", true);
      } finally {
        saving = false;
        if (!destroyed) { save.disabled = !accessible; model.disabled = !accessible; key.disabled = !accessible; }
      }
    });
    if (typeof dialog.showModal === "function") dialog.showModal();
    else { dialog.setAttribute("open", ""); dialog.setAttribute("aria-modal", "true"); }
    api("/api/model", { signal: requestAbort.signal }).then((configuration) => {
      if (destroyed) return;
      accessible = true;
      const currentModel = configuration.model || configuration.defaultModel;
      model.value = ["deepseek-flash", "deepseek-v4-pro"].includes(currentModel) ? currentModel : "deepseek-flash";
      model.disabled = false; key.disabled = false; save.disabled = false;
      status.textContent = configuration.configured ? "本次服务已有配置。密钥不会回显；如需更换，请重新输入。" : "尚未配置。填写后即可在研究任务中使用真实模型。";
      key.focus();
    }).catch(() => {
      status.textContent = "模型设置不可用，请关闭后重试。";
      message("无法读取模型设置。请确认本机服务可用，并退出登录回到本机默认空间后重试。", true);
    });
    return controller;
  }

  window.ResearchModelSettings = Object.freeze({ open });
})();
