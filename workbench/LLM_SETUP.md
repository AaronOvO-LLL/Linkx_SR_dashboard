# 大模型结构化梳理配置

当前工作台使用 DeepSeek 的 OpenAI 兼容 `POST /chat/completions` 接口：

- 接口地址：`https://api.deepseek.com`
- 默认模型：`deepseek-flash`
- 密钥环境变量：`DEEPSEEK_API_KEY`
- 可选覆盖：`LS_LLM_BASE_URL`、`LS_LLM_MODEL`

密钥不写入 JSON、数据库、日志或浏览器页面。Windows 下可运行：

```powershell
powershell -ExecutionPolicy Bypass -File tools/set_llm_env.ps1
```

配置完成后重新启动 Demo。调研输入页会仅显示“已配置/未配置”，不会回显密钥。

生产流程固定为：文字稿（或已人工确认的 ASR 转写稿）→ DeepSeek 结构化 JSON → 字段类型与完整性校验 → 人工信息核对。回归测试通过 `LS_FORCE_EXTRACTION_PROVIDER=rule` 使用离线规则引擎，不消耗模型额度。
