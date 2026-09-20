# 腾讯云超长录音转写：独立联调

这套工具独立于 Flask 页面和工作台数据库，用于先验证真实长录音能否完成：

`本地音频 → 私有 COS → 腾讯云录音文件识别 → 本地结果`

## 1. 安装依赖

在 `workbench` 目录执行：

```powershell
python -m pip install -r requirements-asr.txt
```

## 2. 配置环境变量

只在本机设置密钥，不要把密钥写入文件、代码、聊天或截图。

推荐运行交互式配置脚本。它会隐藏 SecretKey 输入，避免密钥出现在 PowerShell 命令历史中：

```powershell
powershell -ExecutionPolicy Bypass -File tools\set_tencent_asr_env.ps1
```

脚本把四个变量写入当前 Windows 用户环境。完成后关闭并重新打开终端。

## 3. 本机配置检查

```powershell
python tools/tencent_asr_smoke.py check
```

该命令只检查环境变量格式和 SDK 是否已安装，不访问腾讯云，也不会显示密钥。

## 4. 先跑短音频

```powershell
python tools/tencent_asr_smoke.py run "D:\录音\短样本.m4a"
```

默认设置：

- 引擎：`16k_zh_en_2.0`
- 单声道、保留标点、智能数字转换
- 开启说话人分离
- 每 8 秒查询一次任务状态
- 最长等待 180 分钟
- 成功或明确失败后删除 COS 临时音频
- 中断或超时后保留 COS 音频，允许继续查询

## 5. 中断后继续

每次运行都会输出一个任务目录，例如：

```text
data\asr_smoke\20260919_120000_ab12cd34
```

继续查询：

```powershell
python tools/tencent_asr_smoke.py resume "data\asr_smoke\20260919_120000_ab12cd34"
```

腾讯云 TaskId 和识别结果只保留 24 小时，应尽快继续。

## 6. 结果文件

成功后任务目录包含：

- `state.json`：本次联调状态，不含密钥和预签名 URL
- `create_task.response.json`：创建任务响应
- `result.raw.json`：腾讯云完整原始响应
- `provider_result.txt`：腾讯云 `Result` 原文，可能包含厂商时间标记
- `transcript.txt`：从分段结果合并出的纯文字，可直接送入现有调研输入
- `transcript.with_timestamps.md`：带时间戳和说话人编号的可读结果
- `segments.json`：后续接入工作台使用的结构化分段

## 7. 可选参数

首版安全管家热词表位于：

- `config/products/safety_butler/asr_terms.json`：热词、权重、纠错和待确认项
- `config/products/safety_butler/tencent_hotwords_v1.txt`：腾讯云 `词|权重` 导入格式

校验或同步热词表：

```powershell
python tools/tencent_asr_hotwords.py check
python tools/tencent_asr_hotwords.py list
python tools/tencent_asr_hotwords.py sync
```

`sync` 会按 `vocab_name` 查找同名表：不存在则创建，存在则更新，避免重复创建。
当前安全管家 V1 的 `VocabId` 已记录在 `asr_terms.json`；词表保持非默认状态，
仅在安全管家转写请求中显式传入，避免影响其他业务。

指定热词表：

```powershell
python tools/tencent_asr_smoke.py run "D:\录音\样本.m4a" --hotword-id "腾讯云热词表ID"
```

联调时暂不删除 COS 文件：

```powershell
python tools/tencent_asr_smoke.py run "D:\录音\样本.m4a" --keep-cos
```

关闭说话人分离：

```powershell
python tools/tencent_asr_smoke.py run "D:\录音\样本.m4a" --no-speaker-diarization
```
