# 字段模型配置说明 v1

> 面向：需要改字段、加字段、加产品的人（不改代码）。
> 结论先行：**字段模型从「一个 406 行的单文件」拆成了「8 个字段包 + 1 张 31 行组装清单」；
> 安全管家的 49 字段 / 34 必填 / 6 项影响报价一字未动，已逐字段比对确认等价，82 项端到端自测全绿。**
> 现在加一个新产品，是复制一个目录 + 改 2 个 JSON，不是复制 406 行。

---

## 一、为什么不是「一个文件放所有产品的字段」

先问一个问题：如果把三个产品的字段都塞进一个文件，`common_project` 那 7 个字段
（项目名称、业态、占地面积…）会出现几次？

**答案：三次。** 消防管家要项目名称，能源管家也要，安全管家已经在用。三份「项目名称」
定义放在同一个大文件里，改一处忘两处，就是三个产品对同一个字段有三种解释。

所以正确的切法是**按「复用边界」切，不按「产品」切**：

```
                       ┌──────────────────────────────────┐
   config/             │  field_model_schema.json         │  ← 元定义（契约）
   field_model_        │  「字段能有哪些属性、哪些类型合法」 │     改代码时才动
   schema.json         └──────────────────────────────────┘
                                        ▲ 约束
                                        │
                       ┌──────────────────────────────────┐
   config/             │  field_packs/*.json              │  ← 字段本体
   field_packs/        │  common_*  = 跨产品复用（5 个包）   │     改字段改这里
                       │  security_* = 产品专有（3 个包）    │
                       └──────────────────────────────────┘
                                        ▲ 被引用
                                        │
                       ┌──────────────────────────────────┐
   config/products/    │  <t>/fields.json  （31 行）       │  ← 组装清单
   <t>/fields.json     │  「引用哪些包、什么顺序、改哪几个例外」│     加产品改这里
                       └──────────────────────────────────┘
                                        │ 合并
                                        ▼
                       扁平结构 {groups, fields}  ← 全链路原有代码零改动
```

**关键设计：合并后的结构与旧版单文件完全同构。** 所以 `extract.py`（规则引擎）、
`generate.py`（生成物）、`pricing.py`（费用）、`validate.py`（校验）、5 个 `.j2` 模板、
`review.html`（信息核对表单）——**一行都没改，也不用改**。这是这次重构敢动核心配置的前提。

---

## 二、文件清单

| 文件 | 行数 | 内容 | 什么时候改 |
|---|---:|---|---|
| `config/field_model_schema.json` | 136 | 元定义：字段属性清单、7 种字段类型、9 种抽取方式、8 条规则 | 代码新增类型/抽取方式时 |
| `config/field_packs/common_project.json` | 283 | **basic** 项目基本情况（7 字段） | 改项目画像字段 |
| `config/field_packs/common_docs.json` | 107 | **docs** 项目资料（3） | 改资料类收资项 |
| `config/field_packs/common_property.json` | 197 | **property** 物业信息（7） | 改物业口径 |
| `config/field_packs/common_decision.json` | 108 | **decision** 决策地图（4） | 改决策人口径 |
| `config/field_packs/common_requirements.json` | 82 | **special** 特殊要求（3） | 改合规/标杆/采购口径 |
| `config/field_packs/security_monitor.json` | 302 | **monitor + devices** 监控中心与设备（9） | 改摄像头/存储口径 |
| `config/field_packs/security_network.json` | 268 | **network + cabinet** 网络与机柜（9） | 改网络/机柜口径 |
| `config/field_packs/security_business.json` | 188 | **business + keypoints** 安全业务与重点（7） | 改痛点/期望/算法口径 |
| `config/products/safety_butler/fields.json` | **31** | 组装清单（原来 406 行） | 加产品、调分组顺序、做产品级覆盖 |
| `config/products/_template/` | — | 新产品脚手架（`_` 开头，不会被扫描成产品） | 加产品时复制 |
| `core/fieldmodel.py` | 317 | 装载器 + 校验器 | 由 Agent 维护，一般不动 |
| `check_config.py` | 53 | 体检入口 | 改完配置就跑一次 |

