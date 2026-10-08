# 工程启动记录

日期：2026年10月8日。

## 已核对

- 当前目录及父目录没有Git仓库，需建立本项目的独立仓库。
- 目录内原有比赛快照、摘要、实施规划和项目协作规则，保持原样纳入基线。
- 系统默认Python入口无效，已确认存在可调用的Python 3.12环境。
- 已有运行环境缺少FastAPI、Uvicorn、PyMuPDF、上传组件及部分测试依赖，依赖清单已准备；安装需遵守项目动作门禁。
- 现有Git姓名配置为郭剑宇，邮箱字段不是有效邮箱，已请求本项目提交邮箱。身份确认前不伪造作者或提交。

## 首阶段边界

优先实现本地文献库、异步解析、页面定位和原文检索。扫描件在OCR未配置时应报告缺少OCR能力，不能把空白抽取标为解析成功。官方测试集、商用模型配置与公网部署仍待后续落实。

## 技术参考

- [FastAPI文件上传文档](https://fastapi.tiangolo.com/tutorial/request-files/)
- [PyMuPDF文字提取文档](https://pymupdf.readthedocs.io/en/latest/recipes-text.html)
- [PyPI包元数据](https://pypi.org/)

以上用于接口和依赖准备，不代表功能或测试已经完成。
