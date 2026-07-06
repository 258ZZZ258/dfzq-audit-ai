# RTM: P-PRESEG 需求可追溯矩阵(CP-010)

> SPEC-PRESEG §8 八条 Success Criteria → 决策 → 任务 → 测试的映射。全绿 = 可交合并门。

| # | 验收标准(SPEC §8) | 决策依据 | 任务 | 测试(证据) | 状态 |
|---|---|---|---|---|---|
| 1 | fixtures 批次端到端 REGISTERED→INDEXED(orchestrator 驱动,含全边界样例) | D1/D4 | T5/T6/T7/T11 | `test_preseg_e2e::test_whole_batch_reaches_indexed`(4 dv 全 INDEXED+投影就位+直装随管线自动发生) | ✅ |
| 2 | 幂等重跑零重复 | 默认项(源主键+内容哈希) | T5/T11 | `test_preseg_s0::test_idempotent_rerun_zero_new_rows`;`test_preseg_e2e::test_idempotent_rerun_zero_drift`(dv/chunk/Milvus 三层计数零漂移) | ✅ |
| 3 | **桥接点亮**:直装后 bridge 精确反查 + R5 桥接命中(价值验收) | D1(吸收 C) | T9/T10 | `test_preseg_bridge_integration` 全套(语义命中/反查精确/R5 到条款原文) | ✅ |
| 4 | 效力状态映射四态直落 + 未知值进 meta_confirm | D3 | T5/T8 | `test_preseg_status_map`(映射全枚举/未知不猜/源权威保值/s5 不翻写);`test_preseg_s0`("试行中"入队) | ✅ |
| 5 | 推导器 golden 集 P=R=1.0,失败样例正确落伪路径 | D5(舍款取条) | T2 | `test_preseg_derive`(37 样例逐字段精确;失败=kind None 不 raise) | ✅ |
| 6 | 配置缝回归:四既有 profile QC 行为对拍零变更 | B8 | T1 | `test_preseg_profiles_seam`(逐 profile 逐指标对拍;全仓离线 849 passed) | ✅ |
| 7 | 三级引用:零页码不报错、降级留痕 | **D2** | T7/T10/T11 | `test_preseg_bridge_integration::test_zero_page_citation_tolerated`;`test_preseg_e2e::test_finalize_t4_exempt_t2_ran`(T4 显式豁免非静默,T2 照跑) | ✅ |
| 8 | 全仓既有测试绿 + ruff + alembic check 零漂移 | — | 合并门 | 迁移 0013/0014 已 check 零漂移;**全仓模型门全量留合并前**(worktree 纪律) | ⏳ 合并门 |

## 与 TASKS 原案的偏差记录(全部已在 commit/devlog 留痕)

| 偏差 | 原因 |
|---|---|
| 效力状态映射从 T8(S4)前移 T5(S0) | manifest 原值仅 S0 可见;T8 改辖 s5 `resolve_live_status` 源权威尊重 |
| QC 哨兵化(门仅⑥;元数据完整率降报告项) | 实施期追加决策 **D10**(源的责任不拦,拦=覆盖缺口) |
| 接入形态 = SQL 直连(批次快照经现有入口) | 实施期追加决策 **D9**;文件契约退居内部表示,puller 待甲方库 schema |
| `entity_types` 列 T3/T4 遗漏 → 迁移 0014 补差 | D7 文档级"适用对象"载体缺失,T7 发现即补 |
| 全局 rebuild 不在测试中触发(冷备在位断言替代) | 共享栈纪律;全局 rebuild 留合并门/人工 |
| 修既有 `case_ref_align._clause_no` 边界 bug(插入条+款尾) | fixtures 暴露;case 通道 84 passed 回归证零影响 |
