# 新产品脚手架

以 `_` 开头的目录不会被扫描成产品（见 `core/config.py::list_products`），
所以放在这里不会污染产品列表。

## 新增一个产品，五步

```bash
# 1) 复制脚手架（在 workbench/config/products/ 下）
cp -r _template fire_butler

# 2) 改 product.json 的 product_type / name / version / capabilities
#    product_type 必须与目录名一致
#    capabilities 只声明已经完成配置和验收的能力，允许值见 core/capabilities.py

# 3) 改 fields.json 的 product_type、packs、group_order
#    packs 里引用现成的字段包即可；缺什么字段就去 config/field_packs/
#    下新建一个包，或在 extra_fields 里内联

# 4) 建模板目录（缺模板会导致生成物失败，但页面其余功能可用）
mkdir -p fire_butler/templates

# 5) 体检 + 自测
cd ../..
python check_config.py fire_butler
python selftest.py
```

## 现成可复用的字段包

| 包 | 分组 | 字段 | 说明 |
|---|---|---|---|
| common_project | basic | 7 | 项目基本情况 —— 任何产品都要 |
| common_docs | docs | 3 | 项目资料（图纸/台账/人员表） |
| common_property | property | 7 | 物业信息（合同/主体/收费模式/管理范围） |
| common_decision | decision | 4 | 决策地图（上线时间/决策人/对接人/预算） |
| common_requirements | special | 3 | 特殊要求（合规保密/标杆/采购审批） |
| security_environment | monitor, devices, network, cabinet | 18 | 安全专有：监控中心/摄像头/存储/网络/机柜 |
| security_business | business, keypoints | 7 | 安全专有：当前管理模式/痛点/期望/重点位置算法 |

`packs` 只决定"引用哪些字段"，**分组顺序由 `group_order` 决定**。
所以一个产品引用包的顺序可以随意，顺序永远明确。

## 模板目录要放什么

`templates/` 下需要放 `artifacts.json` 中每个生成物对应的 `.md.j2`，
文件名与 `artifacts.json` 的 `template` 字段一致。可先照抄
`safety_butler/templates/` 再改。
