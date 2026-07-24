# Plan: Milvus 原生 BM25 稀疏通道(CP-012)—— 技术实现计划

> SDD 阶段:**Phase 2 / PLAN —— 与 TASKS 一并待人工复核批准**。依据 `SPEC-BM25.md`(已批准 2026-07-24)。

## 0. 架构决策(承 SPEC,PLAN 定型)

1. **不新增包**:改动落既有 `libs/common/common/milvus_schema.py` + `pipeline/pipeline/index/{milvus_io,corpus_rows}.py`
   + `config.py` + `query/query/retrieve/hybrid.py`。与 P-QA/P-CASE 的"函数内分支"同构——稀疏**生产者**按
   `sparse_backend` 分形态,融合层(`hybrid_search`+`RRFRanker`)零改。
2. **升级门先行**(Phase A 第一件事,最高回归风险):Milvus 2.4→2.5 + pymilvus bump,**bge 路径全量回归证零变更**
   ——这是本计划的 B8 式兑现(既有稀疏行为在新栈上 byte 等价),也是 bm25 分支的挂载前提。
3. **配置缝 add-only**:`sparse_backend` 默认 `bge`(现状),`config` 缺失回退 bge——**未登记即现状**,同 PRESEG profile 缝范式。
4. **schema 工厂参数化**:`audit_corpus_schema(sparse_backend="bge")`;bge 形态与现状**逐字段对拍**证零变更,bm25 形态
   加 analyzer + `Function(BM25)` + sparse 索引 metric `BM25`。
5. **PG 零改**:本切片不动 `pg_models`/不加迁移(`sparse_vec` 是 Milvus 侧产出方变化);冷备 bm25 只 dense 是
   `corpus_rows` 读写分支,非 schema 变更。`alembic check` 应零漂移(合并门核)。
6. **测试基名前缀 `test_*bm25*`**(已核全仓未占用),满足基名唯一约定。

## 1. 组件与依赖

```
compose.yaml(milvus v2.5.x)· pyproject(pymilvus >=2.5,<2.6;setuptools 钉子复核)
config/settings.toml + pipeline/pipeline/config.py     [embedding] sparse_backend + analyzer/bm25 参数(add-only,默认 bge)
libs/common/common/milvus_schema.py                    audit_corpus_schema(sparse_backend):bge=现状 | bm25=analyzer+Function(BM25)
pipeline/pipeline/index/milvus_io.py                   建集合(bm25 挂 Function + sparse index metric BM25)· upsert 条件不写 sparse ·
                                                       search(query_text) bm25 分支 · bge 空 sparse fail-fast 护栏
pipeline/pipeline/index/corpus_rows.py                 bm25:冷备只 dense · rebuild 从 text 重算(sparse 免冷存)
query/query/retrieve/hybrid.py                         向 search 传 query_text(bm25);bge 分支不变
```

依赖既有(零改,只调用):`embedding_client.EndpointClient`(dense-only 已容忍缺 sparse)、`chunk_id`、`pg_io`、
`RRFRanker`/`AnnSearchRequest`/`hybrid_search`(pymilvus,2.4→2.5 API 稳定)。

## 2. 实现顺序 + 检查点(TDD;门:零栈 / 真栈=PG+Milvus2.5 / 模型门=+BGE-M3·vLLM dense / 升级门=demo down -v && up on 2.5)

### Phase A — 升级 + 配置缝 + schema 工厂(升级门 + 零栈)
- **T1 Milvus 2.4→2.5 升级 + bge 全量回归**(**最高回归风险,先做**;升级门)
- **T2 `sparse_backend` 配置缝**(add-only,默认 bge 对拍;零栈)
- **T3 schema 工厂 `audit_corpus_schema(sparse_backend)`**(bge 逐字段对拍 + bm25 形态断言;零栈)

**检查点 A**:2.5 栈上 bge 全量测试 + `verify smoke/reconcile/idempotency` 绿;config/schema bge 形态对拍零变更;ruff 绿。

### Phase B — 摄取/查询 bm25 分支(真栈)
- **T4 建集合 bm25 分支 + upsert 条件不写 sparse**(真栈:建集合挂 Function、upsert 由 Milvus 产 sparse)
- **T5 `search(query_text)` bm25 分支 + hybrid.py 传参**(真栈:BM25 sparse req 传原文;bge byte 等价)
- **T6 静默降级护栏**(bge + endpoint 空 sparse → fail-fast;零栈 monkeypatch)

**检查点 B**:bm25 建集合 + upsert + hybrid 查询真栈走通(dense 用本地 BGE-M3 占位亦可);bge 查询 byte 等价。

