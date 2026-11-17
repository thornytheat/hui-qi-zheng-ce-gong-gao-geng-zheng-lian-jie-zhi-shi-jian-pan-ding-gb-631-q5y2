# 深度登记 noticechain（惠企政策公告更正链与截止时间判定）

- 规模：中型（7 个功能模块 + 契约版本化子包 / 7 个页面 / 127 条测试）
- 核心规则一：**公告链合并**——同一 `doc_no` 的公告按 `公告序号` 成链，更正覆盖指定字段、延期顺延截止、撤销作废；链内重复序号、时间倒挂、父公告缺失一律整链隔离，给出稳定原因码与证据行，不猜哪条为准。
- 核心规则二：**工作日截止推算**——`自发布之日起 N 个工作日` 按「周一至周五 − 法定节假日 + 调休上班日」逐日推进，发布日不计入，计数区间左闭右开 `[发布日次日, 截止日次日)`。
- 核心规则三：**门户契约版本化**——额度列改版（带单位文本 → 数值/单位双列）按门户管理契约版本，采集时冻结快照绑定批次；v2 无单位/单位冲突进隔离，旧批次按 v1 原契约恢复、不换算旧金额；切换预检失败保留旧版。
- 使用者与工作流：园区企服组 6 人每天盯 3 个门户，原先手工抄截止日期到表格，更正公告常被漏看；现在一次采集即可得到「当前有效截止 + 可申报清单 + 冲突隔离」，并逐条可复核。

## 功能模块

| 模块 | 使用者动作 | 入口 | 核心文件 | 测试 |
|---|---|---|---|---|
| M1 采信与抓取 | 触发一次采集，看到逐页诊断与分页断点 | POST /api/crawl、页面 / | `app/modules/m1_ingest/service.py` `app/modules/m1_ingest/reader.py` `app/modules/m1_ingest/decode.py` `app/modules/m1_ingest/paging.py` | `tests/test_ingest.py` |
| M2 契约与解析 | 查看列契约、试跑单条公告解析 | GET /api/contract、POST /api/parse/preview | `app/modules/m2_parse/contract.py` `app/modules/m2_parse/fields.py` `app/modules/m2_parse/coerce.py` | `tests/test_parse.py` |
| M3 质量与隔离 | 看原因码分布与隔离行，导出隔离 CSV | GET /api/quarantine、页面 /quarantine | `app/modules/m3_quality/rules.py` `app/modules/m3_quality/quarantine.py` `app/modules/m3_quality/reconcile.py` | `tests/test_quality.py` |
| M4 链判定与截止 | 看政策链版本时间线与当前截止 | GET /api/policies、页面 /policies 与 /policies/{doc_no} | `app/modules/m4_chain/chain.py` `app/modules/m4_chain/workdays.py` `app/modules/m4_chain/service.py` | `tests/test_chain.py` |
| M5 批次与续跑 | 查历史、断点续跑、同源幂等重跑 | GET /api/batches、页面 /batches | `app/modules/m5_batches/resume.py` `app/modules/m5_batches/state.py` `app/modules/m5_batches/service.py` | `tests/test_batches.py` |
| M6 复核与导出 | 确认四账，导出清单/隔离/清单文件 | GET /api/export、页面 /export | `app/modules/m6_export/csv_out.py` `app/modules/m6_export/manifest_out.py` `app/modules/m6_export/service.py` | `tests/test_export.py` |
| M7 复核与回放 | 看冲突链、按公告号回放、写人工裁定与依据 | GET /api/review、GET /api/review/replay、POST /api/review/ruling、页面 /review | `app/modules/m7_review/review.py` `app/modules/m7_review/replay.py` `app/modules/m7_review/service.py` | `tests/test_m7_review.py` |

端到端：`tests/test_e2e.py`（样例站 → 采信 → 链判定 → 清单 → 导出 → 复核回放，含 4xx 失败输入与幂等重跑）。

## 陷阱登记

