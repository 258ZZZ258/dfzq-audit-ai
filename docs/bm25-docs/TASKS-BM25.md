# Tasks: Milvus 原生 BM25 稀疏通道(CP-012)—— 任务分解

> 状态:**与 PLAN 一并待人工复核批准**。依据 `SPEC-BM25.md`(已批准)+ `PLAN-BM25.md`。
> 约定:每任务 ≤5 文件、TDD(先失败测试后实现)、含 Acceptance/Verify。**测试基名用 `test_*bm25*` 前缀**(已核未占用)。
> 硬约束:RRFRanker 融合语义零改、dense 仍 BGE-M3 1024 维、`sparse_backend` 默认 `bge` byte 等价、PG 零改(不加迁移)、
> 默认路径零 LLM、检索前置过滤语义(status/corpus/perm_tag)不碰。
> 门:零栈(默认)/ 真栈=PG+Milvus2.5 / 模型门=+本地 BGE-M3(dense;未起 skip)/ 升级门=`demo down -v && demo up` on 2.5。

- [ ] **T1:Milvus 2.4→2.5 升级 + bge 全量回归** — Phase A(**最高回归风险,先做**)
  - Acceptance:`compose.yaml` `milvusdb/milvus:v2.5.x`(patch 钉死);`libs/common/pyproject.toml` + `pipeline/pyproject.toml` `pymilvus>=2.5,<2.6`;复核 `setuptools<81` 钉子(pymilvus 2.5 若不再需 `pkg_resources` 则放开、否则保留并注明);既有 `milvus_io`/schema import 与 API(`RRFRanker`/`AnnSearchRequest`/`hybrid_search`/`FieldSchema`)在 2.5 无破。
  - Verify:**升级门**——`demo down -v && demo up` 起 2.5.x(collection/index 建成);既有 bge 全量绿:`pytest pipeline/tests/test_milvus_io.py pipeline/tests/test_s5.py pipeline/tests/test_milvus_search_text.py pipeline/tests/test_milvus_search_expr.py query/tests/test_sparse_boost_integration.py -q`;`demo verify smoke && demo verify reconcile && demo verify idempotency` 通过;ruff 绿。
  - Files:`compose.yaml`、`libs/common/pyproject.toml`、`pipeline/pyproject.toml`。 Dependencies:None。 Scope:M。

- [ ] **T2:`sparse_backend` 配置缝(add-only)** — Phase A
  - Acceptance:`config.py` `EmbeddingConfig` +`sparse_backend: str="bge"`(env `PIPELINE_SPARSE_BACKEND`,枚举 `bge|bm25|none`,非法值 fail-fast)+ analyzer/bm25 参数(`bm25_analyzer_type="chinese"`、`bm25_k1`/`bm25_b` ⚠默认占位);`settings.toml` `[embedding]` 注释登记;缺失回退 `bge`(现状)。
  - Verify:`pytest pipeline/tests/test_config_bm25.py`——默认 `bge`;env 覆盖;非法值抛;既有 `test_query_config`/`test_embedding_client` 不回归(add-only)。零栈。
  - Files:`pipeline/pipeline/config.py`、`config/settings.toml`、`pipeline/tests/test_config_bm25.py`。 Dependencies:None(T1 后)。 Scope:S。

- [ ] **T3:schema 工厂 `audit_corpus_schema(sparse_backend)`** — Phase A
  - Acceptance:`milvus_schema.py::audit_corpus_schema(sparse_backend="bge")`;`bge` 形态与现状**逐字段一致**(字段名/类型/partition key/dim,byte 等价);`bm25` 形态:`text` 字段 `enable_analyzer=True`+`analyzer_params={"type":<cfg>}`,`schema.add_function(Function(FunctionType.BM25, input=["text"], output=["sparse_vec"]))`,其余字段不变。
  - Verify:`pytest pipeline/tests/test_schema_bm25.py`——bge 形态字段清单对拍现状(参数化逐字段);bm25 形态含 analyzer+Function(断言 `schema.functions` / text 字段 `enable_analyzer`);`sparse_vec` 仍 `SPARSE_FLOAT_VECTOR`。零栈(schema 对象断言,不连栈)。
  - Files:`libs/common/common/milvus_schema.py`、`libs/common/tests/test_schema_bm25.py`。 Dependencies:None(T1 后,需 pymilvus 2.5 的 `Function`)。 Scope:S。

### 检查点 A:2.5 栈 bge 全量 + verify 三件套绿;config/schema bge 形态对拍零变更;ruff 绿。

- [ ] **T4:建集合 bm25 分支 + upsert 条件不写 sparse** — Phase B
  - Acceptance:`milvus_io` 建集合读 `sparse_backend`:bm25 时用 `audit_corpus_schema("bm25")`、`sparse_vec` 索引 `{"index_type":"SPARSE_INVERTED_INDEX","metric_type":"BM25","params":{"bm25_k1":..,"bm25_b":..}}`(`milvus_io.py:159-160` 分支);`upsert_batch` bm25 时 **payload 剔除 `sparse_vec` key**(Milvus function 从 `text` 产出)、确认 `text` 写入(BM25 输入,非空);bge 分支 byte 等价。
  - Verify:`pytest pipeline/tests/test_milvus_io_bm25.py`(真栈)——bm25 建集合含 Function、upsert 后 `sparse_vec` 由 Milvus 填充(query 得非空稀疏)、payload 不含 sparse key(单元断言 `_corpus_row_to_dict` bm25 分支);bge upsert payload 仍含 sparse(对拍)。
  - Files:`pipeline/pipeline/index/milvus_io.py`、`pipeline/tests/test_milvus_io_bm25.py`。 Dependencies:T2、T3。 Scope:M。

