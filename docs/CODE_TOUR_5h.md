# 灵石工作台 · 5 小时代码精读手册

> 目标：5 小时内建立「业务如何被定义 → 配置如何装载 → 文字如何变结构化 → 结构化如何变交付物」的完整心智模型。
> 所有命令都已在 `workbench/` 目录实测通过（2026-10-01）。
> 断网可用：本手册不依赖联网，规则通道（`provider='rule'`）完全离线。

---

## 0. 读前准备（10 分钟，别跳）

### 0.1 环境与解释器

```bash
cd "D:/claude-workspace/LinkX-SR-dashboard/灵石解决方案工作台/workbench"
```

项目自带虚拟环境：`.venv/Scripts/python.exe`（Windows）。**所有探针都用它**，依赖（flask / jinja2 / openpyxl）都在里面。

每条 Python 探针都遵循这个模板（三件套缺一不可）：

```bash
PYTHONIOENCODING=utf-8 ./.venv/Scripts/python.exe -c "
import sys; sys.path.insert(0,'.')
# 你的代码
"
```

- `PYTHONIOENCODING=utf-8`：Windows 控制台默认 GBK，不加会 UnicodeEncodeError。
- `sys.path.insert(0,'.')`：`core` / `services` 是本地包，必须把 workbench 目录塞进 path。

**更推荐**：把探针写成 `.py` 文件放 `workbench/tmp_probe.py`，用编辑器（VSCode/PyCharm）打开、逐段改、逐段 Run。交互式 `-c` 适合一次性验证，长探针用文件。

### 0.2 三条体检命令（先跑一遍，确认基线是绿的）

```bash
PYTHONIOENCODING=utf-8 ./.venv/Scripts/python.exe check_config.py           # 全部产品
PYTHONIOENCODING=utf-8 ./.venv/Scripts/python.exe check_config.py --strict  # warn 也算失败，发布前标准
PYTHONIOENCODING=utf-8 ./.venv/Scripts/python.exe -m unittest discover -s tests -v  # 单元测试
```

启动服务：根目录「启动 Demo.bat」→ http://127.0.0.1:8770 （或 `python app.py`）。

### 0.3 沙箱规则（重要，别把演示数据搞脏）

| 可以放心跑 | 别乱跑 |
|---|---|
| 任何 `import` + 纯函数调用 | `repo.upsert_*` / `db.execute` 写操作 |
| `extract.run_extraction(..., provider='rule')` | 不带 `provider='rule'` 的提取（会真调 DeepSeek，花钱） |
| `rules.*` 全部（纯函数） | `services/audio.py` 的上传/提交（真调腾讯云，计费） |
| `db.query(...)` 只读 SELECT | 删 `data/workbench.db` |
| `generate.build_context` / `md_to_html_fragment` | `departments.set_password`（会改 `data/departments.json`） |

> 改坏了怎么办：`data/` 下的一切都可重建；`config/` 是只读的，改了就用 `git checkout` 还原。

---

## 1. 五段时间表

| 时段 | 主题 | 读什么 | 产出 |
|---|---|---|---|
| **第 1 小时** | 业务是怎么被定义的 | `ARCHITECTURE.md` §1-§5 + `config/field_model_schema.json` + `field_packs/common_project.json` + `products/safety_butler/fields.json` | 能说清「加一个新字段要改哪个文件」 |
| **第 2 小时** | 配置怎么变成对象 | `core/config.py`（110 行）+ `core/fieldmodel.py::_merge`（78-159）+ `check_config.py` | 能在解释器里手动合并一次并解释每条校验 |
| **第 3 小时** | 文字稿 → 字段 | `core/extract.py`：`split_sentences`→`cn2num`→`negated`→各 `extract_*`→`_run_rule_engine`(519)→`run_extraction`(740) | 能手工造一句话让某个字段命中/不命中 |
| **第 4 小时** | 字段 → 派生结果 → 生成物 | `core/rules.py`（materialize 210 / compute_rows 296 / compute_all 411）+ `core/generate.py::build_context`(63) + `core/renderers.py` | 能手算一遍传感器 BOM 并对上代码输出 |
| **第 5 小时** | 串起来：服务与路由 | `services/survey.py` + `services/artifacts.py` + `app.py`（review 597 / save_field 641 / generate 677）+ `templates/review.html` + `static/app.js` | 能在浏览器里走完一遍，并说出每个按钮背后调了谁 |

