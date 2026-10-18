# 流式传输预算与主库核对

根据当前官方Chat Completion协议，传输包括SSE包头、可选私有推理及最终用量字段，不能直接等同于公共JSON正文大小。新增回归以超过2MiB的私有推理分片加短正文验证，修复后的公共正文仍限制2MiB，SSE总传输单独限制16MiB，预览仅24000字符；私有推理不留存。官方参考：https://api-docs.deepseek.com/api/create-chat-completion ，2026-10-18读取核对。

108项相关回归通过，24.69秒；`artifacts/model-stream-budget-final-20261018.xml`。包括37项流式专项及模型调用、引用校验、账号任务、API和用量回执相关测试。不是108条真实模型回答，也不等于Pro验证。完整回归仍在进行，最终记录另存。

本机8765服务已加载前阶段新代码，健康检查正常且解析worker与研究worker均运行。重启前确认模型未配置、无排队/执行任务；未读取密钥或尝试从旧记录恢复配置。加载本阶段预算修复后还需最终服务重载核对。

重启前后只读快照一致：12篇文献、15次解析、7812个正文块、146个研究任务、465个检查点、420条模型调用记录的内容摘要均相同。`artifacts/model-stream-service-before.json`与`artifacts/model-stream-service-after.json`只保存计数及SHA-256。没有新增付费请求、重排历史任务或覆盖原始PDF；旧阶段文档与实验产物保留。