- [ ] **T5:`search(query_text)` bm25 分支 + hybrid 传参** — Phase B
  - Acceptance:`milvus_io.search` 加 `query_text: str|None=None`;bm25 时 sparse `AnnSearchRequest([query_text],"sparse_vec",{"metric_type":"BM25"},...)`、dense 仍传向量、`hybrid_search`+`RRFRanker` 不变;bge 时走现状 sparse 向量分支(**签名 add-only,query_text=None + bge → byte 等价**);`hybrid.py::_search_partition` 把 `query`(原文)传入 `search(query_text=query)`。空 query_text(bm25 却未传)→ 清晰报错(不静默 dense-only)。
  - Verify:`pytest pipeline/tests/test_milvus_io_bm25.py::test_search_bm25`（真栈:bm25 查询命中、走 hybrid 非 dense_only 兜底）+ `query/tests/test_hybrid_bm25.py`(hybrid 传 query_text；bge 分支入参对拍不变)。bge 回归:`test_milvus_search_text`/`test_sparse_boost_integration` 绿。
  - Files:`pipeline/pipeline/index/milvus_io.py`、`query/query/retrieve/hybrid.py`、`query/tests/test_hybrid_bm25.py`。 Dependencies:T4。 Scope:M。

- [ ] **T6:静默降级护栏(bge 空 sparse fail-fast)** — Phase B
  - Acceptance:摄取路径 `sparse_backend=bge` 且 embedding 返空 sparse(如误配 vLLM only-dense)→ **fail-fast**(清晰错误指向 sparse_backend 配置),不再静默走 dense-only 冷备空 sparse;bm25/none backend 不触发该校验(预期无客户端 sparse)。
  - Verify:`pytest pipeline/tests/test_sparse_guard_bm25.py`——monkeypatch endpoint 返 `{}` + bge → 摄取抛;bm25 + 空客户端 sparse → 不抛(Milvus 侧产);既有 bge 正常 sparse 路径不回归。零栈。
  - Files:`pipeline/pipeline/index/corpus_rows.py`(或 `s5_embed_index.py` 校验点)、`pipeline/tests/test_sparse_guard_bm25.py`。 Dependencies:T2。 Scope:S。

### 检查点 B:bm25 建集合+upsert+hybrid 查询真栈走通;bge 查询/upsert byte 等价。

- [ ] **T7:corpus_rows bm25 冷备 dense-only + rebuild 从 text 重算** — Phase C
  - Acceptance:bm25 时冷备只写 `dense_vec_cold`、**不写 `sparse_vec_cold`**(`corpus_rows.py:114` 的 sparse 齐全校验按 backend 放宽);`rebuild` bm25 从 PG `chunks.text` 重灌(不反序列化 sparse,Milvus function 重算 BM25);bge 冷备/rebuild 现状不变(dense+sparse 双冷存)。
  - Verify:`pytest pipeline/tests/test_rebuild_bm25.py`(真栈/模型门)——bm25 rebuild 后召回与首灌一致(发文字号查询同命中);冷备行 `sparse_vec_cold=None` 断言;bge rebuild 回归(`eval` reconcile/rebuild 现状测绿)。
  - Files:`pipeline/pipeline/index/corpus_rows.py`、`pipeline/tests/test_rebuild_bm25.py`。 Dependencies:T4。 Scope:M。

- [ ] **T8:bm25 端到端 + 发文字号精确命中(价值验收)** — Phase C
  - Acceptance:fixtures 含发文字号/条款号显式语料(如"证监会公告〔2023〕15号 …第二十一条 …");`sparse_backend=bm25` 端到端 `REGISTERED→…→INDEXED` 全通、幂等重跑零重复;hybrid(BM25+dense)查询发文字号,目标 chunk 命中且较**纯 dense**(dense_only)名次提升;jieba analyzer 对发文字号切词达标(命中即达标,否则 R3 触发)。
  - Verify:`pytest pipeline/tests/test_e2e_bm25.py`(**模型门**:PG+Milvus2.5+本地 BGE-M3 dense,未起 skip)——端到端 INDEXED + 幂等零漂移 + 发文字号命中优于 dense_only + 条款号精确召回;三级引用无页码不报错(容错)。
  - Files:`pipeline/tests/test_e2e_bm25.py`(+fixtures 增量)。 Dependencies:T4、T5、T7。 Scope:M。

### 检查点 C:bm25 单文档端到端 INDEXED;发文字号命中优于纯 dense;rebuild 召回一致。

- [ ] **T9:全仓模型门全量 + 文档收尾** — Phase D(合并门)
  - Acceptance:全仓模型门全量绿(bge 默认 + bm25 opt-in 双路);ruff 绿;`alembic check` 零漂移(PG 未改);`RTM-BM25` 全绿;`bm25_devlog.md` 记 **rebuild 冷备契约 delta**(bm25 sparse 不冷存 vs v1.6 §491)、analyzer 选型结论、setuptools 钉子处置;内网自查清单(compose≥1.25.1 + AVX2 + 离线镜像准入)。
  - Verify:`pytest -q`(全仓,干净 2.5 栈 + 本地 BGE-M3);`ruff check .`;`alembic check`;RTM 状态更新。
  - Files:`docs/bm25-docs/RTM-BM25.md`、`docs/bm25-docs/bm25_devlog.md`、(CLAUDE.md 模块索引指针增行)。 Dependencies:全部。 Scope:S。

## 依赖图

```
T1(升级门,先行)──┬─ T2 ─┬─ T4 ─┬─ T5 ──┐
                  └─ T3 ─┘      ├─ T7 ──┼─ T8 ─ T9(合并门)
                     T6(独立)───────────┘
```