---

## 2. 重点函数地图

### 2.1 配置装载层（`core/config.py` 110 行 / `core/fieldmodel.py` 331 行）

| 函数 | 行 | 为什么是重点 |
|---|---|---|
| `config.load_json` | 8 | 进程内字典缓存。改完 JSON 不重启不生效——调试第一坑 |
| `config.reload_all` | 18 | 清缓存，长跑进程里改配置后要调它 |
| `config.list_products` | 43 | `_` 开头目录不算产品（`_template` 脚手架） |
| `config.field_config` | 64 | 唯一的字段模型入口，下游全走它 |
| `config.fields_by_group` | 89 | 注意注释：组装后**不能**再按包内 order 重排 |
| **`fieldmodel._deep_merge`** | 47 | dict 递归合并、其余类型整体覆盖——`overrides` 的语义基础 |
| **`fieldmodel._merge`** | 78 | **全项目最值得精读的函数**。81 行做了 5 件事：读包 → 分组排序 → 收集字段 → 应用 overrides/remove → 排序。`problems=None` 快失败、`problems=list` 一次报全，这个双模式是设计精髓 |
| `fieldmodel.load_field_model` | 162 | 无 `packs` 键 → 旧扁平格式原样返回（向后兼容） |
| `fieldmodel.validate_field_model` | 189 | 校验器本体，60 行规则全在这里，对着 schema 一条条看 |
| `fieldmodel.render_report` | 303 | 生成人读报告；`check_config.py` 直接消费它 |

### 2.2 提取层（`core/extract.py` 767 行）

| 函数 | 行 | 为什么是重点 |
|---|---|---|
| **`split_sentences`** | 45 | 正则 `SENT_SPLIT`(28)：逗号切分只在「后续 25 字内无句末标点」时生效，防止把整句拦腰截断 |
| **`cn2num`** | 63 | 中文数字 → float。支持 一/十/二十/三万/两。**注意：`cn2num('三栋')` 返回 None**（遇到非数字字符直接放弃），这是刻意的保守设计 |
| **`negated`** | 92 | 否定语境判定：前后各取 6 字窗口，`NEG_PRE`/`NEG_POST` 词表。所有出现位置**全部**被否定才算否定 |
| `keyword_hits` | 111 | `guard=False` 可关闭否定保护（少数场景需要） |
| `extract_entity` | 147 | 冒号引导权重最高、长度适中加分——打分式抽取的范本 |
| `extract_sentence` | 182 | 默认模式，`preserve_lines` 开关决定是否按行处理 |
| `extract_number` | 238 | 总量线索优先（「一共四栋」压过「两栋是车间」） |
| `extract_enum` | 370 | 选择题：`_polarity` 判正负、同义词匹配、`prefer_longest_match` |
| `extract_list` | 421 | 多选题，配合 `require_other_detail` |
| `extract_table` | 451 | 表格字段（能源管家） |
| **`extract_rules_table`** | 499 | 规则驱动表格（设备管家），抽完统一走 `rules.postprocess` |
| **`_run_rule_engine`** | 519 | 规则通道主循环：按 `extract.mode` 分派到 10 个抽取器；末尾 `allow_custom + fallback_text` 的兜底逻辑值得看 |
| `_run_llm_engine` | 624 | LLM 通道；`_normalize_llm_value`(595) 把模型输出收紧到字段契约 |
| `_normalize_llm_value` | 595 | 「不合规值按缺失处理，禁止宽松落库」——费用铁律在代码里的体现 |
| `relevance_check` | 726 | 关联度过低直接拒绝解析（防粘贴错产品） |
| **`run_extraction`** | 740 | 入口。长度校验 → 关联度校验 → 分派通道 → 拼 stats |