| TRAP | 现象 | 朴素实现会怎样 | 本项目处理 | 处理位置 | 测试 |
|---|---|---|---|---|---|
| TRAP-01 | 门户 B 页面声明 `utf-8`，字节其实是 GB18030 | `open()` 崩或整页乱码，列名对不上 | 声明只作首个候选，严格解码失败即回退 gb18030，并记 `E_CHARSET_MISMATCH` | `app/modules/m1_ingest/decode.py` | `tests/test_ingest.py::test_charset_fallback_decodes_gb18030` |
| TRAP-02 | 文件头 UTF-8 BOM（本系统自己导出的 CSV 也带） | 首列名变 `\ufeff文号`，按列名取值全空 | 解码前剥离 BOM 并留下 `bom_stripped` 标记 | `app/modules/m1_ingest/decode.py` | `tests/test_ingest.py::test_bom_stripped_before_header_mapping` |
| TRAP-03 | 附件 CSV 某个单元格内含换行 | `splitlines()` 把一条记录切成两条，列数错位 | 用 `csv` 模块流式解析，单元格内换行归一为空格 | `app/modules/m1_ingest/reader.py` | `tests/test_ingest.py::test_quoted_newline_keeps_record_count` |
| TRAP-05 | 截断页末行只有 3 列、附件某行多一列 | 静默补 `None` 或错列错位 | 严格列数校验，不一致整行进隔离并带原值 | `app/modules/m2_parse/contract.py` | `tests/test_parse.py::test_column_count_mismatch_quarantined` |
| TRAP-06 | `1,200万元`、`1，200万元`、`１２３４万元` | `float()` 抛错或把全角数字当字符串 | 全角转半角 + 去千分位 + 单位后缀归一，再 Decimal 化 | `app/modules/m2_parse/coerce.py` | `tests/test_parse.py::test_amount_thousands_and_fullwidth` |
| TRAP-07 | `-` / `N/A` / `未知` / 空串 混在额度列 | 被当成 0 参与合计，清单合计虚低 | 哨兵清单集中定义，返回 `None` 三态，合计跳过 | `app/reasons.py`、`app/modules/m2_parse/coerce.py` | `tests/test_parse.py::test_sentinel_amount_is_three_state` |
| TRAP-11 | 同一时刻写成 `2026-11-03T09:00:00Z` 与 `17:00+08:00` | 落进两个日期桶，截止日差一天 | 先归一 UTC 再派生本地日期；纯日期按 +08:00 当日 00:00 | `app/modules/m2_parse/coerce.py` | `tests/test_parse.py::test_instant_timezone_offsets_collapse` |
| TRAP-14 | 同一公文号同一序号两条不同额度（800 / 950） | 后写覆盖，清单里悄悄换成一个数 | 冲突归因，整链隔离并列出两条证据行，不猜哪条为准 | `app/modules/m4_chain/chain.py` | `tests/test_chain.py::test_duplicate_seq_conflict_isolated` |
| TRAP-15 | 一个门户给「元」、另一个给「万元」 | 合计差 10000 倍 | 单位表集中在配置，先折算成万元再累加，单位随数字展示 | `app/config.py`、`app/modules/m2_parse/coerce.py` | `tests/test_parse.py::test_amount_unit_yuan_converted_to_wan` |
| TRAP-16 | 门户 B 第 2 页在最后一行的中间被截断；附件末行引号未闭合 | 半行被当有效数据，凭空多一条公告 | 页面校验表格闭合与「本页 N 条」，附件校验声明条数；半行按列数不齐隔离 | `app/modules/m1_ingest/paging.py`、`app/modules/m1_ingest/reader.py` | `tests/test_ingest.py::test_truncated_page_detected` |
| TRAP-22 | 同一公告被两个门户转载，采集顺序不定 | 合并结果随顺序变化，计数不可复现 | 先按 (门户, 页面, 行号) 定序再判定，全行签名去重 | `app/modules/m3_quality/rules.py` | `tests/test_quality.py::test_order_independence` |
| TRAP-23 | `自发布之日起 30 个工作日` 的截止端点在不在区间内 | 两端都算，相邻区间重复计入一天 | 声明左闭右开 `[发布日次日, 截止日次日)`，扣除节假日、计入调休 | `app/modules/m4_chain/workdays.py` | `tests/test_chain.py::test_workday_interval_is_half_open` |
| TRAP-27 | 冲突链人工裁定与自动判定不同（自动整链隔离，人工选 800 万那版） | 只改状态或只留最终值，事后看不出原自动判定是什么 | 以人工结论重算并记 `M_MANUAL_OVERRIDE`，快照同时留 `auto_status`/`auto_amount`/`auto_deadline` 与逐字段差异 | `app/modules/m7_review/service.py`、`app/modules/m7_review/review.py` | `tests/test_m7_review.py::test_manual_ruling_overrides_conflict_and_keeps_both_values` |
| TRAP-28 | 撤销（序号 2）之后又出现更正（序号 3） | 后到版本照常覆盖，已作废的链被复活成可申报 | 迁移表把撤销后的 发布/更正/延期 判为非法迁移，该版不能被选为有效版（`M_ILLEGAL_TRANSITION`，409）；M4 侧只登记 `C_AFTER_CANCEL`，链仍为已撤销 | `app/modules/m7_review/review.py`、`app/modules/m4_chain/chain.py` | `tests/test_m7_review.py::test_correction_after_cancel_is_illegal_transition` |
| TRAP-29 | 门户改版把额度拆成「数值/单位」两列，新记录单位列空缺 | 沿用 v1 把两列拼回文本或默认按「万元」入账，清单金额虚高且无法追溯 | 按门户契约 v2（`contract/2.0`）双列校验：单位缺失/哨兵给 `Q_AMOUNT_UNIT_MISSING` 整行隔离，不用默认单位顶替 | `app/contracts/registry.py`、`app/modules/m2_parse/coerce.py::norm_amount_split` | `tests/test_contracts.py::test_v2_missing_unit_is_quarantined_not_defaulted` |
| TRAP-30 | 数值列残留内嵌单位且与单位列冲突（`120万元` + 单位列 `元`） | 二选一猜一个单位，金额差 10000 倍 | 两列单位都解析，不一致给 `Q_AMOUNT_UNIT_CONFLICT` 整行隔离，依据里保留两个原值 | `app/modules/m2_parse/coerce.py::norm_amount_split` | `tests/test_contracts.py::test_v2_embedded_unit_conflict_is_quarantined` |
| TRAP-31 | 门户切到新契约后，重跑历史失败批次把旧额度按新金额重算 | 历史清单金额被悄悄改写，导出不可复现 | 批次绑定采集时的契约快照，批次键含快照摘要；旧批次永远走 v1 文本路径，原文与采用依据落库，导出逐字节复现 | `app/contracts/snapshot.py`、`app/modules/m5_batches/state.py` | `tests/test_contract_pipeline.py::test_historical_v1_batch_reproduces_original_amount_after_registry_changes` |
| TRAP-32 | 新契约表头与门户实际页面对不上（缺额度单位列）仍强行切换 | 切换后整门户记录全部解析失败，采集中断 | 切换前表头预检，缺必需列/额度列给 `Q_CONTRACT_PREFLIGHT_FAILED` 并保留旧版契约 | `app/contracts/registry.py::switch_with_preflight` | `tests/test_contracts.py::test_switch_preflight_failure_keeps_old_contract` |
| TRAP-33 | 解析阶段中断后重跑，重新抓取整个门户 | 重复采集、重复公告，且源页面可能已变化导致不可复现 | 采信即落 `raw_rows` 断点表；解析/质量失败续跑直接重放已存原始行，ingest 标 `reused` 不重抓，重试不新增公告 | `app/store.py`、`app/modules/m5_batches/resume.py`、`app/modules/m5_batches/service.py` | `tests/test_contract_pipeline.py::test_parse_interruption_resumes_from_saved_raw_rows` |

