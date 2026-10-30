# 剪映原生配音与视频生产准备

## 本轮范围与当前状态

用户已授权按23段文字脚本制作视频，并要求使用剪映里的语音音色。软件及已批准文字脚本的阶段基线为`d2c4e7d`；本轮生产脚本和素材另行留存，不改写软件的历史验收版本。

当前完成剪映原生配音生成、23段原声与分镜匹配、380秒旁白音轨及估时字幕准备。**新视频尚未完成**：音频、字幕和工程素材不能当作完整MP4或完整视听验收。没有进行新的Pro复测。

本轮证据根目录为`artifacts/video-production-jianying-20261030-v1/narration-native/`。以下文件路径均相对于该目录，不记录个人缓存位置或设备标识。

## 原生音色与母带来源

在剪映专业版11.6原生文本朗读界面选择“解说小帅”，先生成S01试样，再批量生成S02至S23。原生界面中S02至S23使用0.80x速度，S01试样保留原速。项目名称为“研序_科研主线演示_20261030_v4”。

`S01-native-timeline-proof.png`保留原生时间线证据，`batch-native-voice-speed-proof.png`保留批量配音及速度证据。`S01-native-origin.json`记录样音来源；批准稿快照保存在`approved-script-snapshot.json`，其中记录的脚本原件摘要为`13c367dc142a8fe39db3cb6e7708794b362039ce9f0e9f40ed092e84725f71a4`。

剪映原生界面导出的`yanxu_native_voice_master_v1.wav`实测为382.966712秒、44.1kHz双声道PCM16。该母带及23段原声均保留。原生源文件虽然使用WAV扩展名，ffprobe实际识别为24kHz单声道MP3；复制时保留原字节并按真实格式使用`.mp3`扩展名，没有重新调用TTS或替换声线。

## 用音频证据匹配分镜

23段原声使用随机文件名，不能按文件名、目录顺序或修改时间猜测S01至S23。`scripts/map_native_jianying_voice.py`使用八个语音频带的对数谱包络局部归一化互相关，以10ms时间步比较原速和0.80x候选，再将测得峰值位置与批准脚本的时间锚点核对。

23段均得到唯一分镜归属，起点与脚本锚点的差异为0或约0.01秒。匹配分值范围为0.8476286至0.9998349；多数段落约0.988至0.994。映射和原声摘要保存在`audio-map-v1.json`，按段号命名且字节一致的原声副本保存在`mapped-scenes-v1/`。

原母带的S22约在362.01秒开始，按原声长度和0.80x估算会延续到374.64秒；S23在374.00秒开始，存在约0.64秒边界重叠风险。因此最终音轨重新从23段独立原声排布，没有从这个混合重叠区截取来补片段。

## 最终旁白音轨

最终采用`assembly-v2/final-narration-v1.wav`，实测**380.000秒、48kHz单声道PCM16**。处理仅包括保音高变速、每段20ms淡入/淡出、按批准分镜起点排布及必要的线性音量留量，不生成新音色。

- S01也调整为0.80x，与其余旁白节奏统一；处理后长10.141333秒。
- S02至S21及S23使用0.80x。
- S22为适配12秒镜头并预留约0.35秒边界，采用最小必要加速`0.8672961373x`；实际结束在373.622708秒，距S23起点留0.377292秒。
- 所有段落均无跨镜重叠；最终全局线性增益为1.0，没有额外提升音量。

`assembly-v2/narration-assembly-metadata-v1.json`保留每段原声摘要、处理后文件、实际起止、速度、淡化及边界检查。全片解码退出0；FFmpeg音量检测均值为-23.3dB、峰值为-1.0dB。程序检查不代替人工完整试听。

S08、S09和S18各有约7至8秒的阅读留白，后续画面应安排真实原文、审核或条件核对，不应仅用无变化的空静止画面填满。

## 字幕范围与限制

最终采用`captions-final-v1/native-narration-captions-v1.srt`及同名`.ass`，共78条。字幕逐段重组后与批准稿原词一致，英文术语不拆；多数分句约14至24汉字宽，少数自然语义分句稍短，最大24汉字宽，最短1.42秒。已纠正“高低。”“结果。”等过短尾句和中文复合词切断。

