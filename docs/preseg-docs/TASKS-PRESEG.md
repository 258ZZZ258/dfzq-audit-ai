# Tasks: 预切块数据源适配(P-PRESEG · CP-010)—— 任务分解

> 状态:**与 PLAN 一并待人工复核批准**。依据 `SPEC-PRESEG.md`(已批准)+ `PLAN-PRESEG.md`。
> 约定:每任务 ≤5 文件、TDD(先失败测试后实现)、含 Acceptance/Verify。**测试基名全用 `test_preseg_*` 前缀**(已核未占用)。
> 硬约束:chunk_id 公式不动、add-only(0013)、状态机零改、既有四 profile 行为零变更(对拍证明)、默认零 LLM、单向只读。
> 门:零栈(默认)/ 真栈=PG+Milvus / 模型门=+本地 BGE-M3(未起/无模型 skip)/ 迁移门=alembic upgrade+check。

- [ ] **T1:profile 配置缝 + indicators 对拍回归** — Phase A
  - Acceptance:`config/profiles.yaml` 扩为 per-profile 档案(`qc_indicators[]`/`qc_threshold_overrides{}`/`sampling_rate`/`case_ref_source`),现四 profile 行为(indicators.py:222-237 硬编码启用集 + qc_thresholds.yaml 全局阈值)原样写入 yaml 作默认;`config.py` 读取;`qc/indicators.py::indicators_for` + `qc/gate.py` 改读配置(硬编码字典删除,配置缺失回退全七项现状)。
  - Verify:`pytest pipeline/tests/test_preseg_profiles_seam.py`——**对拍**:P-INT/P-EXT/P-QA/P-CASE 逐 profile 的启用集与阈值,改造前后逐项相等;未登记 corpus_type 回退行为不变;`case_ref_source` 默认值(P-CASE=llm)。零栈。
  - Files:`config/profiles.yaml`、`pipeline/pipeline/config.py`、`pipeline/pipeline/qc/indicators.py`、`pipeline/pipeline/qc/gate.py`、`pipeline/tests/test_preseg_profiles_seam.py`。 Dependencies:None。 Scope:M。

- [ ] **T2:clause_path_norm 推导器 + golden 集** — Phase A
  - Acceptance:`pipeline/pipeline/preseg/derive.py::derive_norm(clause_label) -> DeriveResult(norm|None, kuan_raw|None)`:复用 `normalize.normalize_clause_no`;支持"第X条/第X条之一/第X章第X条/小数式 2.17";款级**舍款取条**(D5),款号原文回带;无法推导→`norm=None`(调用方落伪路径 `preseg/{block_seq}`)。纯函数零 IO。
  - Verify:`pytest pipeline/tests/test_preseg_derive.py` + golden 集 `pipeline/tests/golden/preseg_labels.jsonl`(≥30 样例:常规/插入条/章前缀/小数式/款级/垃圾输入),P=R=1.0(仿条款树 golden 先例)。零栈。
  - Files:`pipeline/pipeline/preseg/__init__.py`、`pipeline/pipeline/preseg/derive.py`、`pipeline/tests/test_preseg_derive.py`、`pipeline/tests/golden/preseg_labels.jsonl`。 Dependencies:None。 Scope:S。

- [ ] **T3:接收契约 reader(扩展 manifest + blocks/cases JSONL)** — Phase A
  - Acceptance:`preseg/reader.py`:P-PRESEG manifest 扩展列集(11+7,SPEC §3.2)**精确匹配**校验(缺/多列整批拒收,复用 s0 语义);`read_blocks(path)`/`read_cases(path)` 解析+逐行校验(必填缺失→行级 error 汇总,整文件拒收);产出中间 dataclass(`PresegDoc`/`PresegBlock`/`PresegCase`)。
  - Verify:`pytest pipeline/tests/test_preseg_reader.py`——合法样例全解析;列集不匹配/坏行/空文件各自拒收路径;fixtures 首版(含全部边界样例,后续任务复用)。零栈。
  - Files:`pipeline/pipeline/preseg/reader.py`、`pipeline/tests/test_preseg_reader.py`、`pipeline/tests/fixtures/preseg_batch/`(manifest.xlsx+blocks/+cases.jsonl)。 Dependencies:None。 Scope:M。

### 检查点 A:T1 对拍零变更 + T2 golden 满分 + T3 拒收路径全覆盖;ruff 绿。