字段本体只在一个地方：`field_packs/`。**组装清单里没有字段定义**，这是刻意的——
避免"字段在两处各写一遍"这种最容易漂移的形态。

---

## 三、三个最常见的动作

### 3.1 改一个枚举（例如摄像头品牌加个「天地伟业」）

打开 `config/field_packs/security_monitor.json`，找到 `camera_brand`：

```json
{
  "key": "camera_brand", "label": "摄像头品牌", "group": "devices",
  "type": "select", "required": true, "affects_pricing": true,
  "options": [
    {"value": "海康", "synonyms": ["海康威视"]},
    {"value": "大华", "synonyms": []},
    {"value": "宇视", "synonyms": []},
    {"value": "华为", "synonyms": []},
    {"value": "天地伟业", "synonyms": ["天地伟业", "天地"]},   ← 只加这一行
    {"value": "其他", "synonyms": ["杂牌", "非主流"]}
  ],
  "extract": {"mode": "enum"}
}
```

`synonyms` 是口语同义词，**规则引擎的打分和「这段文字稿跟本产品有没有关系」都靠它**——
加了新品牌，录音里说"天地伟业"才能被识别出来，同时这个字段的关联度命中数也会+1。

改完把 `template_version` 升一位（`0.3.0` → `0.3.1`），然后跑体检。

### 3.2 加一个字段

**第一步先想清楚它属于哪个包。** 问自己："消防管家也会要这个字段吗？"
- 会 → 加到对应的 `common_*` 包
- 不会 → 加到对应的 `security_*` 包
- 只有这一份资料里有、且只服务这一个产品 → 别建新包，写在组装清单的 `extra_fields` 里

然后在包的 `fields` 数组里追加：

```json
{
  "key": "camera_install_year", "label": "摄像头安装年份", "group": "devices",
  "type": "number", "unit": "年", "required": false,
  "help": "用于判断设备是否到了更换周期",
  "extract": {"mode": "number", "keywords": ["装了", "安装", "哪一年", "年限"]}
}
```

`key` 用 snake_case，**一旦有真实数据进来就不能再改名**——它是已入库数据的锚点。

### 3.3 让某个产品「例外」

不想改公共包、只想在安全管家里改掉一个字段？用 `overrides`：

```json
"overrides": {
  "camera_count": {
    "required": false,
    "remark": "安全管家口径：>300 路需增服务器或算力卡",
    "extract": {"keywords": ["摄像头", "摄像机", "探头", "路", "点位"]}
  }
}
```

`overrides` 是**字段级深合并**：dict 递归合并（所以可以只改 `extract` 里的一个子键），
其余类型整体覆盖。不想要包里某个字段，写进 `remove_fields`。

---

## 四、加一个新产品：五步

以「消防管家」为例。

```bash
cd workbench/config/products

# 1) 复制脚手架（_template 不会被当成产品）
cp -r _template fire_butler

# 2) 改 product.json：product_type 必须是 "fire_butler"（与目录名一致）
#    同时把 name / short_desc / coverage / version 填上

# 3) 改 fields.json：packs 里引用现成的包，缺什么字段就去 field_packs/ 新建
#    最省事的起点：先引用全部 5 个 common_* 包
```

```json
{
  "template_version": "0.1.0",
  "product_type": "fire_butler",
  "note": "消防管家字段模型。口径来源：待补。",
  "packs": [
    "common_project", "common_docs", "common_property",
    "common_decision", "common_requirements"
  ],
  "group_order": ["basic", "docs", "property", "decision", "special"],
  "overrides": {},
  "extra_fields": [],
  "remove_fields": []
}
```

```bash
# 4) 建模板目录（缺模板会导致「生成物」失败，但页面其余功能可用）
mkdir -p fire_butler/templates
#    然后照抄 safety_butler/templates/ 的 5 个 .j2 再改，
#    并同步写 fire_butler/artifacts.json（生成物清单）

# 5) 体检 + 自测
cd ../..
python check_config.py fire_butler
python selftest.py
```

