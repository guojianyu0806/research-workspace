# 多语向量模型下载及离线准备

日期：2026年10月20日。对应121方案S3的依赖准备阶段。

## 授权与文件

用户明确授权按冻结清单下载六文件，仅限本项目目录及本地验收。原提案`142-multilingual-model-download-plan-20261020.json`保持冻结时的`proposed_not_downloaded`状态，不覆盖历史提案；当前实际结果以本记录及运行收据为准。

公开来源为[Qdrant多语MiniLM ONNX仓库](https://huggingface.co/Qdrant/paraphrase-multilingual-MiniLM-L12-v2-onnx-Q)，固定revision为`faf4aa4225822f3bc6376869cb1164e8e3feedd0`，发布元数据标记Apache-2.0。完整下载URL、大小及SHA-256已写入冻结JSON。

六文件共252139978字节，约240.46 MiB，保存在`data/models/multilingual-minilm-l12-v2-onnx-q-faf4aa4`。逐文件校验字节大小及SHA-256后才移入目标目录。已有文件只校验、不覆盖；失败的未验证内容留在隔离运行目录。未安装任何软件、依赖或系统程序，没有更改全局配置或下载其他权重。

首个禁用环境传输配置的尝试因ConnectTimeout失败，记录保留于`artifacts/model-download/20261020T024148224576Z/receipt.json`，没有成功下载文件。随后通过本机现有HTTP传输配置访问同一固定来源，六文件在`artifacts/model-download/20261020T024309787452Z/receipt.json`全部验证通过，下载耗时约31秒。收据不记录CDN签名查询参数，也不读取HF登录凭据。

## 独立运行配置

`prepare_multilingual_model.py`再次核对全部文件，创建独立运行清单`data/models/multilingual-model.json`。现有英文`semantic-model.json`及其权重没有覆盖。新模型指纹为`3e3d03f4b31ce9239a33fffd65f0151dbeeb8390f3c0a1044df3b9b531d60d57`，包含分块策略、模型revision和文件摘要；相同384维不意味着可以复用英文向量。

本项目已安装FastEmbed 0.9.0、ONNX Runtime及分词器。使用实际本地模型路径、`local_files_only=True`和CPU，不通过构造函数隐式下载。下载脚本与准备脚本属于显式操作入口，应用推理入口不负责下载。

## 验证边界

实际离线启动与分词器验证见143记录。下载及启动成功不能证明论文跨语言检索、跨语去重、译本身份判定或8语160题达到方案指标。阅读区入口、双索引融合、候选关系及完整专项仍按S3继续实施，不因为模型文件已到位标记完成。

旧模型、旧PDF、历史引文和已有验收文件保留。新增文件均位于项目目录，模型及大运行产物由`.gitignore`排除，不纳入源码提交。