时间估算结合原标点、实际语音能量分布及邻近能量谷值，不是机械平均分配。**没有使用ASR、强制对齐或完成逐字同步验收**，仍需最终视听校准。`captions-final-v1/caption-timing-metadata-v1.json`是最终字幕计数和时序的依据；`assembly-v2`及`assembly-v3`中的早期字幕不作为最终版本使用。

ASS设为1920×1080、微软雅黑40px、白字黑描边2.2px、底部68px及左右96px安全边距。已用FFmpeg真实渲染并查看`captions-final-v1/caption-style-proof-v1.png`，未见缺字或出画；`caption-style-smoke.json`保留解析退出0和ASS摘要。该图片仅为内部字幕样式检查，不是视频实拍画面。

## 证据摘要

| 文件 | SHA-256 |
| --- | --- |
| `approved-script-snapshot.json` | `9e01287f92e6ee1a41c090c7a3049c5567688436b79d692ec6697e04f9f70409` |
| `S01-native-timeline-proof.png` | `b228ca4e70bc96cb48d6ebb644ceedb318454ba92b0fd9e74e5323e7d7955fa2` |
| `batch-native-voice-speed-proof.png` | `b473dc8a29944d55e0aee75f78a95e48004d60d653ab364459d74b38a1b7ec73` |
| `yanxu_native_voice_master_v1.wav` | `13493d9737b8f5d515bc32dbb9350bdde3c6823502eb78bafb8af63a65821b16` |
| `audio-map-v1.json` | `d5d18235dbe7c4259d0166b0bee012f56cbde87fcc3a13e6d78aeb6b7a201e5e` |
| `assembly-v2/final-narration-v1.wav` | `7d00cb2a13535605cf5b7009895ddf0a87709cc617b621ceb85438939d07066d` |
| `assembly-v2/narration-assembly-metadata-v1.json` | `88da40b230dbade1999b3cdbd8435ba4af11d377065d7cc6e5a60e3098a3df11` |
| `captions-final-v1/native-narration-captions-v1.srt` | `869898c7ae9071ea670378011f1d6b97deccdf1153c6a2669d3fc7fcb0391316` |
| `captions-final-v1/native-narration-captions-v1.ass` | `08017b61ae8ca9fc4ba55681eb332b134ebcf7b72819df70ff44aef99842ad9e` |
| `captions-final-v1/caption-timing-metadata-v1.json` | `7d1adfd6491aec76ce880edb000badf28175c34d2699572ed18895374458161a` |

## 脚本边界与文件保护

已核查本轮三个音频脚本：`map_native_jianying_voice.py`只读取用户指定的23段原声和项目内音频证据，`prepare_native_jianying_narration.py`与`refine_native_narration_captions.py`只读取项目内已复制音频、批准稿及校验记录。它们不读取认证配置、账号日志或加密工程，不调用内部TTS接口，也没有网络TTS请求。正常成功输出仅包含音频基名、分镜号、匹配数值及项目输出路径。

脚本没有对剪映原工程写入、解密或重建，也没有修改技能库。旧视频、旧音频、旧技术文档、旧源码包和旧交付目录保持原样；原生母带、原声及未采用中间结果另行保留，未覆盖旧成品。

当前尚需完成新画面录制/制作、剪辑与原生配音匹配、字幕视听校准、最终MP4导出和整片复核。本阶段没有宣布视频完成、当前Pro质量通过、公网部署或正式提交完成。相关脚本和本文档由主代理检查后作真实阶段提交，本文档编写阶段没有自行提交Git。

## 源目录参数化复核

主代理复核后，`map_native_jianying_voice.py`移除了本机个人源目录默认值，改为显式必填`--source-dir`。参数仅接受已经存在、恰好包含23个普通`.wav`文件且没有其他条目的目录；不递归扫描，不跟随源文件符号链接，不扩大到其他工程。输出仍限于项目内既定音频目录，已有输出拒绝覆盖。

重新执行时应由操作者填写准确的本项目原生朗读目录，例如：

```powershell
.venv\Scripts\python.exe scripts\map_native_jianying_voice.py --source-dir "<仅含本项目23个原生WAV文件的目录>" --analyze-only --output artifacts\video-production-jianying-20261030-v1\narration-native\audio-map-analysis-new-version.json
```

本次参数调整仅执行AST语法检查和`--help`检查，没有重新生成TTS、重跑音频匹配或改变任何已保存音频、字幕与证据摘要。