- [ ] **T4:迁移 0013(add-only)** — Phase B
  - Acceptance:`pg_models.py`:DocVersion +`source_doc_id`(索引)/`content_hash`/`version_status_source`/`issuer_level_src`/`tags`(JSONB)/`file_no`/`source_created_by`;Case +`persons`(JSONB)/`occurred_at`(Date)/`source_url`;全部 nullable 或 server_default,autogenerate → `0013_preseg_columns`。
  - Verify:**迁移门**——`alembic upgrade head` + `alembic check` 零漂移(主测试栈,跑前确认栈空闲);`ruff check --fix && ruff format alembic/versions`;既有全量测试绿(列加法零行为变更)。
  - Files:`libs/common/common/pg_models.py`、`alembic/versions/0013_preseg_columns.py`。 Dependencies:None(可与 A 并行,先于 T5)。 Scope:S。

- [ ] **T5:S0 preseg 批次注册 + 虚拟文档合成** — Phase B
  - Acceptance:`s0_register` 增 preseg 入口分支(corpus_type=P-PRESEG 时走 reader,不走文件白名单/magic number);幂等键=`source_doc_id+content_hash`(重发同 hash 跳过、变 hash 走新版本+版本链);案例记录→虚拟 doc_version(`source_format="preseg"`,raw 空+标记,D4);blocks 文件存 ObjectStore(替代 raw 语义)。
  - Verify:`pytest pipeline/tests/test_preseg_s0.py`(真栈 PG:注册/幂等重跑零重复/虚拟文档字段/批次报告计数;未起 skip)。
  - Files:`pipeline/pipeline/stages/s0_register.py`、`pipeline/pipeline/preseg/cases_ingest.py`(仅虚拟文档合成部分)、`pipeline/tests/test_preseg_s0.py`。 Dependencies:T3、T4。 Scope:M。

- [ ] **T6:S1 装载分支 + S2 子集** — Phase C
  - Acceptance:`s1_parse` 增 P-PRESEG 分支:blocks→合成 IR(每块一 block,`page=None`,`clause_label` 存 block 属性),IR 契约保真;S2 经 T1 配置缝启用 {⑥文本质量, 新指标**必填元数据完整率**}(新指标实现于 indicators.py,配置注册),①②③④⑦禁用。
  - Verify:`pytest pipeline/tests/test_preseg_load_qc.py`——合成 IR 结构/页码为空;QC 子集:元数据齐全→过,缺必填→QC_FAILED 进队列;①②③④⑦确未执行(配置驱动)。零栈(IR/QC 纯函数)。
  - Files:`pipeline/pipeline/stages/s1_parse.py`、`pipeline/pipeline/qc/indicators.py`、`config/profiles.yaml`、`pipeline/tests/test_preseg_load_qc.py`。 Dependencies:T1、T3。 Scope:M。

- [ ] **T7:S3 preseg_adapter(块流→ChunkSpec)** — Phase C
  - Acceptance:`preseg/adapter.py::build_preseg_specs`:每块一 ChunkSpec(`seq=0`;超预算按句末边界二次切分 seq 递增、无边界硬切标 oversize);norm 来自 derive(失败→`preseg/{block_seq}` + `chunk_type=preseg_raw`);零页码;文档级适用对象继承写 `chunks.entity_type`(D7);`profile_router` 加分支;chunk_id 走既有 `compute_chunk_id`。
  - Verify:`pytest pipeline/tests/test_preseg_adapter.py`——chunk_id 幂等(同输入同 id)/seq 规则/oversize/伪路径标记/entity_type 继承/表格块 `is_table` 透传。零栈。
  - Files:`pipeline/pipeline/preseg/adapter.py`、`pipeline/pipeline/chunking/profile_router.py`、`pipeline/tests/test_preseg_adapter.py`。 Dependencies:T2、T6。 Scope:M。

