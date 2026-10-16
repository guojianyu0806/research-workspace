# 全赛题检查与补齐清单（2026-10-16）

本表是本轮开始时的基线，不把旧规划的未勾选框当作未实现。状态按代码、回归和真实验收范围分别填写；后续修复记录另列，旧版资料不覆盖。

共52项，来源：本次重新读取的官方detailContent及初始逐项验收编号。P0为基础证据链/明确功能故障，P1为核心缺口或正式验收，P2为评分强化或口径待确认。

| 编号 | 优先级 | 基线状态 | 尚缺内容 | 实现依据 |
|---|---|---|---|---|
|M1-01|P0|已实现，合成接口验收|批量失败隔离与真实PDF再核对|research_workspace/api.py, research_workspace/library.py|
|M1-02|P0|部分实现|已有arXiv标识/正文五词指纹；尚非向量语义去重，需改名v1/v7及不同论文反例|research_workspace/library.py|
|M1-03|P0|已实现，合成验收|保留/切换活动版本及历史证据用真实版本再验收|research_workspace/library.py|
|M1-04|P1|部分实现|作者机构、复杂公式LaTeX、扫描版表格、矢量图语义仍为候选或未实现|research_workspace/structure.py, research_workspace/layout.py|
|M1-05|P0|发现实现缺口|版面服务给出双栏建议，但正式入库按全页y排序，需接通正确阅读顺序|research_workspace/library.py, research_workspace/layout.py|
|M1-06|P0|已实现，公开替代样本验收|实际OCR曾通过；官方扫描件未取得，需重新核对OCR文字和锚点|research_workspace/library.py|
|M1-07|P0|已实现，公开样本工程验收|已有541页样本和旧benchmark，需本轮重跑并检查全部页完成|research_workspace/library.py|
|M1-08|P0|已实现，合成验收|调度/取消/重试/不可变证据测试需纳入完整回归|research_workspace/library.py|
|M1-09|P2|已实现，有限逐块差异|真实版本差异粒度和对齐质量待人工验收|research_workspace/library.py|
|M2-01|P0|部分真实模型验收|已有机制/数值/任务问答；机构、优化参数、GPU等官方细节题仍需补查|research_workspace/intelligence.py|
|M2-02|P0|已实现，真实Pro及浏览器验收|045已核对59处自身引用和返回；保持固定版本不破坏|research_workspace/intelligence.py|
|M2-03|P0|部分真实模型验收|两篇架构/训练任务已通过；其他官方比较维度尚未逐项验收|research_workspace/intelligence.py|
|M2-04|P1|部分实现|当前BM25+查询扩展/RRF，缺真实向量通道及检索效果对照|research_workspace/intelligence.py|
|M2-05|P0|已实现，真实Pro验收|拒答、条件限制、独立引用已验证；继续回归防止退化|research_workspace/intelligence.py|
|M2-06|P2|部分实现|已有任务SSE，尚非模型逐token答案；不得用伪打字动画冒充|research_workspace/intelligence.py, research_workspace/jobs.py|
|M3-01|P0|已实现，阅读器样本验收|复杂单双栏阅读顺序须随M1-05修复；官方长文档页面体验待验收|research_workspace/api.py|
|M3-02|P1|已实现，样本及浏览器验收|编号引用与返回已验证，作者年份引用样式支持范围仍有限|research_workspace/structure.py|
|M3-03|P1|已实现，有真实模型记录|局部选文、预览与返回有阶段证据；官方样本再检查|research_workspace/intelligence.py|
|M3-04|P1|已实现，正式整篇验收不足|可分块翻译和导出；完整真实论文的缺段/术语一致性待验收|research_workspace/jobs.py|
|M3-05|P1|已实现，样本交互验收|块级双向联动存在，长译文混合公式场景待验收|research_workspace/jobs.py|
|M3-06|P0|部分真实模型验收|有公式/代码保护回归和真实翻译记录，复杂公式与跨块情况需扩展|research_workspace/intelligence.py|
|M4-01|P1|已实现，真实联网验收不足|arXiv与书目身份核对/下载/入库已接入，需现场联网执行完整链路|research_workspace/discovery.py, research_workspace/reference_discovery.py|
|M4-02|P1|已实现，接口验收|仅元数据/无全文/下载失败等状态仍需真实网络核对|research_workspace/discovery.py|
|M4-03|P2|部分实现|规则相关性筛选可恢复，不等于客观学术含金量判定|research_workspace/reference_discovery.py|
|M4-04|P1|已实现，模拟调度验收|真实arXiv定时增量检查与重启后调度待验收|research_workspace/discovery.py|
|M4-05|P1|部分实现|标题/反馈推荐已接入；缺核验关联GitHub代码后的优先排序|research_workspace/discovery.py|
|M4-06|P1|已实现，接口验收|喜欢/踩持久化和排序测试已有；真实推荐效果未评估|research_workspace/discovery.py|
|M4-07|P2|部分实现|独立账号文献偏好已有，未实现跨用户协同过滤；官方口径需确认|research_workspace/accounts.py, research_workspace/discovery.py|
|M4-08|P1|已实现，模拟故障验收|网络限额/超时有测试，真实源失效与恢复待验收|research_workspace/discovery.py|
|M5-01|P1|已实现，真实大图验收缺失|21篇合成测试通过；尚无20篇以上真实全文引用图谱验收|research_workspace/graph.py|
|M5-02|P1|已实现，合成来源验收|需对真实图谱抽查本地/外部节点及引用边原句|research_workspace/graph.py|
|M5-03|P1|已实现，算法测试通过|PageRank/局域角色存在；真实语料解释与交互待验收|research_workspace/graph.py|
|M5-04|P0|已实现，真实Pro验收|044补齐三篇九个维度，各结论自身引用和审核已验收|research_workspace/review_completion.py|
|M5-05|P1|部分真实模型验收|后续文献查询及逐证据判定存在，实时全文搜索完整性不足|research_workspace/future_verification.py|
|M5-06|P1|已实现，有范围与状态约束|不能把没找到说成确定未解决；真实新论文场景待验收|research_workspace/future_verification.py|
|M6-01|P0|已实现，真实Pro验收|045三节事实/未来计划/来源清单已验收|research_workspace/intelligence.py|
|M6-02|P0|已实现，真实Pro验收|拟开展计划与已知结果分开，继续回归|research_workspace/intelligence.py|
|M6-03|P1|部分实现|结构图规范和SVG实际渲染已有；应补可直接运行的通用图脚本导出|research_workspace/visualization.py|
|M6-04|P1|已实现，样例数据验收|CSV/折线/柱状/脚本已接入；再验证导出脚本独立运行及真实数据变化|research_workspace/visualization.py|
|M6-05|P2|部分实现，定义待确认|已有三维分层雷达图，类别高度说明明确；比赛具体定义待确认|research_workspace/radar.py|
|M6-06|P1|部分实现|现有确定性图注给范围，不含显著性/因果；应增加有数据依据的趋势说明|research_workspace/visualization.py|
|M6-07|P1|已实现，部分真实模型验收|已有学术翻译与保护测试，整篇术语一致性仍待验收|research_workspace/intelligence.py|
|M6-08|P1|已实现，数据回归验收|字段/缺失/归一化/原数据SHA均有记录|research_workspace/visualization.py|
|M6-09|P1|已实现，受控模板|未执行用户任意代码；需要在独立目录实跑导出脚本|research_workspace/visualization.py|
|X01|P1|部分实现|任务内有检索/审核/补全编排；跨检索下载解析问答仍需完整工作流验收|research_workspace/jobs.py, research_workspace/discovery.py|
|X02|P1|部分实现|有状态/事件/来源；长时间事实审核阶段细化进度不足|research_workspace/jobs.py|
|X03|P0|已实现，故障回归验收|中断未知结果不自动重放，已验证取消和恢复|research_workspace/jobs.py|
|X04|P1|部分实现|步数/条数/超时边界存在；缺每任务完整模型token与费用用量台账|research_workspace/intelligence.py|
|X05|P0|本地边界已实现|公网部署需单独完成认证/限流/密钥环境配置验证|research_workspace/api.py|
|X06|P0|已实现，隔离回归|真实多账户浏览器验收仍需补充；向量缓存新增时必须保留边界|research_workspace/accounts.py|
|X07|P1|尚未完成部署验收|无公网演示、独立安装复现及部署版并发实测|research_workspace/api.py|
|X08|P0|已实现，多轮真实Pro验收|结构、来源、事实和完整性分别审核，记录不包含私有推理|research_workspace/intelligence.py|

交付材料另核：远程仓库未配置；README部分状态过期；正式≤30页PDF和5–8分钟1080p视频未制作；公网地址/评审账号未验收。准备可以本地完成，实际发布与正式提交需要明确目标和授权。

本轮执行顺序：先修正式入库的双栏阅读顺序并重跑真实PDF/541页/OCR；再补语义检索与去重（依赖安装待授权）、真实图谱与联网链路；之后完善图脚本/图注、部署复现。官方资料包始终单独列待验收，不以公开替代样本冒充。