### 2.3 规则计算层（`core/rules.py` 764 行）—— 只有设备管家用到

| 函数 | 行 | 为什么是重点 |
|---|---|---|
| `load` / `has_rules` / `rules_version` | 47/52/56 | 没有 `rules.json` 的产品返回 None，全链路要能容忍 |
| `field_output` | 72 | 反查「这个字段是不是规则驱动的」——页面/服务据此决定要不要显示联动 UI，**不靠产品名判断** |
| `category_of` / `item_of` | 99/110 | 空间类型 / 物业设备的同义词解析。解析不到返回 None，不猜 |
| `parse_qty` | 128 | 数量解析，返回 (数量, 错误说明) |
| **`materialize`** | 210 | **重点**。固定项补全，幂等。docstring 解释了为什么「不要某项要改数量 0 而不是删行」 |
| **`postprocess`** | 263 | rule/llm 两个通道的统一收口，防口径漂移；算不出就整体降为 low |
| **`_sensor_qty`** | 282 | 三种模式；**未知模式直接抛错而不回落 1:1**——注释解释了为什么 |
| **`compute_rows`** | 296 | 逐行计算，返回与输入等长、下标对应的结果；行级问题就地返回 |
| `build_dataset` | 368 | 组装 `columns` + `rows` + `issues` |
| **`compute_all`** | 411 | 按当下录入重算全部输出，**刻意不落库**（预览与生成永远看到同一份最新结果） |
| `table_ui` | 436 | 给页面用的下拉候选与计量标签 |
| `validate_rules` | 471 | 规则库自检，`check_config.py` 一并体检 |
| `extract_rows` | 716 | 规则通道的表格抽取（含 `_room_mentions` 570、`_group_rooms` 678） |

### 2.4 生成层（`core/generate.py` 300 行 / `core/renderers.py` 180 行）

| 函数 | 行 | 为什么是重点 |
|---|---|---|
| **`generate.build_context`** | 63 | **重点中的重点**。一次装配好 `project/product/pp/groups/data/display/detail/datasets/meta`，模板只负责表达。`datasets` 在这里实时重算 |
| `generate.md_to_html_fragment` | 124 | 自写极简 Markdown 解析器，81 行。不引三方库 |
| `generate.render_artifact` | 250 | 有 `renderer` 走注册表，否则走 Jinja；`StrictUndefined` 意味着模板里写错变量名会直接报错 |
| `generate.generate_selected` | 285 | 逐项生成，单项失败不影响其他（try 在循环内） |
| `renderers.render_xlsx_table` | 7 | 直接导表格字段原始录入行 |
| **`renderers.render_xlsx_dataset`** | 89 | 导规则引擎算出的派生结果；`_resolve_path`(73) 用声明式路径取页眉，渲染器不写死业务字段名 |
| `RENDERERS` | 179 | 注册表。新格式在这里加，不在 generate 里按 key 分支 |

### 2.5 校验与服务层

| 函数 | 行 | 为什么是重点 |
|---|---|---|
| `validate.validate_fields` | 44 | 通用字段校验 |
| `validate._business_rules` | 137 | 产品专用跨字段规则（注释说不应长期写死在这） |
| `validate.eligibility_issues` | 156 | 准入规则消费 `product.json.eligibility_rules`（只有能源管家有） |
| `choices.choice_state` / `normalize_manual_choice` | 16/27 | 选择题状态机 + 「选其他必须填具体品牌」的强制 |
| **`services/survey.py::extract_latest_source`** | 48 | **重点**。先建运行记录再调 provider（失败也留审计）；模型跑期间用户可能在填表，写回前**再查一次** protected |
| `services/survey.py::save_field_value` | 88 | 表格字段保存时会触发 `rules.materialize`——新增一间设备房自动补固定项 |
| `services/artifacts.py::build_preview` | 15 | 预览准备 |
| `repo.upsert_field_value` | 342 | 所有字段写入的唯一出口，`protected` 位在这里 |
| `repo.field_stats` | 488 | 进度统计（必填完成数/待确认数） |
| `app.py::review` | 597 | 页面如何把 `rules_ui` 注入字段定义 |
| `app.py::save_field` | 641 | 前端 AJAX 保存入口，返回新的状态与校验结果 |