## 对账规则

- 页账：抓取页数 = 公告页（列表/详情/附件）+ 非公告跳过页 + 采集失败页
- 行账：表格数据行 = 入链公告 + 转载合并 + 解析隔离 + 质量隔离
- 金额账：公告金额合计 − 更正覆盖调整 = 清单合计 + 未入清单合计（Decimal，万元，2 位）
- 清单账：入链公告数 = 每条链版本数之和；链最终金额 = 清单 + 未入清单
- 四账在 `GET /api/reconcile`、`/export` 页面与 `reconcile.json` 导出中显示，差额非 0 会显式为「不平」。
- 复核账：裁定后清单条数与合计由留痕快照重算，与自动清单并存；两者的字段差异逐条列在改判差异里，不覆盖自动值。

## 反例与不变量

- 反例（朴素实现会挂）：`tests/test_chain.py::test_naive_weekday_count_differs`（只数周一至周五得到 2026-11-09，正确是 2026-11-13）、`tests/test_ingest.py::test_naive_splitlines_breaks_records`、`tests/test_parse.py::test_naive_float_parse_would_raise`、`tests/test_chain.py::test_naive_last_write_wins_would_revive_cancelled_chain`（撤销后的更正仍不能复活链）、`tests/test_m7_review.py::test_long_chain_compresses_without_changing_conclusion`（逐版覆盖会把作废链算成最后一版金额）。
- 不变量：`tests/test_batches.py::test_idempotent_rerun_adds_no_rows`（幂等）、`tests/test_quality.py::test_reconcile_amounts_invariant_holds`（守恒）、`tests/test_ingest.py::test_page_account_identity`（页账守恒）、`tests/test_m7_review.py::test_replay_state_matches_chain_for_every_chain`（回放与链判定一致）、`tests/test_m7_review.py::test_ruling_rerun_keeps_single_trail`（裁定留痕幂等）、`tests/test_contract_pipeline.py::test_resume_does_not_duplicate_announcements`（续跑不新增公告）、`tests/test_contract_pipeline.py::test_batch_key_changes_when_contract_snapshot_changes`（契约快照入批次键）。
- 端到端：`tests/test_e2e.py::test_full_pipeline_end_to_end`。

## 已知不覆盖

- 「自发布之日起三十个工作日」这类**中文数字**截止写法不支持，直接按 `Q_BAD_DEADLINE_RULE` 隔离（样例站 `b/export.csv` 第 8 行即为该情况）；中文数字解析需要额外的金额/数字词表，超出本期范围。
- 法定节假日表只维护 2026 年（`app/config.py` 的 `HOLIDAYS_2026` / `MAKEUP_WORKDAYS_2026`），跨年公告会落到「表外日期按周一至周五」的默认规则。
- 门户 B 第 2 页之后的公告（`共 13 条` 只抓到第 1 页）在样例站未落文件，`E_PAGING_NO_NEXT` 只记录断点，不自动构造第 2 页 URL。
- 只做单机 SQLite 与串行采集；没有并发抓取、限流与重试退避（内部 6 人使用场景下不需要）。
- 裁定只记「人名 + 依据」文本，没有登录态与权限校验（内部 6 人共用一台机器，人事后靠名字追）。
- 裁定后清单是**独立视图**，不回写 `chains` 表；重新采集会生成新批次，旧裁定留在旧批次里，跨批次比对需要人工带批次号。