启动 Demo 后，「项目 → 添加产品」的下拉里就会出现消防管家（`list_products`
扫描 `config/products/` 下带 `product.json` 的目录，跳过 `_` 开头的）。

---

## 五、两个最容易搞错的点

**① 分组顺序由 `group_order` 决定，与 `packs` 的引用顺序无关。**

安全管家的 `security_business` 包同时提供 `business` 和 `keypoints` 两组，
但原表里 `decision` 夹在它们中间（business=8 → decision=9 → keypoints=10）。
如果让包的引用顺序决定分组顺序，`decision` 和 `keypoints` 就会颠倒。
所以顺序由清单的 `group_order` 说了算：

```json
"packs": [..., "security_business", "common_decision", "common_requirements"],
"group_order": ["basic","docs","property","monitor","devices","network",
                "cabinet","business","decision","keypoints","special"]
```

好处是：引用包的顺序怎么排都不影响结果，顺序永远明确、永远在清单里一眼可见。

**② `_` 开头的产品目录会被跳过。** 这是脚手架机制，别用 `_` 给真产品起名。

---

## 六、字段属性速查（写字段时照着填）

| 属性 | 必填 | 取值 | 谁在用它 |
|---|:---:|---|---|
| `key` | ✅ | snake_case，全局唯一 | 全链路。**发布后不可改名** |
| `label` | ✅ | 中文显示名 | 信息核对页、踏勘结果、费用卡片 |
| `group` | ✅ | 分组 key | `core/config.py::fields_by_group` |
| `type` | ✅ | `text` / `textarea` / `number` / `date` / `select` / `multiselect` / `table` | `review.html` 决定渲染成什么控件 |
| `required` | — | 布尔，缺省 false | 抽不到时状态为「必须补填」而非「可选缺失」 |
| `affects_pricing` | — | 布尔，缺省 false | 进费用卡片的「计价依据」区。**这是费用可追溯性的唯一入口，别乱加** |
| `remark` | — | 业务规则原文 | 报价时逐条展示的「为什么是这个价」的依据 |
| `help` / `placeholder` | — | 文本 | 核对页的提示与占位符 |
| `unit` | — | `m²` / `路` / `台` … | 核对页 + 踏勘结果文档。number 类型建议必给 |
| `options` | select/multiselect 必填 | `[{value, synonyms}]` | 下拉选项 + 规则引擎打分 + 关联度检查 |
| `columns` | table 必填 | `[{key, label}]` | 表格列 |
| `validate` | — | `{min, max, pattern, message}` | `core/validate.py` 生成前拦截 |
| `ai_hint` | — | 文本 | 仅 LLM 通道生效，rule 通道忽略 |
| `extract` | — | 见下 | 仅 rule 通道生效 |

### `extract` 的 9 种抽取方式

| mode | 干什么 | 必须配 |
|---|---|---|
| `sentence`（缺省） | 摘含关键词的原句，多句用「；」连接 | `keywords` |
| `entity` | 抽机构/人名/地址 | `keywords` + 建议 `entity_suffix` |
| `enum` | 在 options 上打分（含同义词与否定语境） | 字段有 `options` |
| `list` | 同上，返回全部正分选项数组 | 字段有 `options` |
| `number` | 抽数量，优先取「一共/总共」的总量句 | `keywords` |
| `money` | 抽含金额表述的句子（不编精确数字） | `keywords` |
| `phone` | 正则抽手机/固话 | — |
| `date` | 正则抽日期，归一为 `YYYY-MM-DD` | — |
| `table` | 按内置设备词表扫多行结构 | —（词表在 `core/extract.py`，暂未配置化） |

**`entity_suffix` 是防编造的闸门**：配了它却抽不到合法实体时，规则引擎宁可留空也不猜。
`negation_guard` 保持 `true`——关掉会让「不需要驻场」被读成「需要驻场」。

---

## 七、怎么知道改对了

```bash
cd workbench
python check_config.py              # 校验全部产品
python check_config.py safety_butler  # 只校验一个
python check_config.py --strict       # 有警告也算失败（发布前用）
```

正常输出：

