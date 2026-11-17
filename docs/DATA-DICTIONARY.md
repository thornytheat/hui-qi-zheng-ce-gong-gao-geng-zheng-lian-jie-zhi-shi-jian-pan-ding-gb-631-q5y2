# 数据字典 noticechain

字段契约的唯一人类可读版本；机器可读版本由 `GET /api/contract` 返回（同源：`app/modules/m2_parse/fields.py`、`app/modules/m2_parse/contract.py`、`app/reasons.py`、`app/config.py`）。

## 1 公告（announcement）

| 规范字段 | 中文名 | 类型 | 必需 | 三门户的列名（别名） | 含义与归一 |
|---|---|---|---|---|---|
| `doc_no` | 公文号 | 字符串 | 是 | 公文号 / 文号 / 文件编号 / 编号 / 发文字号 | 全角括号归一为 `〔〕`，去空格；**保持字符串**，前导零不丢（`〔2026〕07号` 仍是 `07`） |
| `seq` | 公告序号 | 整数 | 是 | 公告序号 / 版本序号 / 版本号 | 从 1 起；链内顺序，重复序号即冲突；同页出现「序号」与「公告序号」时取后者 |
| `kind` | 公告类型 | 枚举 | 是 | 公告类型 / 类型 / 公告性质 / 文种 | `首次发布`(ORIGINAL) / `更正`(CORRECT) / `延期`(EXTEND) / `撤销`(CANCEL)，别名见 `coerce.norm_kind` |
| `title` | 标题 | 文本 | 是 | 标题 / 公告标题 / 名称 / 项目名称 | 原文保留，只做空白折叠 |
| `published_at` | 发布日期 | 时刻（UTC） | 是 | 发布日期 / 发布时间 / 发文时间 / 日期 | 见 §2 时间规则 |
| `deadline` | 截止说明 | 规则 | 否 | 截止说明 / 截止日期 / 截止时间 / 申报截止 | `N 个工作日` / `N 个自然日` / 绝对日期；空值与「详见正文」按未登记；无法识别给 `Q_BAD_DEADLINE_RULE` |
| `amount` | 资助额度 | Decimal(万元,0.01) | 否 | 资助额度 / 支持金额 / 额度 / 资助金额 | 全角→半角、去千分位、单位折算；哨兵 → `None` |
| `target_field` | 更正事项 | 枚举 | 否 | 更正事项 / 更正内容 / 调整事项 | 仅更正公告：`截止说明` / `资助额度` / `标题`，其它值 → `C_ORPHAN_CORRECTION` |
| `target_value` | 更正后 | 文本 | 否 | 更正后 / 更正后内容 / 调整后 / 更正为 | 按 `target_field` 解释类型后再归一 |

## 2 数值与时间规则

- **金额**：一律 `Decimal`，单位「万元」，最小刻度 `0.01`；SQLite 中以 TEXT 存 Decimal 字符串，不落 float。
- **单位换算**：`万元`=1，`元`=0.0001，`亿元`=10000（`app/config.py::UNIT_TO_WAN`）；输出时单位跟着数字。
- **空值三态**：`None`（哨兵或未登记）/ `Decimal("0.00")`（真值 0）/ 具体数值，三者语义分开；哨兵清单只在 `app/config.py::SENTINELS` 定义。
- **时间**：入库 UTC（ISO 8601 带偏移），展示 `Asia/Shanghai(+08:00)`；`Z`、`+08:00`、无偏移的本地时刻、纯日期（按当日 00:00 +08:00）、中文日期都接受。
- **区间**：工作日计数区间 `[发布日次日, 截止日次日)`；自然日 `截止日 = 发布日 + N 天`，区间 `[发布日+1, 截止日+1)`。左闭右开，相邻区间不重不漏。

## 2A 门户契约版本化（额度列改版兼容）

门户改版把「额度」从带单位单列文本拆成「额度数值」「额度单位」两列，系统按**门户管理契约版本**并在采集时**冻结快照绑定批次**，两代契约并存：

| 契约版本 | 额度模式 | 额度列 | 解析路径 | 违约处理 |
|---|---|---|---|---|
| `contract/1.4`（v1） | `text` | `资助额度` 单列，数值与单位写在同一格（`420万元`、`12,000,000元`） | `coerce.norm_amount`：全角/千分位归一 + 单位表折算万元，**原文保留** | 坏文本给 `Q_BAD_AMOUNT` |
| `contract/2.0`（v2） | `split` | `额度数值` + `额度单位` 两列 | `coerce.norm_amount_split`：单位显式校验后按单位表折算，不做旧文本换算 | 见下 |

