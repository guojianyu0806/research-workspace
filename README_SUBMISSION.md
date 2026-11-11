# 研序 可溯源科研助手 Agent

参赛者：郭剑宇。参赛形式：个人。指导老师：无。学校、报名组别及报名号、代码仓库评审地址和公网演示地址：待回填，以报名个人中心为准。

研序围绕论文导入、原页阅读、问答比较、翻译、研究追踪、引用图谱和写作绘图构建工作台。后端采用Python、FastAPI与SQLite，前端采用HTML、CSS和JavaScript。原文解析使用PyMuPDF与项目内OCR数据，英文检索采用本地BGE与BM25融合，语言任务接入自行配置的在线模型。功能和具体安装步骤见根目录README.md。

## 本次提交材料

2026 年 11 月 11 日整理 v6 正式网盘材料，应用和源码 ZIP 冻结于 `3b4d6676f269fcbac58a3f792f72182cd509df62`。本地交付目录为 `deliverables/郭剑宇_研序_正式网盘材料_20261111_v6`，包内清单记录应用基线、文稿 Git 提交、视频版本和逐文件摘要。

1. **第 5 项**上传 `01_上传百度网盘_作品文件` 整个目录，填写有效分享链接及适用的提取码。目录含 v6 源码与 Git 历史 ZIP、技术 PDF、作品介绍 PDF 和 Flash 验收，以及 v5 演示 MP4 与校验清单。
2. **第 6 项**从 `02_表单第6项_作品介绍文档` 直接上传唯一的 v6 作品介绍 PDF。它与第 5 项包内介绍逐字节一致，按表单七项要求补充了新版功能、实测结果和截图。[可编辑文稿](docs/submission/20261111-v6/project-introduction.md)
3. **第 7 项**发布选定 v5 视频后填写 B 站、抖音等平台的公开视频链接。该片为 `064dc58` 阶段实录，时长 5 分 34 秒、1920×1080、30 fps，剪映原生普通话配音为正常 1 倍速。视频 SHA-256 为 `869e3335c8f5144eba8a2854827d75051a586f42a84d8b51dc9069b884d72dd2`。[实录记录](docs/development/166-v5-real-ai-capture-and-local-evidence-20261030.md) · [成片记录](docs/development/167-v5-real-ui-film-delivery-20261030.md)

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

新版作品介绍和新版配图位于 `docs/submission/20261111-v6`，历史作品介绍和画面位于 `docs/submission/20261031-v5`。正式网盘材料采用已经验收的应用快照，后续的材料文稿提交记录在当前分支历史中。

源码 ZIP 内含 694 个跟踪文件、可恢复两个项目分支的 Git bundle 及操作说明，候选分支可达 322 条提交。按包内说明可在新目录恢复并执行 `git fsck`。正式评审还需提供可访问的 GitHub 或 Gitee 仓库地址。

Dockerfile.submission与compose.submission.yaml为部署草案，未构建或部署。视频及本地材料已经准备；代码托管、公网演示、必要评审账号、报名字段和实际网盘分享链接仍待落实。本轮不推送仓库、不部署、不上传网盘或提交比赛表单。

## 验证边界与AI协同

应用快照 `3b4d667` 通过 146 项本地专项测试。两条真实 Flash 任务为 BERT 与 Transformer 比较和基于结果的追问，核对 4 条结论、6 处原文引用和 8 条工具回执，58 项 Edge 检查通过。Transformer 更新到解析第 4 版后，追问仍引用比较锁定的 Transformer 第 3 版和 BERT 第 1 版。上一提交 `9207d2e` 的 1389 项全量回归、10 月 27 日的 1277 项本地回归和 v5 视频中的四个 Flash 场景各按原版本记录。

复杂低清扫描结构、通用跨语言检索与去重、官方资料包、新重型通道持续并发和部分外部效果仍有待验收项。E5 开发检索为 19/96，既有混合为 18/96。上下文实验最好加权 35/96，尚未启用为生产默认。阶段记录保存在 `docs/development`。

Pro付费复测已取消，其原定最终质量范围没有取得完整验收结论。不得将历史测试数量、公开替代论文或本轮Flash演示扩展解释为全部赛题通过。

Codex辅助代码、界面、测试、排错和材料整理；参赛者提供需求、体验反馈、实施选择和材料确认。DeepSeek用于应用语言任务，剪映原生朗读用于视频配音。实际Prompt版本保存在research_workspace/prompts，不公开完整私人聊天，不把AI协作改写为纯人工独立开发。