```
【safety_butler】版本 0.3.0 ｜ 字段包 common_project、common_docs、...
        分组 11 ｜ 字段 49 ｜ 必填 34 ｜ 影响报价 6 ｜ 错误 0 ｜ 警告 0
  ✅ 校验通过
```

**它拦得住什么？** 我造了一份故意写错的配置做验证，10 个问题一次全报出来：

```
❌ 字段包不存在：does_not_exist（应位于 config/field_packs/does_not_exist.json）
❌ overrides 引用了不存在的字段：no_such_field
❌ remove_fields 引用了不存在的字段：not_here
❌ key 必须是 snake_case（小写字母开头，仅含小写字母/数字/下划线）
❌ select 必须提供 options
❌ type 'richtext' 不在 schema 允许的取值内：text、textarea、number、date、select、multiselect、table
❌ extract.mode 'sorcery' 不在允许取值内：sentence、entity、enum、list、number、money、phone、date、table
❌ validate.pattern 不是合法正则：unterminated character set at position 1
❌ group 'nowhere' 未在任何字段包中定义
❌ multiselect 必须提供 options
```

一次报全，而不是"改一个跑一次"。**"方便调整修改"的前提是改错了能马上知道**——
否则配置越灵活，埋雷越深。

---

## 八、本次结构变更留痕

按项目约定，反转已确认决策必须显式留痕。

| 项目 | 原文/原状 | 变更决定 | 处理方式 |
|---|---|---|---|
| 字段模型存储形态 | `config/products/<t>/fields.json` **单文件**平铺 11 分组 49 字段（406 行，`template_version 0.2.0`） | 拆为 **schema + field_packs + 组装清单** 三层（`template_version 0.3.0`） | 原件留档于 `config/products/safety_butler/_archive/fields.v0.2.0.flat.json`；已逐字段比对确认合并结果与原件 **groups/fields 完全等价** |
| 字段本体位置 | 在 fields.json 内 | 移到 `config/field_packs/`，fields.json 只做组装 | 加载器向后兼容：无 `packs` 键的 fields.json 仍按旧扁平格式处理 |
| 「新增产品」的成本 | 复制 406 行单文件 | 复制 `_template/` + 改 2 个 JSON | `list_products()` 增加跳过 `_` 开头目录的逻辑 |

**口径本身零变更**：仍然以 A 表（『踏勘要求的项目具体内容.xlsx』）为唯一权威来源，
49 字段 / 11 分组 / 34 必填 / 6 项影响报价，不做增删。这次动的是**存放形态，不是内容**。

---

## 九、MVP 边界与预留

按你的决定，本次**以 A 表为主先出 MVP**，B 系统（此前在线填报系统）独有的两类字段暂不并入：

| 待补字段类 | 来源 | 数量 | 并入时怎么做 |
|---|---|---|---|
| 接入可行性 | B「安全管家·监控详情」 | 9 项（账号密码、监控机房、监控网络、算法接入、算法需求、点位图…） | 新建 `security_access.json` 包 → 在 `safety_butler/fields.json` 的 `packs` 里加一行 |
| 需求意向 | B「需求沟通」 | 9 项（节能减碳、安全管理、设备管理、企业服务、消防管理、资产管理系统…） | 新建 `cross_sell.json`（`scope: common`）→ 所有产品共用 |

**这两类字段将来并入时，只改 JSON，不改代码**——这正是这次重构要买的东西。

---

## 十、已知限制（诚实清单）

1. **`extract_table` 的设备词表仍硬编码在 `core/extract.py`**（`DEVICE_WORDS` / `BRAND_WORDS`），
   不满足"一切业务知识进配置"。产品化时应移到 schema 或包级配置。
2. **字段名以中文 `label` 为展示主体，没有多语言层。** 目前不需要，记在这里备忘。
3. **schema 是"约定式"校验，不是 JSON Schema 严格校验。** 它拦得住枚举越界、漏配选项、
   key 拼错这类问题，但拦不住语义错误（比如把 `camera_count` 的 keywords 写成
   `storage_count` 的）。语义正确性还得靠录音样本抽检（K3 节点）。
4. **`extract.unit_hint` 目前无消费方**，schema 里已标注「预留」——别以为写上去就会换算。