---

## 3. 逐小时精读指南

### 第 1 小时 · 业务是怎么被定义的

**阅读顺序**：`ARCHITECTURE.md` §1（8 条原则）→ §3.3（产品配置）→ `config/field_model_schema.json` 的 `layers` 字段 → `field_packs/common_project.json` → `products/safety_butler/fields.json`。

**三层配置的职责（`field_model_schema.json:4-19` 写得很清楚）**：

| 层 | 文件 | 改它的时机 |
|---|---|---|
| 元契约 | `field_model_schema.json` | 只有代码新增了字段类型/抽取方式时才动 |
| 字段本体 | `field_packs/*.json` | 改字段、加字段、改枚举与同义词 |
| 组装清单 | `products/<t>/fields.json` | 新增产品、调分组顺序、对某产品单独改写个别字段 |

**关键机制**：`group_order` 决定分组顺序，**与 `packs` 的引用顺序无关**（吃过亏，见 `config.py:92` 注释）。

**看着 `safety_butler/fields.json` 回答**：
1. `packs` 引用了 8 个包，为什么 `common_decision` 排在后面但分组仍按 `group_order` 显示？
2. `switch_brand` 在包里是什么类型？在产品里被 `overrides` 改成了什么？（答案：改成 `multiselect` + `allow_custom` + `require_other_detail`）
3. `remove_fields: ["service_type"]` 删掉的是哪个包里的字段？

### 第 2 小时 · 配置怎么变成对象

精读 `fieldmodel._merge`（78-159）。在编辑器里单步跟一遍。

**`_merge` 的 5 步**：
1. 读包（缺包不中断，记 problem）
2. 分组：包给定义，`group_order` 给顺序，缺失回落 `group.order`
3. 字段：按包顺序收集，检测跨包 key 重复
4. 应用 `remove_fields` / `overrides`（`_deep_merge`）
5. 排序：分组顺序优先，组内保持声明顺序

**双模式设计**：`problems=None` → 遇致命问题立刻抛（运行时快失败）；`problems=list` → 记录并尽力合并（校验时一次暴露全部问题）。这是「配置化不等于埋雷」的关键。

### 第 3 小时 · 文字稿 → 字段

先读小工具（45-120 行），再读 `run_extraction`(740)，最后钻 `_run_rule_engine`(519)。

**四类口语难题**（`extract.py` 头部 docstring）：
- 中文数字 `cn2num`
- 总量线索优先 `TOTAL_CUES`
- 否定语境 `negated`
- 自我更正（「一共四栋……哦不对，五栋」→ 取更正值并标待确认）

**状态机**（`_run_rule_engine:562-568`）：空值 → `required_missing`/`optional_missing`；有值 → `pending_confirm`（conf=low）或 `extracted`。

### 第 4 小时 · 字段 → 派生 → 生成物

**设备管家是唯一有 `rules.json` 的产品**，用它当样本。

数据流：`device_room_survey`（录入行：设备房名/空间类型/物业设备/数量）→ `materialize`（补固定项）→ `compute_rows`（算传感器数量）→ `build_dataset`（组装列与行）→ `ctx['datasets']` → `xlsx_dataset` renderer → Excel。

**三条硬约束**：纯函数不落库 / 不出现产品名分支 / 不臆造数值（算不出就留空报错）。

