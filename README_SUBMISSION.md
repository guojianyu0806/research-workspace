# 研序 可溯源科研助手 Agent

参赛者：郭剑宇。参赛形式：个人。指导老师：无。报名组别、报名号、学校信息、公开仓库地址和公网演示地址：待补充。

本项目围绕论文导入、阅读、问答、引用核对、研究追踪和写作绘图构建科研工作台。后端采用Python、FastAPI与SQLite，前端采用HTML、CSS和JavaScript；原文解析使用PyMuPDF与项目内OCR数据，问答接入兼容接口的大模型，英文检索采用本地BGE与BM25融合。完整功能、安装运行和费用说明见根目录README.md。

## 安装与运行

推荐Python 3.12。在项目目录创建专用虚拟环境，安装requirements-dev.txt。仅运行基础应用可安装requirements.txt；完整开发依赖包含FastEmbed及测试工具。环境变量模板为.env.example，不包含真实密钥，也不会自动加载到宿主Python进程。

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pip check
.\scripts\start.ps1
```

浏览器打开http://127.0.0.1:8765。运行过程中保持终端开启，按Ctrl+C停止。不要对同一数据目录启动多个实例或多个Uvicorn worker。新机器安装未在独立设备验收，不能将本机已有环境通过等同于异机部署通过。

问答、翻译和写作需自行配置可用模型。在界面模型设置输入密钥，或在启动前设置RESEARCH_MODEL_BASE_URL、RESEARCH_MODEL和RESEARCH_MODEL_API_KEY。密钥不得提交Git或写入公开材料。创建真实模型任务可能产生费用，本轮未调用Pro。

本地向量模型和OCR语言数据未放入源码包。已有安装与模型准备步骤见README.md；运行期不隐式下载权重。无模型或语言数据时相应通道会明确降级或不可用，基础应用仍可启动。公开论文来源和可复制演示输入见docs/demo-examples.txt。

## 交付与复现

技术文档、录屏稿、提交表单、证据清单和本地打包工具位于docs/submission/20261027-v1与scripts。交付压缩包以自身manifest记录的完整Git提交为准。完整历史使用Git bundle保存，可在本地恢复；它不能替代官方要求的GitHub/Gitee仓库地址。

Dockerfile.submission与compose.submission.yaml是本地部署配置草案，未构建或部署，不能作公网可用性验收证据。公网演示、评审账号、实际5–8分钟1080p MP4和报名信息仍待补齐。

## 已知边界

六大模块均有实现，但复杂低清扫描结构、多语检索与去重及部分外部流程未全部验收。E5真实开发检索19/96，现有混合18/96；上下文实验最好加权35/96，仍未达标且未启用为生产默认。不得用测试项数或论文页数推算竞赛通过率。Pro最终复测、官方资料包与新增通道持续并发保持待验收。实际阶段证据及失败记录保留在docs/development。

AI辅助了代码、界面、测试、排错和材料整理。参赛者提供需求、体验反馈和实施决策；不将AI协助改写为全人工独立开发，不将原始私人聊天公开。实际运行Prompt文件和对应版本保留在research_workspace/prompts。