- **按门户登记**：`app/contracts/registry.py::PORTAL_CONTRACT_REGISTRY` 记录每个门户当前生效版本；portal_a/b/c 为 v1，改版门户 portal_d 为 v2。`all` 来源只含 v1 三门户，portal_d 需显式 source 触发。
- **采集快照绑定**：采信时按参与门户生成快照（契约定义 + sha256 digest，见 `app/contracts/snapshot.py`），写入 `contract_snapshots` 表与运行清单；批次键在「规则版本 + 来源 + 页面摘要」之外再纳入**快照摘要**，同页面但契约不同会产生不同批次，历史批次不被新契约改写。
- **v2 隔离规则（新记录）**：
  - 单位列缺失/空/哨兵而数值列有值 → `Q_AMOUNT_UNIT_MISSING`，禁止用默认单位顶替；
  - 单位取值未登记（如「美元」）→ `Q_AMOUNT_UNIT_CONFLICT`；
  - 数值列内嵌单位与单位列不一致（如数值 `120万元` + 单位 `元`）→ `Q_AMOUNT_UNIT_CONFLICT`，不猜以哪个为准；
  - 两列同为哨兵 → 按未登记（三态 `None`），不是 0；数值无法 Decimal 化 → `Q_BAD_AMOUNT`。
- **旧批次按原契约恢复**：解析与导出都读批次绑定的快照。v1 公告的 `amount_mode=text`、`raw_amount_text` 保留原始文本、`amount_basis` 写明「v1 文本契约按采集时单位表折算」；**不会把旧额度重新按 v2 解释或换算成新金额**。门户后续切换契约后，历史批次的清单/链版本导出逐字节复现原金额与采用依据。
- **契约切换**：`POST /api/contract/switch` 先做表头预检（`validate_headers`：必需列 + 额度两列是否命中）；预检失败抛 `Q_CONTRACT_PREFLIGHT_FAILED` 并**保留旧版**，门户不会被切到无法解析的契约；未知版本给 `Q_CONTRACT_UNKNOWN_VERSION`。
- **采用依据落库**：`announcements` / `chain_versions` 每行带 `contract_version`、`amount_mode`、`raw_amount_text`、`amount_unit`、`amount_basis_json`；清单与链版本 CSV 新增「契约版本 / 额度模式 / 额度原文 / 额度单位 / 采用依据」列。

## 3 工作日规则

- 工作日 = 周一至周五 − `HOLIDAYS_2026` + `MAKEUP_WORKDAYS_2026`（调休上班日即便落在周末也算工作日）。
- 发布日不计入；`第 N 个工作日` 即截止日。
- 例：发布日 `2026-09-28` + 30 个工作日 = `2026-11-13`（期间扣除国庆 7 天，计入 `2026-10-10` 调休上班日）。
- 例：发布日 `2026-09-01` + 30 个工作日 = `2026-10-20`（跨中秋与国庆）。

## 4 链（chain）

- 链键：`doc_no`；链内顺序：`seq` 升序，同序号即冲突。
- 合并规则：`ORIGINAL` 建基线 → `CORRECT` 覆盖 `target_field` → `EXTEND` 换截止说明 → `CANCEL` 作废；撤销后的版本只登记（`C_AFTER_CANCEL`）。
- 链状态：`ACTIVE`（有效）/ `VOIDED`（已撤销）/ `CONFLICT`（冲突隔离）；`ACTIVE` 且截止日早于评估日时展示为 `EXPIRED`。
- 可申报清单 = `ACTIVE` 且（未登记截止 或 截止日 ≥ 评估日）。评估日由 `EVAL_DATE` 环境变量给定，默认 `2026-11-15`。

## 5 去重与冲突

- 转载去重：`doc_no + seq + kind + published_at + 截止说明 + 额度 + 标题 + 更正项` 全行签名相同 → 合并计 1 条并记 `Q_DUP_ROW`。
- 业务键冲突：同 `doc_no + seq` 但取值不同 → 整链 `CONFLICT`，记 `C_DUP_SEQ_CONFLICT`，两条证据行都保留。
- 保留哪条：**都不保留**（整链隔离）；理由写在页面「链级冲突」与 `docs/DEPTH.md`。