然后读 `generate.build_context`(63)，理解 `ctx` 的 9 个键。

### 第 5 小时 · 服务与路由

- `services/survey.py`（128 行，全部读完）
- `app.py` 只看 4 个路由：`review`(597)、`save_field`(641)、`preview`(664)、`do_generate`(677)
- `templates/review.html`：注意 `data-key` / `data-type` / `data-rules` 三个 data 属性，前端靠它们做无框架联动
- `static/app.js`：`scheduleSave(el, 700)` 是 700ms debounce 自动保存

---

## 4. 可以在解释器里一点点试的地方（25 条探针，全部实测通过）

> 建议：新建 `workbench/tmp_probe.py`，把下面代码粘进去，用编辑器逐段 Run / 打断点 / 改参数。

### A 组 · 配置装载（约 40 分钟）

**A1 产品与字段包全景**
```python
import sys; sys.path.insert(0,'.')
from core import config, fieldmodel
for p in config.list_products():
    print(p['product_type'], p['name'], p['version'], p['status'], p['capabilities'])
print('字段包:', fieldmodel.list_packs())
```
预期：`safety_butler 安全管家 0.3.0 active` 等 4 个；11 个字段包。

**A2 看一个字段被 overrides 改成什么样**
```python
from core.config import field_map
m = field_map('safety_butler')
print(len(m))                                  # 48（安全管家字段数）
f = m['switch_brand']
print(f['type'], f.get('required'), f.get('allow_custom'), f.get('require_other_detail'))
print(f['extract'])                            # {'mode':'list', 'context_keywords':['交换机']}
```
预期：`multiselect False True True`。**再打开 `field_packs/security_network.json` 找同一个 key 对比**——这就是 overrides 的效果。

**A3 分组顺序**
```python
from core.config import fields_by_group
for g, fs in fields_by_group('safety_butler'):
    print(g['key'], g['name'], len(fs))
```
预期 11 组，顺序与 `group_order` 一致（basic → … → special）。

**A4 手动合并一次（理解 _merge）**
```python
import json
from core import fieldmodel, paths, config
raw = config.load_json(paths.product_dir('safety_butler') + '/fields.json')
probs = []
merged = fieldmodel._merge(raw, 'safety_butler', problems=probs)
print('problems:', probs)
print('packs:', merged['packs'])
print('字段数:', len(merged['fields']), '分组数:', len(merged['groups']))
```
把 `problems=probs` 改成 `problems=None` 再跑一次——体会「快失败 vs 一次报全」。

**A5 故意改坏配置，看校验器报什么**
```python
from core import fieldmodel
for i in fieldmodel.validate_field_model('safety_butler'):
    print(i['level'], i['where'], i['message'])
```
然后临时改 `products/safety_butler/fields.json`：把某个 `required` 写成字符串 `"true"`，或在 `overrides` 里加一个不存在的 key，重跑看报错。**改完记得还原（`git checkout`）**。

**A6 缓存陷阱**
```python
from core import config
a = config.field_config('safety_butler')
# 现在去改 config/field_packs/security_network.json 里某个 label
b = config.field_config('safety_butler')
print(a is b)          # True —— 同一个缓存对象，改动不可见
config.reload_all()
c = config.field_config('safety_butler')
print(a is c)          # False —— 清缓存后才重新读
```

### B 组 · 提取引擎（约 60 分钟）

> ⚠️ **必须显式传 `provider='rule'`**。`app.json` 里默认 `provider: "llm"`，不带参数会真调 DeepSeek。
> （`LS_FORCE_EXTRACTION_PROVIDER=rule` 环境变量**只在 services 层生效**，`run_extraction` 本身不读它——这是个容易踩的坑。）

