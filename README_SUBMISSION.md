# 研序 可溯源科研助手 Agent

参赛者：郭剑宇。参赛形式：个人。指导老师：无。学校、报名组别及报名号、代码仓库评审地址和公网演示地址：待回填，以报名个人中心为准。

研序围绕论文导入、原页阅读、问答比较、翻译、研究追踪、引用图谱和写作绘图构建工作台。后端采用Python、FastAPI与SQLite，前端采用HTML、CSS和JavaScript。原文解析使用PyMuPDF与项目内OCR数据，英文检索采用本地BGE与BM25融合，语言任务接入自行配置的在线模型。功能和具体安装步骤见根目录README.md。

## 本次提交材料

2026年10月31日整理新版本地提交材料。选定视频为真实页面演示v5，时长5分34秒、1920×1080、30fps，剪映原生“解说小帅”正常1倍速。视频SHA-256为869e3335c8f5144eba8a2854827d75051a586f42a84d8b51dc9069b884d72dd2。技术文档和AI创作说明同步更新，完整源码、Git历史及文件摘要以包内清单记录的冻结提交为准。

视频中的问答、多篇比较、选文翻译和论文框架为本轮实际Flash任务，共35次请求。事实引用、翻译检查类型和演示边界见[实录记录](docs/development/166-v5-real-ai-capture-and-local-evidence-20261030.md)和[成片记录](docs/development/167-v5-real-ui-film-delivery-20261030.md)。本轮演示不等同于全部软件、官方资料或外部长期效果通过验收。

## 安装运行

推荐Python 3.12。完整依赖使用requirements-dev.txt；仅运行基础应用可使用requirements.txt。环境变量模板.env.example不含真实密钥，也不会自动加载到宿主Python进程。

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pip check
.\scripts\start.ps1
```

浏览器打开http://127.0.0.1:8765。运行时保持终端开启，按Ctrl+C停止。不要对同一数据目录启动多个实例或多个Uvicorn worker。本次打包不安装依赖、不下载模型；新机器首次安装和公网部署尚未验收。

问答、翻译和写作需自行配置可用模型。可在本机默认空间的“模型设置”输入密钥，或在启动前设置RESEARCH_MODEL_BASE_URL、RESEARCH_MODEL和RESEARCH_MODEL_API_KEY。密钥不得提交Git、录入文档或公开分享。真实模型任务可能产生费用；后续不再安排Pro付费复测，本次材料整理不发起模型请求。

本地向量权重与OCR语言数据不随源码包再分发，准备步骤与来源清单见README.md和项目文档。运行期不隐式下载；数据缺失时相应通道降级或不可用。公开论文来源和演示输入见docs/demo-examples.txt，原论文和个人资料库不纳入源码包。

独立演示数据使用scripts/run_submission_demo.py时，只有显式传入--enable-worker才执行后台研究任务；省略该选项只提供隔离页面及既有记录展示。不要把未启用后台执行的任务入队视为真实模型成功调用。使用独立数据目录，参数见启动器--help。

## 版本与恢复

新版技术文稿和AI创作说明位于docs/submission/20261031-v4，实际视频脚本位于docs/submission/video-real-ui-20261030-v5，打包工具位于scripts。旧材料目录保持原样，以新包内清单识别本次视频、文档和源码版本。

源码ZIP与完整Git bundle分别保存。可在新目录从bundle克隆，核对冻结HEAD并执行git fsck；普通源码ZIP不含.git，不能单独证明完整开发历史。Git bundle也不能替代官方要求的GitHub/Gitee评审访问地址。

Dockerfile.submission与compose.submission.yaml为部署草案，未构建或部署。视频及本地材料已经准备；代码托管、公网演示、必要评审账号、报名字段和实际网盘分享链接仍待落实。本轮不推送仓库、不部署、不上传网盘或提交比赛表单。

## 验证边界与AI协同

2026年10月27日1277项回归为相应源码版本的历史结果，本轮未重新执行。复杂低清扫描结构、通用跨语言检索与去重、官方资料包、新重型通道持续并发和部分外部效果仍有缺口。E5开发检索19/96、既有混合18/96；上下文实验最好加权35/96，尚未达标且未启用为生产默认。完整失败与阶段记录保存在docs/development。

Pro付费复测已取消，其原定最终质量范围没有取得完整验收结论。不得将历史测试数量、公开替代论文或本轮Flash演示扩展解释为全部赛题通过。

Codex辅助代码、界面、测试、排错和材料整理；参赛者提供需求、体验反馈、实施选择和材料确认。DeepSeek用于应用语言任务，剪映原生朗读用于视频配音。实际Prompt版本保存在research_workspace/prompts，不公开完整私人聊天，不把AI协作改写为纯人工独立开发。
