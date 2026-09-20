# 腾讯云控制台准备清单

本清单只为“独立联调脚本”准备云端条件，不涉及工作台页面开发。

## 一、开通语音识别

1. 登录腾讯云控制台，搜索并进入“语音识别”。
2. 按页面提示完成实名认证（如已完成则跳过）。
3. 阅读并同意服务条款，单击“立即开通”。
4. 确认“录音文件识别”已经可用。
5. 本联调默认使用大模型 2.0 引擎 `16k_zh_en_2.0`，若控制台要求开通后付费，请确认开通。该引擎不使用普通录音文件识别的每月免费额度。

暂时不需要在控制台上传录音，也不需要配置回调 URL。

## 二、创建私有 COS 存储桶

1. 进入“对象存储 COS”控制台。
2. 左侧选择“存储桶列表”，单击“创建存储桶”。
3. 建议填写：
   - 类型：通用存储桶。
   - 名称：例如 `lingshi-asr-mvp`。
   - 地域：上海 `ap-shanghai`；也可以选择更靠近实际使用地点的广州 `ap-guangzhou`。
   - 数据冗余：MVP 使用单 AZ 即可。
   - 存储类型：标准存储。
   - 访问权限：**私有读写**。
4. 创建后复制完整桶名。控制台会自动附加 APPID，例如 `lingshi-asr-mvp-1250000000`。
5. 不要开启公有读、静态网站或 CDN；本脚本通过 6 小时预签名 URL 授权腾讯 ASR 临时下载。

建议增加一道兜底清理规则：在存储桶“生命周期”中，对前缀 `asr-mvp/` 设置 1 天后删除，并清理 1 天前未完成的分块上传。脚本正常完成后会立即删除临时音频，这条规则只处理异常中断留下的对象。

## 三、创建联调专用子用户和密钥

不要使用主账号密钥。

1. 进入“访问管理 CAM” → “用户” → “用户列表” → “新建用户”。
2. 创建一个仅供程序调用的子用户，例如 `lingshi-asr-mvp`；不需要控制台登录能力。
3. 为首次 MVP 联调授予：
   - COS 预设策略 `QcloudCOSDataFullControl`。
   - 下方 ASR 自定义策略。
4. 进入该子用户详情 → “API 密钥” → “新建密钥”。
5. 立即保存 SecretId 和 SecretKey。SecretKey 只在创建时显示一次。

ASR 自定义策略：进入 CAM“策略” → “新建自定义策略” → “按策略语法创建”，使用空白模板并粘贴：

```json
{
  "version": "2.0",
  "statement": [
    {
      "effect": "allow",
      "action": [
        "asr:CreateRecTask",
        "asr:DescribeTaskStatus"
      ],
      "resource": "*"
    }
  ]
}
```

保存后把策略关联到 `lingshi-asr-mvp` 子用户。

如果需要优先排除自定义策略配置问题，也可以在首次联调时直接关联腾讯云预设策略 `QcloudASRFullAccess`。链路跑通后，再替换回上面的最小权限策略。

`QcloudCOSDataFullControl` 对账号下所有 COS 数据范围较宽，但用于第一次联调最不容易被权限问题阻断。跑通后应改成仅授权指定存储桶的 `asr-mvp/*` 前缀，所需操作包括上传、分块上传、读取、查询和删除对象。

## 四、在本机保存配置

回到 `workbench` 目录，运行：

```powershell
powershell -ExecutionPolicy Bypass -File tools\set_tencent_asr_env.ps1
```

该脚本会隐藏 SecretKey 输入，并写入当前 Windows 用户环境变量。完成后关闭并重新打开 PowerShell。

依次验证：

```powershell
python tools/tencent_asr_smoke.py check
python tools/tencent_asr_smoke.py run "D:\录音\5分钟脱敏样本.m4a"
```

短样本成功后，再运行真实 2 小时录音。密钥不要通过聊天、邮件或截图传递。
