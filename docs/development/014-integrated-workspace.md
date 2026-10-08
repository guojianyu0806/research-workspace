# 六模块工作台集成与浏览器验证

日期：2026年10月8日。

工作台将原文阅读、文献管理、问答与写作、发现与追踪、引用图谱、数据与图表放入同一导航。新增账号入口和DeepSeek临时配置弹窗；原文证据跳转继续绑定文献、解析记录、页码和区域坐标。模型设置样式使用独立前缀，避免影响文献管理页面。

桌面浏览器使用现有Playwright与Edge，未安装额外依赖。测试运行在独立的8766服务及 `artifacts/integration/20261008T1513587450929Z/data`，账号、PDF/CSV合成样本与导出文件明确标为测试资料，没有写入默认真实文献空间。

## 已执行的浏览器验证

| 脚本 | 实际覆盖 | 结果与记录 |
| --- | --- | --- |
| `scripts/workspace_smoke.cjs` | 六模块挂载、模型弹窗关闭清空、版本比较、标题/引用及原文位置、单块选区、CSV真实折线和柱状图、SVG/Python/JSON下载、双账号隔离及重新登录 | 14项通过；控制台、页面脚本和HTTP错误均为0。记录 `artifacts/browser/workspace-2026-10-08T15-31-07-765Z-muzp1qid_fdaa8222/report.json` |
| `scripts/browser_smoke.cjs` | 批量导入、重复文件、跨文献/当前文献检索、高亮、空结果、末页、重新解析及旧证据、390像素手机三视图 | 7项通过；记录 `artifacts/browser/2026-10-08T15-32-09-034Z/` |
| `scripts/bilingual_smoke.cjs` | 不等长原文/译文按段双向联动、重新渲染保持位置、版本化出处回调、不完整覆盖提示、桌面及手机布局 | 6类检查通过；使用明确合成译文，没有模型调用。记录 `artifacts/browser/bilingual-2026-10-08T15-20-40-735Z/results.json` |
| `scripts/future_result_smoke.cjs` | 核验状态与取舍分组、原句/官方链接/检索时间/缓存、未完成阶段恢复、非官方链接和HTML文字处理 | 8项通过；完全离线合成夹具，网络请求及浏览器错误均为0。记录 `artifacts/browser/future-fixture-2026-10-08T15-38-06-786Z-f23aa74a/report.json` |

真实公开Attention论文v7在当前规则下识别25个标题、40个编号书目及63处引用。两份Attention版本构成的局域图谱有2个本地节点、0条唯一确认引用边、76条未确认书目；按实际数据记录，不补造关系或声称完成20篇真实论文验收。

浏览器报告保存当时Git HEAD、工作区是否有未提交修改及各源文件SHA-256。第一轮账号初始化故障和后续测试断言精度调整的失败物料均保留，新一轮使用新目录。最终验证不能用一个成功截图代替真实开发和失败记录。

本阶段最终完整后端回归：212项通过，记录 `artifacts/verification/20261008T1543112734200Z/pytest.txt`。有一条Starlette TestClient针对httpx的弃用提醒，不影响本次结果。前一轮全量回归发现Windows锁文件首字节初始化竞态，已用确定性测试复现并修复；同时把旧单步骤任务取消测试改为综述场景，并为新的Future Work两阶段保存单独验证在途取消。失败记录 `artifacts/verification/20261008T1538524508666Z/pytest.txt` 保留。

## 复现

先启动隔离数据目录的服务，再设置已有工具的位置，不安装新工具：

```powershell
$env:PLAYWRIGHT_MODULE = '<已有Playwright模块路径>'
$env:BROWSER_EXECUTABLE = '<已有Edge或Chrome可执行文件路径>'
$env:BASE_URL = 'http://127.0.0.1:8766'
node scripts/workspace_smoke.cjs
node scripts/bilingual_smoke.cjs
node scripts/future_result_smoke.cjs
```

原阅读流程另用 `SMOKE_URL` 指定端口，并传入公开Attention v7、v1的本地PDF路径。所有脚本创建新的验证目录，保留旧物料。测试账号仅用于隔离环境，不作为真实用户成果。

真实模型质量仍待本机配置后统一验收；离线协议及浏览器合成输出检查不能代替这一阶段。细粒度版式解析、跨语言语义检索、3D雷达图、官方资料包及最终比赛交付的剩余范围见README。