**B1 小工具逐个试**
```python
from core import extract
print(extract.cn2num('三'), extract.cn2num('二十'), extract.cn2num('二十万'), extract.cn2num('两'))
print('带无关字符:', extract.cn2num('三栋'))     # None，刻意保守
print(extract.negated('驻场暂时不需要', '需要')) # True
print(extract.negated('需要驻场', '需要'))        # False
print(extract.split_sentences('一共四栋。其中两栋是车间，另外两栋是办公楼。'))
```

**B2 跑一次完整规则提取**
```python
from core import extract
text = open('samples/transcript_full.md', encoding='utf-8').read()
res, stats = extract.run_extraction(text, 'safety_butler', provider='rule')
print(stats)   # {'provider':'rule','hit_fields':28,'relevance_hits':26,...}
for k in ['project_name','has_internet','camera_brand']:
    print(k, '=>', repr(res[k]['value'])[:60], '|', res[k]['status'], '|', res[k]['quote'][:40])
```
预期：`project_name => '新宇精密产业园'`；`hit_fields` 28/48。

**B3 自己造句子，单字段验证**
```python
from core.config import field_map
from core import extract
f = field_map('safety_butler')['has_internet']
for s in ['现场有专线网络。', '现场没有网络。', '网络暂时不通。']:
    r, _ = extract.extract_enum(extract.split_sentences(s), f['extract'], f, s)
    print(s, '->', r[0])
```
**改句子玩**：加「暂时」「不用」「先不」，看否定判定怎么变。

**B4 关联度拒答**
```python
from core import extract
print(extract.relevance_check('今天天气不错，我们吃了午饭。', 'safety_butler'))
# (False, 0) —— 会被 run_extraction 拒绝
```

**B5 边界：过短 / 过长**
```python
from core import extract
try:
    extract.run_extraction('太短了', 'safety_butler', provider='rule')
except extract.ExtractionError as e:
    print('Expected error:', e)
```

**B6 对比两个通道（要联网，可选）**
```python
res_rule, s1 = extract.run_extraction(text, 'safety_butler', provider='rule')
res_llm,  s2 = extract.run_extraction(text, 'safety_butler', provider='llm')
diff = [k for k in res_rule if res_rule[k]['value'] != res_llm[k]['value']]
print('差异字段:', diff)   # 观察口径漂移
```

### C 组 · 规则引擎（约 50 分钟）

**C1 规则库结构**
```python
from core import rules
out = rules.get_output('device_butler', 'sensor_bom')
print(out['source_field'], out['group_column'], out['category_column'], out['item_column'], out['qty_column'])
print('空间类型:', [c.get('item') or c.get('name') for c in rules.categories(out)][:6])
print('结果列:', [c['label'] for c in out['result_columns']])
```

**C2 完整换算链路**
```python
import json
from core import extract, rules
text = open('samples/device_butler/full.md', encoding='utf-8').read()
res, _ = extract.run_extraction(text, 'device_butler', provider='rule')
rows = res['device_room_survey']['value']
print('录入行数:', len(rows))          # 29
print('首行:', json.dumps(rows[0], ensure_ascii=False))

out = rules.get_output('device_butler', 'sensor_bom')
m = rules.materialize(out, rows)
print('物化后:', len(m))               # 29（本样例已补全，验证幂等）
m2 = rules.materialize(out, m)
print('再跑一次:', len(m2))            # 还是 29 —— 幂等性验证

ds = rules.build_dataset('device_butler', out, m)
print('列:', [c['label'] for c in ds['columns']])
print('派生行数:', len(ds['rows']))    # 27
print('示例:', json.dumps(ds['rows'][0], ensure_ascii=False))
print('问题:', ds['issues'][:3])
```

**C3 未知模式必须抛错**
```python
from core import rules, extract
try:
    rules._sensor_qty('some_new_mode', 3, 1)
except rules.RulesError as e:
    print('Expected:', e)
```
改完这句去 `rules.py:282` 看注释，理解为什么不回落 1:1。

