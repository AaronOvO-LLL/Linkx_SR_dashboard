# 能源管家首版实现说明

按《能源管家需求文档_v1.md》接入现有项目、产品、录音转写、信息核对、预览与结果中心流程。需求原文未修改。

- 新增产品 energy_butler，版本 0.1.0；引用三个公共字段包及 energy_survey。
- 产品配置声明两条准入规则；字段变更后实时更新校验横幅。失败时保留输入，阻断生成、重试、单文件下载、ZIP打包及历史ZIP下载。
- 设备表支持增删行、连续序号、数字列、工/变频选择与待确认默认值；逐行必填及数字校验。人工修改受保护。
- 十问选填；按机房逐行内容不截断。预览完整展示所有设备行。
- 独立 xlsx_table renderer 输出10列、连续序号、相邻供冷区域合并；楼栋仅在同一供冷区域内合并。文本按文字类型写出，避免备注被Excel解释为公式。
- LLM收到完整列契约；离线规则支持带列名的逐行记录，遇到含糊信息留空，普通口语设备提取使用现有LLM通道。
- 本机 .venv 已安装 openpyxl==3.1.5；两份依赖清单已登记。启动脚本优先使用项目 .venv，保留 LINGSHI_PYTHON 显式覆盖。

## 验证

在 workbench 目录使用 `.venv/Scripts/python.exe` 执行：

```
python check_config.py energy_butler
python check_config.py --strict
python -m unittest discover
python selftest.py
```

新增 tests/test_energy_butler.py 覆盖多机房提取、否定守卫、行内校验、人工保护、空选填项、Excel列/合并、ZIP及准入拦截与恢复。浏览器已验证新增行自动保存、完整预览、生成Excel成功。

脱敏样例：samples/energy_butler/full.md；期望字段与设备行：samples/energy_butler/expected.json。

## 待确认与验证边界

价值卡片仅预留 key，不开放；完整踏勘报告、节能计算、方案报价不在首版。参考图未在项目内找到，Excel暂按文档10列与层级合并规则实现；sheet暂名“设备清单”，未加说明行。

本次验证使用离线规则与本地测试数据，未调用付费LLM/ASR服务。能源热词已合入本地项目级素材；腾讯云既有 vocab_id 未改，云端词表发布仍沿用现有人工流程。文档§11中的业务待确认项保持待确认状态。

最终检查记录（2026-09-29）：51项单元测试通过，原有selftest 89/89通过，能源管家配置检查通过。最后一次全局strict检查受工作区中同时新增的device_butler配置影响：其extract.mode=rules_table尚未注册；能源、安全、消防三个产品均通过。该设备管家改动不属于本次能源管家实现，未修改。