- [ ] **T8:S4 效力状态映射器** — Phase C
  - Acceptance:`preseg/status_map.py`:映射表(现行有效→effective/已废止|失效→abolished/被替代|已修订→superseded(无目标→meta_confirm)/尚未生效→upcoming/**未知→meta_confirm**);仅 P-PRESEG 生效(D3 按通道分权威),写 `version_status_source="source"` + pipeline_events 留痕;自建通道行为零改;`issuer_level_src` 原值落列(INT8 序映射留 Open Question,暂走既有字典派生)。
  - Verify:`pytest pipeline/tests/test_preseg_status_map.py`——映射表全枚举/未知值进队列/留痕事件/自建通道回归(P-INT 件 version_status 仍由链推导)。真栈 PG(队列/事件)。
  - Files:`pipeline/pipeline/preseg/status_map.py`、`pipeline/pipeline/stages/s4_meta.py`、`pipeline/tests/test_preseg_status_map.py`。 Dependencies:T4、T5。 Scope:M。

### 检查点 C:单文档(内规+外规各一)真栈 REGISTERED→INDEXED 走通;既有全量测试绿。

- [ ] **T9:案例直装(cases_ingest 完整版)** — Phase D
  - Acceptance:`cases_ingest.py` 完整链:PresegCase→虚拟 dv(T5 已建)→`violated_regulations` 经 `align_cited`+`PgRegLookup` 归一对齐(复用,零改)→`upsert_case`(cited_regulations/persons JSONB/occurred_at/source_url);"问题汇总"→`case_summary` chunk、"案例描述"→`case_section` chunk(伪路径 `case/{k}` 先例);`case_ref_source=structured` 时 s4 的 `case_l2.apply` 支路不进(LLM 零调用);对齐失败置 `ref_unresolved`(现状口径)。
  - Verify:`pytest pipeline/tests/test_preseg_cases_ingest.py`(真栈 PG:直装字段全断言/对齐成功与失败路径/LLM client 零实例化(mock 断言)/chunk 落库)。
  - Files:`pipeline/pipeline/preseg/cases_ingest.py`、`pipeline/pipeline/stages/s4_meta.py`、`pipeline/tests/test_preseg_cases_ingest.py`。 Dependencies:T5、T7、T8。 Scope:M。

- [ ] **T10:桥接点亮集成测(价值验收)** — Phase D
  - Acceptance:fixtures 含"案例 A 引用外规 X 第21条"+外规 X 正文;入库后:`bridge.build_cited_index`/`cases_for_clauses` 由外规条款反查命中案例 A;`r5_judgment.resolve_cited_clauses` 由案例桥接到 effective 外规 chunk 并优先合并;`retrieve_cases` P-CASE/P-PRESEG 分区口径确认(案例虚拟文档的 corpus_type 归属断言)。
  - Verify:`pytest query/tests/test_preseg_bridge_integration.py`(**模型门**:PG+Milvus+BGE-M3,未起 skip)——反查命中/桥接命中/三级引用无页码不报错(B4 容错)。
  - Files:`query/tests/test_preseg_bridge_integration.py`(+fixtures 增量)。 Dependencies:T9。 Scope:M。

### 检查点 D:桥接双通道有数据命中——本次改造价值证明。

- [ ] **T11:端到端 + eval 兼容 + 幂等** — Phase E
  - Acceptance:fixtures 全批次(内规/外规/案例,含全部边界样例)`demo ingest` e2e `REGISTERED→INDEXED`;幂等重跑零重复;finalize:T4 回放对 P-PRESEG **跳过**(D2)、T2 冒烟照跑通过;`reconcile`/`rebuild` 对 preseg 数据行为正确(冷备回灌含零页码块)。
  - Verify:`pytest pipeline/tests/test_preseg_e2e.py`(模型门,未起 skip);`demo verify reconcile` + `demo rebuild` 手动核对留痕。
  - Files:`pipeline/tests/test_preseg_e2e.py`、`pipeline/pipeline/stages/finalize.py`(T4 跳过分支)、fixtures 增量。 Dependencies:T6–T10。 Scope:M。

- [ ] **T12:文档收尾 + 报备** — Phase E
  - Acceptance:`docs/preseg-docs/RTM-PRESEG.md`(SPEC §8 八条验收→任务→测试映射)+ `preseg_devlog.md`(决策/踩坑);**B3 口径变更甲方报备一页纸**(四级→三级理由+D2 决策引用,交用户发出);调研报告 v0.2/SPEC 交叉指针补齐;CLAUDE.md 模块索引加 preseg 行。
  - Verify:文档互链可达;RTM 覆盖八条 Success Criteria 无缺口。
  - Files:`docs/preseg-docs/RTM-PRESEG.md`、`docs/preseg-docs/preseg_devlog.md`、`docs/preseg-docs/B3-报备-页码降级.md`、`CLAUDE.md`。 Dependencies:T1–T11。 Scope:S。

### 合并门(全仓,worktree → PR 前):全量 pytest(含模型门,干净栈)+ ruff + alembic check + 对拍回归复跑;Codex 审查闭环(findings→修复→复审)后交 PR。