### Phase C — 冷备/rebuild + 端到端(模型门)
- **T7 corpus_rows bm25 冷备 dense-only + rebuild 从 text 重算**(真栈/模型门)
- **T8 bm25 端到端**(REGISTERED→INDEXED + hybrid 召回 + **发文字号精确命中** + rebuild 一致;模型门)

**检查点 C**:bm25 单文档端到端 INDEXED、发文字号查询命中较纯 dense 提升、rebuild 召回一致。

### Phase D — 收尾(全仓门)
- **T9 全仓模型门全量 + 文档收尾**（ruff + `alembic check` 零漂移 + RTM/devlog + **rebuild 冷备契约 delta 文档化** + 内网自查清单）

## 3. 并行 vs 串行

- **T1 先行且串行**(升级门,升级后 bge 回归是其余一切的地基);T2/T3 T1 后可并行(零栈)。
- T4→T5 串行(建集合先于查询);T6 独立(可与 B 并行)。T7 依赖 T4;T8 依赖 T4/T5/T7;T9 依赖全部。
- **前置动作(开工前)**:①SDD 文档集(SPEC/PLAN/TASKS/RTM)先 commit 上 `feat/bm25-sparse`;②**升级共享 Milvus 栈到
  2.5 须协调**——全局单例栈、多 worktree 共用,升级前确认对方空闲、`demo down -v && demo up` 取干净 2.5 栈,**绝不并发集成**;
  ③pymilvus bump 波及全仓 import,**全仓模型门全量留合并前一次**;④内网 §Q5 自查(compose≥1.25.1 + AVX2)并行、不阻塞本地开发。

## 4. 风险与缓解

| # | 风险 | 缓解 |
|---|---|---|
| R1 | Milvus 2.5 升级引入既有 bge 行为回归 | T1 升级门先行:2.5 栈上 bge 全量 + verify 三件套对拍;不过不进 Phase B |
| R2 | pymilvus 2.5 破全仓 import / `setuptools<81` 钉子失效 | T1 内复核:`Function`/`FunctionType` 新增(additive)、`RRFRanker`/`AnnSearchRequest`/schema API 2.4→2.5 稳定;setuptools 放开前验证 2.5 不需 `pkg_resources` |
| R3 | **jieba analyzer 把发文字号切碎**(`证监会公告〔2023〕15号`)→ 精确命中不达标 | T8 集成硬验证发文字号命中;不达标 → 自定义 analyzer(Ask-first 范围变更,不返工主通道) |
| R4 | bm25 upsert 误带 `sparse_vec`(function 产出字段)致 Milvus 拒插 | T4 断言 upsert payload 剔除 `sparse_vec` key;确认 `text` 已在 upsert 写入(BM25 function 输入) |
| R5 | 内网 vLLM dense 与本地 BGE-M3 dense 不逐位一致 → 集成用哪个 dense | 本地开发/集成用本地 BGE-M3 出 dense(dense 空间由模型定,内网切端点两侧一起切,predeploy 契约);bm25 稀疏与 dense 生产者解耦,不受影响 |
| R6 | 共享栈互扰(升级/集成/模型门) | 沿用栈纪律:升级前确认空闲、干净 2.5 栈、绝不并发集成;全仓模型门留合并前 |
| R7 | rebuild 冷备契约 delta(bm25 sparse 不冷存)被误解为回归 | T7 + T9 文档化:dense 仍冷存免重编码,sparse 由 Milvus 从 text 重算;与 v1.6 §491 差异写入 devlog |

## 5. 可追溯(SPEC §8 SC → 组件/任务)

| 来源(SPEC §8) | 落点 |
|---|---|
| SC1 schema 工厂 bge/bm25 形态 | `milvus_schema`(T3) |
| SC2 bm25 upsert 不写 sparse + 冷备 dense-only + 端到端 | `milvus_io`(T4)/`corpus_rows`(T7)/e2e(T8) |
| SC3 bm25 查询 query_text + RRF + 发文字号命中 | `milvus_io.search`/`hybrid`(T5)/e2e(T8) |
| SC4 rebuild 从 text 重算 | `corpus_rows`(T7) |
| SC5 bge byte 等价 on 2.5 | 升级 + 回归(T1)/config·schema 对拍(T2/T3) |
| SC6 静默降级护栏 | `milvus_io`/摄取校验(T6) |
| SC7 Milvus 2.5 升级冒烟 | 升级门(T1) |
| SC8 config add-only + pymilvus bump + alembic 零漂移 | T2/T1/合并门(T9) |
| SC9 全仓 + ruff + DAG | 合并门(T9) |
```
