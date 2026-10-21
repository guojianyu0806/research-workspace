# E5受控下载、离线编码及数值核对

日期：2026年10月21日。121方案S3的候选检索模型准备，不是阅读区检索或跨语去重整体验收。

用户已明确授权145冻结清单中的五文件，仅下载到本项目目录并作本地验收。清单保持原提案状态，未修改或扩大下载对象；未安装软件或依赖、不覆盖英文/MiniLM、不调用Pro。模型为`intfloat/multilingual-e5-small`，revision `614241f622f53c4eeff9890bdc4f31cfecc418b3`，五文件共135430817字节，约129.16 MiB。

第一次下载因ConnectError结束，0文件成功，收据`artifacts/e5-model-download/20261021T082032357777Z/receipt.json`保留。随后对同一固定小配置验证传输：直连ConnectTimeout，现有HTTP传输配置返回200且内容摘要正确。确认原进程已终止后重试，`artifacts/e5-model-download/20261021T082329195024Z/receipt.json`五文件全部通过大小及SHA-256核对。没有打印代理配置、凭据或CDN签名查询参数，没有换镜像或自动下载其他模型。

文件保存在`data/models/multilingual-e5-small-onnx-int8-614241f`；旧文件存在时只核对，不覆盖。验证后独立创建`data/models/e5-small-model.json`，指纹`d1ac840f43e5459c666be88af9fae12fd394ccff6c76e33f829860754a00d666`。同为384维不复用旧向量，指纹包括权重、前缀、分块及池化策略。

候选编码器直接使用已安装ONNX Runtime与tokenizers、本地CPU文件路径；没有自动联网下载分支。[发布方用法](https://huggingface.co/intfloat/multilingual-e5-small/blob/614241f622f53c4eeff9890bdc4f31cfecc418b3/README.md#usage)规定问句和段落使用不同前缀及带掩码均值池化。实现逐一检查加前缀和特殊符号后的实际512-token上限；分块记录仅对应原始字符范围，前缀不混入正文。每个原文子串同时验证两种前缀预算，保证检索时切换模式不触发静默截断。

补齐使用tokenizer的实际`<pad>` ID 1，不能照抄模型config中的0。单独构造attention mask，池化时排除补齐位置，归一化后输出有限384维向量。分块、输入检查、文件摘要和ONNX输出不满足条件时明确失败，不对缺少的输入生成向量。

## 实测

- 首次实际CPU启动通过，中英文文献和中文问句均返回384维归一化向量。AVX512命名的INT8图在当前CPUExecutionProvider实际可执行，不需要其他图或回退下载。
- E5新增19项合同，加既有MiniLM及代际索引测试，共61项通过，见`artifacts/e5-contract/20261021-initial/junit.xml`。覆盖8语/emoji原文尾部、前缀预算、非法输入、文件损坏/越界、加载后清单变化、两种模式、补齐掩码、零/非有限输出。
- 真正禁用所有Python socket连接后，实际权重运行`artifacts/e5-model/20261021T082859155709Z/report.json`，14项检查通过，连接尝试和付费调用均0。用另一份原始tokenizer及直接ONNX Session，独立切片求均值和归一化，核对文献/问句向量与余弦分数；旧英文/MiniLM全部清单和权重摘要前后相同。
- 8条合成语言探针均将预期句排在4候选首位，仅作为启动及数值检查，不是160题论文检索、80对身份判定或真实多语效果验收。

下一步在隔离公共论文工作区按原96题开发集实际建索引和计分，保留原MiniLM结果及BERT64题留出边界。新编码器未成为阅读器默认，也未启用跨语自动合并。完整回归和后续检索质量分别记录，不能以本阶段通过代替S3整体完成。

旧模型、原件和历史引用保留；新增本阶段记录及独立模型目录，无旧成品覆盖。源码进行真实增量本地提交，不推送或发布。
