# Plan: 预切块数据源适配(P-PRESEG · CP-010)—— 技术实现计划

> SDD 阶段:**Phase 2 / PLAN —— 与 TASKS 一并待人工复核批准**。依据 `SPEC-PRESEG.md`(已批准)+ 调研报告 v0.2 §6 决策 D1–D8。

## 0. 架构决策(承 SPEC,PLAN 定型)

1. **新包 `pipeline/pipeline/preseg/`** 承载 preseg 域(reader / derive / adapter / cases_ingest / status_map),stages 内按 corpus_type 分支调用——与 P-QA/P-CASE 的"stage 内分支"模式同构,状态机与 orchestrator 零改。
2. **配置缝先行**(Phase A 第一件事):`profiles.yaml` 扩为真档案(qc_indicators/qc_threshold_overrides/sampling_rate/case_ref_source),`indicators.py` 改读配置;**四个既有 profile 行为写入 yaml 作默认,以"对拍测试"证零变更**——这是 B8 的兑现,也是后续所有 P-PRESEG 行为的挂载点。
3. **迁移一批**:`0013_preseg_columns`(doc_versions +7 列 / cases +3 列,全 add-only + server_default 安全;当前最新迁移 0012)。
4. **fixtures 先行**(部署测试数据):自造样例批次覆盖全部边界(插入条/款级引用/推导失败/未知效力状态/多涉案人员),真导出到手后仅替换 fixtures + 转换脚本,管线不动(SPEC §3 接缝)。
5. **测试基名前缀 `test_preseg_*`**(已核全仓未占用),满足基名唯一约定。

## 1. 组件与依赖

```
config/profiles.yaml(扩)── config.py(读)── qc/indicators.py(改读配置)
pipeline/pipeline/preseg/
├── reader.py        接收契约:扩展 manifest 校验 + blocks/cases JSONL 解析(SPEC §3)
├── derive.py        clause_label → clause_path_norm 推导器(复用 normalize.py;失败→伪路径)
├── adapter.py       块流 → ChunkSpec(seq/零页码/entity_type 文档级继承)← profile_router 分支
├── status_map.py    效力状态映射器(D3;未知值→meta_confirm)← s4_meta 挂接
└── cases_ingest.py  案例直装(虚拟文档合成 D4 + align_cited 复用 + persons D6)← s0/s4 挂接
alembic/versions/0013_preseg_columns.py
stages 触点:s0_register(preseg 批次入口+幂等键)、s1_parse(装载分支)、s2_qc(子集经配置缝)、s4_meta(status_map/biz 闸/entity_type)
```

依赖既有(零改,只调用):`normalize.py`、`chunk_id.py`、`case_ref_align.align_cited`、`case_l2.PgRegLookup`(仅查询类,不触 LLM 链)、`pg_io.upsert_case`、`corpus_rows`、`milvus_io`。

## 2. 实现顺序 + 检查点(TDD;门标注:零栈 / 真栈=PG+Milvus / 模型门=+BGE-M3 / 迁移门=alembic)

### Phase A — 配置缝 + 纯函数地基(全零栈,可并行)
- T1 配置缝 + indicators 对拍回归(**本计划最高风险回归点,先做**)
- T2 推导器 derive.py + golden 集
- T3 接收契约 reader.py(manifest 扩展列集校验 + JSONL 解析)

**检查点 A**:对拍测试证四个既有 profile QC 行为零变更;推导器 golden P=R=1.0;ruff 绿。

### Phase B — Schema + 接入(迁移门)
- T4 迁移 0013(add-only;`alembic upgrade + check` 零漂移)
- T5 S0 preseg 批次注册(幂等键 source_doc_id+content_hash;案例虚拟文档合成 D4)