**C4 不臆造数值**
```python
from core import rules
out = rules.get_output('device_butler', 'sensor_bom')
bad = [{'room_name':'某某机房','space_type':'不存在的空间类型','device':'未知设备','qty':None}]
print(rules.compute_rows(out, bad)[0]['error'])   # 有 error，不填默认值
```

**C5 `compute_all` 不落库**
```python
from core import rules
print(list(rules.compute_all('device_butler', {'device_room_survey': []}).keys()))
# {'sensor_bom': {...}} —— 空输入也给结构，不会崩
```

### D 组 · 生成层（约 40 分钟）

**D1 Markdown → HTML（纯函数，随便玩）**
```python
from core import generate
print(generate.md_to_html_fragment(
    '# 标题\n- a\n- b\n| x | y |\n|---|---|\n| 1 | 2 |\n**粗** `code`\n> 引用'))
```
改输入：加三级标题、有序列表、`---` 分隔线，看输出怎么变。

**D2 渲染真实上下文**（需要数据库，只读）
```python
import json
from core import db, generate
rows = db.query('select id, product_type from project_products limit 3')
print(rows)
pp = rows[0]
ctx = generate.build_context(pp['id'], pp['product_type'])
print('ctx keys:', list(ctx.keys()))
print('groups:', len(ctx['groups']), '| datasets:', list(ctx['datasets'].keys()))
print('meta:', json.dumps(ctx['meta'], ensure_ascii=False))
```
预期：`groups: 11`，`datasets: []`（安全管家没有规则库；换成 device_butler 的 pp 就有 `sensor_bom`）。

**D3 直接渲染一个生成物**
```python
from core import generate
files = generate.render_artifact(pp['id'], pp['product_type'], 'survey_result')
for f in files: print(f['format'], f['name'], f['size'])
```
去 `data/outputs/<pp_id>/survey_result/` 看产出文件。

**D4 xlsx renderer 直接调**
```python
from core import rules, generate, config
from core.renderers import render_xlsx_dataset
out = rules.get_output('device_butler','sensor_bom')
rows = rules.materialize(out, <C2里的 rows>)
ds = rules.build_dataset('device_butler', out, rows)
ctx = {'project': {'name':'测试项目'}, 'datasets': {'sensor_bom': ds}}
adef = config.artifact_map('device_butler')['sensor_bom']
print(adef)   # 看 filename / renderer / columns / title / meta 的声明
```
（写文件会落 `data/outputs/`，想试就补 `output_dir` 参数。）

### E 组 · 服务与路由（约 40 分钟）

**E1 Flask test client（不用起服务器）**
```python
import sys; sys.path.insert(0,'.')
import app as flask_app
c = flask_app.app.test_client()
r = c.get('/login')
print(r.status_code)                 # 200
print('分部' in r.get_data(as_text=True))
```
参照 `tests/test_input_modes.py` 看完整登录 → 建项目 → 保存字段的写法。

**E2 能力门禁**
```python
from core.capabilities import product_capabilities, require_capability, KNOWN_CAPABILITIES
print(product_capabilities('safety_butler'))
print(KNOWN_CAPABILITIES)
try:
    require_capability('safety_butler', 'image_evidence')   # 未声明
except Exception as e:
    print('Expected:', e)
```

**E3 选择题归一**
```python
from core.config import field_map
from core.choices import normalize_manual_choice, choice_state
f = field_map('safety_butler')['switch_brand']
print(normalize_manual_choice(f, ['华为','其他']))       # 缺 other_detail 会怎样？
try:
    normalize_manual_choice(f, ['其他'])
except ValueError as e:
    print('Expected:', e)     # 选“其他”必须填具体品牌
```

**E4 只读查库，看落库长什么样**
```python
from core import db
for r in db.query("select name from sqlite_master where type='table' order by name"):
    print(r['name'])
print(db.query("select field_key, status, protected from survey_field_values limit 5"))
```

### F 组 · 端到端（浏览器，约 30 分钟）