## 6 页面与原因码

- 页面级（采信阶段）：`E_CHARSET_MISMATCH`、`E_BOM_STRIPPED`、`E_DECODE_FAILED`、`E_HTTP_STATUS`、`E_PAGE_TRUNCATED`、`E_PAGING_NO_NEXT`、`E_PAGING_LOOP`、`E_PAGING_LIMIT`、`S_NO_ANNOUNCEMENT`。
- 行级（解析/质量/链）：`Q_*`、`C_*`、`B_*`、`X_*`，完整目录见 `app/reasons.py`，`GET /api/contract` 的 `reason_codes` 字段与页面「原因码目录」同源。
- 级别：`error`（已隔离，不能进清单）/ `warn`（需人工看一眼）/ `info`（采集提示）。

## 7 对账规则

| 账 | 等式 |
|---|---|
| 页账 | 抓取页数 = 公告页 + 非公告跳过页 + 采集失败页 |
| 行账 | 表格数据行 = 入链公告 + 转载合并 + 解析隔离 + 质量隔离 |
| 金额账 | 公告金额合计 − 更正覆盖调整 = 清单合计 + 未入清单合计 |
| 清单账 | 入链公告数 = 每条链版本数之和；链最终金额 = 清单 + 未入清单 |

差额非 0 时页面与导出显式标「不平」，不做四舍五入掩盖。

## 8 运行清单（run manifest）

`run_id`（`NC-时间戳-随机`）、`batch_id`、`batch_key`（规则版本 + 来源 + 页面 sha256 集合 + 门户契约快照摘要）、`input_digest`、`contract_snapshot_digest`、`contract_snapshots`（每门户的契约版本/额度模式/digest）、`rule_version`、`eval_date`、`params`、每个输入页面的 `url/status/bytes/sha256/encoding`、`stages`（进/出/隔离/耗时/状态）、`counters`。导出：`GET /api/export?kind=manifest`。

## 8A 原始行断点与续跑

- 采信阶段把每条原始表格行写入 `raw_rows` 表（`batch_id + row_ref` 唯一，含 `fields_json`/`cells_json`/`processed`），解析中断后**从已保存原始行续跑，不重抓已采内容**。
- 续跑复用矩阵（`app/modules/m5_batches/resume.py`）：采信失败整体重抓；解析/质量失败复用采信（页面 + 9 条原始行）；链判定之后复用已解析公告。续跑 ingest 阶段标记 `reused`，重试不新增公告，原始行数与一次成功运行一致。

## 9 复核与回放（M7）

- 候选优先级：同一 `doc_no + seq` 有多行时，**发文机关页**优先于**转载页**，转载页只作来源；两行同属一个发文机关页即多值，链判定已按冲突隔离，复核时由人工选版。
  - 发文机关由公文号前缀决定（`澄工信发`→工信局、`澄科技发`→科技局、`澄企服`→企服中心）；门户所属机关见 `app/modules/m7_review/review.py::PORTAL_AGENCY`。前缀或门户未登记记「来源未登记」，排在最后。
- 裁定字段：`reviewer`（≥2 字）、`basis`（≥6 字，写清看了哪一页哪一行）、`seq`（必须是链内已有序号）；缺一项返回 4xx，不落库。
- 迁移合法性：撤销（`CANCEL`）之后再出现首次发布/更正/延期属非法迁移，该版不能被选为有效版（`M_ILLEGAL_TRANSITION`）；M4 侧同样只登记 `C_AFTER_CANCEL`，链状态保持「已撤销」。
- 重算：以所选版本及更早版本重算；同一序号先用候选优先级收敛成一条，再走与链判定相同的合并规则。重算只影响本次裁定结果，不改历史版本行。
- 留痕：一次裁定写一条记录（`rulings` 表，按批次 + 公文号唯一），快照同时含人工结论、自动结论（`auto_status` / `auto_amount` / `auto_deadline` / `auto_listed`）、逐字段差异与原因码 `M_MANUAL_OVERRIDE`（改判）/ `M_RULING_RECORDED`（一致）。
- 超长链：版本数 >20 时回放给压缩视图（`M_LONG_CHAIN`），只把「没有改变状态」的连续版本合并成一步，结论字段不变。
- 裁定后清单 = 已裁定且人工结论为「在清单内」的链，与自动清单并存，两者差异逐条列出，不互相覆盖。