### Phase C — 管线垂直切片(真栈)
- T6 S1 装载分支(块→合成 IR,page=None)+ S2 子集(⑥+元数据完整率,①②③④⑦禁用经配置)
- T7 S3 adapter(ChunkSpec:seq 规则/超长二次切分/零页码/entity_type 继承写入 D7)
- T8 S4 效力状态映射器(D3 映射表全枚举 + 未知→meta_confirm + version_status_source 留痕)

**检查点 C**:单文档(内规/外规各一)REGISTERED→INDEXED 真栈走通。

### Phase D — 案例直装 + 桥接点亮(模型门)
- T9 cases_ingest(直装 cases + persons JSONB + case_summary/case_section chunks;case_ref_source=structured 停用 LLM 链)
- T10 **桥接点亮集成测**(bridge 精确反查 + R5 桥接通道命中——本次改造的价值验收)

### Phase E — 端到端 + 收尾(全仓门)
- T11 e2e fixtures 批次全通 + 幂等重跑 + 三级引用容错(B4)+ eval 兼容(T2 冒烟/reconcile/rebuild 对 preseg 数据;T4 跳过验证)
- T12 文档收尾:RTM-PRESEG、preseg_devlog、**B3 口径变更甲方报备一页纸**、报告/SPEC 交叉指针

## 3. 并行 vs 串行

- Phase A 三任务全并行(纯函数零依赖);T4→T5 严格串行;C 内 T6/T7/T8 可并行(依赖 T4/T5);T9 依赖 T3+T4;T10 依赖 T9;T11 依赖全部。
- **前置动作(开工前)**:①现有 docs(调研报告 v0.2 + SPEC/PLAN/TASKS + devlog 积压)先 commit 入 main;②从 origin/main 开独立 worktree `feat/preseg`(多会话/栈纪律照 CLAUDE.md);③集成/模型门在共享栈串行,全仓模型门留合并前一次。

## 4. 风险与缓解

| # | 风险 | 缓解 |
|---|---|---|
| R1 | 推导器覆盖不足(真实条款标识形态未知) | golden 集 + 伪路径兜底不阻塞入库 + `preseg_raw` 批次占比告警(⚠ 阈值待样例标定,Ask-first) |
| R2 | 配置缝改 indicators 引入既有 profile 回归 | T1 对拍测试(逐 profile 逐指标与硬编码基线一致)先行;不过不进 Phase B |
| R3 | 真导出格式与接收契约差异大 | 接缝隔离(薄转换脚本);契约破坏性变更走 Ask-first |
| R4 | 效力状态值域超预期 | 未知值→meta_confirm 队列,不阻塞;映射表增删走 Ask-first |
| R5 | 幂等键冲突(源主键重发/内容变更) | content_hash 第二分量;确定性 chunk_id 使重跑覆盖安全(既有机制) |
| R6 | 共享栈互扰(集成/模型门) | 沿用栈纪律:跑前确认空闲、干净栈、绝不并发集成 |
| R7 | B3 报备被甲方打回(要求恢复页码) | D2 无回填预留是已知代价;若打回,补页码=解析原件的独立后置工序,不返工本期代码(SPEC §9-3 留了原件存档口) |

## 5. 可追溯(SPEC/决策 → 组件)

| 来源 | 落点 |
|---|---|
| SPEC §3.2/3.3 接收契约 | reader.py(T3) |
| SPEC §3.3 推导器 + D5 舍款取条 | derive.py(T2) |
| SPEC §4 S0/S1/S2/S3/S4 行为 | T5/T6/T7/T8 |
| SPEC §6 配置缝(B8) | T1 |
| SPEC §5 schema 清单 | 0013 迁移(T4) |
| D2 零页码(QC④/T4 禁用、三级引用) | T6(QC 子集)/T11(finalize 跳过+容错) |
| D3 效力状态分权威 | status_map.py(T8) |
| D4 虚拟文档 / D6 persons | cases_ingest.py(T5/T9) |
| D7 entity_type 落库+投影 | adapter.py(T7) |
| 桥接点亮(价值验收) | T10 |
| B3 报备 | T12 |