起服务 → 新建项目 → 加「安全管家」→ 粘 `samples/transcript_full.md` → 提取 → 在 review 页改几个字段 → 预览 → 生成 → 打包下载。

**边点边对照**：
- 点「提取」= `POST /p/<id>/extract` → `services/survey.py::extract_latest_source`
- 改一个输入框、等 0.7 秒 = `POST /p/<id>/field` + `app.js` debounce
- 再点「重新提取」→ 观察你改过的那个字段**没被覆盖**（protected 机制），去库里查 `protected=1`

---

## 5. 读完应该能回答的 12 个问题

1. 加一个新字段，要动哪几个文件？（提示：字段包 + 可能的产品 overrides，代码不用动）
2. 加一个新产品，最小改动是什么？（复制 `_template`，改 `product.json` + `fields.json`）
3. 为什么 `group_order` 与 `packs` 顺序无关？不声明会怎样？
4. `overrides` 是覆盖还是深合并？数组类型的属性（如 `options`）会怎么合？
5. 页面刷新为什么不会导致 ASR 重复计费？（`providers/asr/base.py` 的注释）
6. 为什么 `protected` 字段在提取时不被覆盖？代码在哪一层拦？
7. 规则引擎为什么「不落库」？如果落库会出什么问题？
8. 固定项为什么不能靠「删行」去掉？
9. `_sensor_qty` 遇到未知模式为什么抛错而不是回落？
10. Jinja2 用 `StrictUndefined` 的好处与代价？
11. `config/` 为什么是只读的？改密为什么写在 `data/`？
12. 页面上「待确认」这个状态是怎么产生的？（`confidence=='low'` → `pending_confirm`）

---

## 6. 我扫读时发现的落差与坑（读的时候留意）

| # | 现象 | 影响 |
|---|---|---|
| 1 | `providers/asr/` 与 `providers/storage/` **只有 `base.py` 抽象接口**，没有任何厂商实现类 | 腾讯云真实调用绕道 `providers/tencent_pipeline.py` → `tools/tencent_asr_smoke.py`（22KB 联调脚本）。ARCHITECTURE §1.4 声称的「业务层只依赖接口」被绕开 |
| 2 | `LS_FORCE_EXTRACTION_PROVIDER` 只在 `services/survey.py` 读，`extract.run_extraction` 不读 | 直接调 `run_extraction` 做实验时，不传 `provider='rule'` 会静默走 LLM |
| 3 | 4 个产品都标 `active`，但配置齐备度不均 | 只有 `device_butler` 有 `rules.json`，只有 `energy_butler` 有 `eligibility_rules`，`fire_butler` 模板照搬安全管家 |
| 4 | 项目级 ASR 词表与各产品词表的合并是人工动作 | ARCHITECTURE §8 第 4 项待办，存在漂移风险 |
| 5 | `config/*.json` 有进程内缓存 | 改配置不重启不生效，调试时最容易被坑 |
| 6 | `samples/transcript_analog.md` / `_medical.md` 等 | 是「关联性不足」的负样本，用来测 `relevance_check` 拒答 |

---

## 7. 参考入口速查

| 想看什么 | 打开哪里 |
|---|---|
| 架构权威说明 | `workbench/ARCHITECTURE.md` |
| 字段模型三层契约 | `config/field_model_schema.json` |
| 数据库全部表结构 | `core/db.py` SCHEMA（10-162 行，带大量「为什么」注释） |
| 迁移怎么演进 | `core/migrations.py` |
| 字段模型操作说明 | `docs/安全管家Demo_字段模型配置说明_v1.md` |
| 字段与收资表对照 | `docs/安全管家Demo_字段模型对照_v1.md` |
| 部署 | `deploy/ubuntu/` + `docs/部署上线评估报告_v1.md` |
| 全部单元测试 | `tests/`（16 个文件）；`test_device_butler.py` 是规则引擎最好的说明书 |
| 端到端自测 | `workbench/selftest.py` |
