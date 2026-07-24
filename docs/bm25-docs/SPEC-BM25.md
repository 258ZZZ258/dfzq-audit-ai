# Spec: Milvus 原生 BM25 稀疏通道(内网 vLLM only-dense 适配 · CP-012)

> SDD 阶段:**Phase 1 / SPECIFY —— 待人工复核批准后进 PLAN**。
> 触发:内网 BGE-M3 部署在 **vLLM** 上,其嵌入端点**只吐 dense、不吐 sparse(lexical_weights)**——
> 命中 CP-005-② "embedding endpoint 须暴露 dense+sparse 双输出" 前置验证项**被证伪**。现状后果:`EndpointClient`
> 拿不到 sparse → `_normalize_sparse` 返 `{}`(`embedding_client.py:106`)→ 每 chunk 空 sparse → 每次查询命中
> `milvus_io.py:260` 的 `空 sparse → dense-only 兜底`,**且静默**(predeploy devlog 早警告,`docs/predeploy-docs/predeploy_devlog.md:52`)。
> 上游:predeploy_devlog(嵌入端点决策)/ SPEC-SPARSE(§5.4 稀疏通道)/ v1.6 §8.2(schema)、§491(rebuild 冷备契约)。
>
> **已决(2026-07-24,AskUserQuestion):**
> 1. **目标设计 = A1**(升 Milvus 2.4→2.5,用**原生 BM25 全文检索**产稀疏,替代 BGE-M3 lexical)。分水岭"内网
>    **Docker 19.03 x86 Linux** 能否上 Milvus 2.5"已用**官方文档确认通过**:Milvus 2.5.x 官方最低支持 **Docker 19.03+ /
>    Docker Compose 1.25.1+**、x86_64 主支持(`milvus.io/docs/v2.5.x/prerequisite-docker.md`)。弃 A2(客户端 BM25
>    自管 IDF)——greenfield 下升级零数据迁移,A2 的 IDF 冻结/两侧模型同步纯负担无对冲。
> 2. **切片范围 = 核心 BM25 主通道先行**;查询侧 `sparse_boost`(BM25 版 query 文本增强)**留下切片**。BM25 对高 IDF
>    稀有词(发文字号/条款号)天然强命中,提权大部分免费;先验证主通道 + 中文分词(发文字号切词 = 主风险)。
> 3. **BM25 文本源 = 复用现有 `text` 字段**(开 analyzer),不加专用全文字段。
> 4. **(默认已决)`sparse_backend` 默认 `bge`**(byte 等价保既有本地行为 + 全部既有测试),`bm25` **opt-in**(内网)。
>    Milvus 2.5 向后兼容 2.4 稀疏 API(`SPARSE_FLOAT_VECTOR`/`hybrid_search`/`RRFRanker`),故 bge 路径升级后不回归。

## 0. 约束装配(输入面清单)

- **权威上游**:predeploy 线嵌入端点决策(`EndpointClient` `mode=endpoint`,dense 仍走 vLLM BGE-M3);硬契约
  (`chunk_id` 公式、写序 PG→Milvus upsert→flush→INDEXED、PG add-only、IR 契约、RRF 融合语义);v1.6 §8.2 `audit_corpus` 全字段。
- **消费者清单**:制度查询智能体(hybrid 检索 R1/R5 —— 直接受益方,dense+稀疏 RRF 不变);eval(reconcile/rebuild
  须兼容 bm25 冷备形态,T2 照跑);audit-biz `/v1/query`(检索面 corpus_type 口径不变,边界契约零改)。
- **红线**:不改 AI 内核、不改检索融合语义(RRFRanker 不变);dense **仍 BGE-M3 1024 维**(维度/空间不动);
  默认路径**零 LLM**(BM25 零模型);PG 权威 / Milvus 投影可重建;边界过滤"算在 Java、用在 Python"(检索**前**前置过滤,本切片不碰 `expr` 语义);机制替换走**配置缝**不物理删除(`sparse_backend`,bge 保留)。

## 1. 切片边界(本轮做什么 / 不做什么)

| | 范围 |
|---|---|
| **做** | ①**Milvus 2.4→2.5 升级**(`compose.yaml` image + `pymilvus` 版本约束放开到 `>=2.5,<2.6`),bge 路径回归冒烟不变;②`sparse_backend = bge\|bm25\|none` 配置位(默认 `bge`),`audit_corpus_schema()` **按 backend 分形态**;③**bm25 schema**:`text` 字段 `enable_analyzer=True`(中文 analyzer,jieba)+ 挂 `Function(FunctionType.BM25, input=["text"], output=["sparse_vec"])`;`sparse_vec` 索引 metric `IP`→`BM25`(`milvus_io.py:159-160`);④**摄取**:bm25 backend 时向量端只取 vLLM dense、**不写客户端 sparse**(Milvus function 从 `text` 算;`milvus_io.py:94` upsert 条件剔除 `sparse_vec` key);冷备只存 dense、**不存 sparse**(`corpus_rows.py` bm25 分支);⑤**查询**:`milvus_io.search` 加 `query_text` 入参,bm25 backend 时 sparse 的 `AnnSearchRequest` **传 query 原文字符串**(Milvus 分词算 BM25,`metric_type="BM25"`)、dense 仍传向量,`hybrid_search`+`RRFRanker` 融合不变(`milvus_io.py:262-276`);⑥**rebuild**:bm25 backend 从 PG `chunks.text` 重灌、Milvus 重算 BM25(sparse 免冷存);⑦**静默降级护栏**:`sparse_backend=bge` 时摄取校验 endpoint sparse 非空、空则 **fail-fast**(治现 vLLM 静默退 dense-only 隐患)。 |
| **不做** | **查询侧 `sparse_boost` 的 BM25 版重写**(发文字号提权 + `dict_scenario_terms` 扩展 → query 文本增强)——**留下切片**(已决 Q1;BM25 高 IDF 已覆盖大部分发文字号精确命中)。**自定义 analyzer 精调发文字号切词**(v0 用 jieba 内置;冒烟若发现发文字号被切碎 → Open Question / 下切片)。**bge→bm25 存量迁移**(greenfield,建新库不迁)。**换非 BGE-M3 dense / 改 dense 维**(predeploy 已否决,空间不兼容)。**Milvus `WeightedRanker` / 检索后过滤 / 改 `status`·`corpus_type`·`perm_tag` 前置过滤语义**(红线不碰)。**query-api 边界改动 / 比对智能体 / 图谱**。**bge 与 bm25 混用同一 collection**(BM25 与 BGE-M3 稀疏**不在同一空间**,一集合只一种稀疏形态)。 |

## 2. Objective(建什么 / 何为完成)

在**不改 AI 内核 / 不改检索融合语义 / dense 仍 BGE-M3(vLLM)**的前提下,让稀疏(词法)通道在内网 vLLM
only-dense 环境下由 **Milvus 2.5 原生 BM25**(从 `text` 字段 analyzer 产出)承担,替代拿不到的 BGE-M3 lexical。

完成 =(a)`sparse_backend=bm25` 下样例批次 `REGISTERED→…→INDEXED` 全通、幂等重跑零重复;(b)hybrid(dense +
BM25)RRF 召回正常、**发文字号/条款号精确命中**集成断言(相对纯 dense 有提升);(c)bm25 冷备(dense-only)+
`rebuild` 从 text 重算 BM25 命中一致;(d)**`sparse_backend=bge` + 全部既有测试在 Milvus 2.5 上仍绿(byte 等价)**;
(e)Milvus 2.4→2.5 升级冒烟通过;(f)全仓全量 + ruff 全绿、DAG 无环。

## 3. Tech Stack(增量)

- **Milvus 2.5.x** standalone(`compose.yaml` image bump;patch 版本钉死,见 §9 Q4);**pymilvus `>=2.5,<2.6`**
  (放开 `libs/common` + `pipeline` 约束);用 `Function` / `FunctionType.BM25` + `analyzer_params`(中文 jieba,**server 侧**,客户端无需装 jieba)。
- 复用 `pipeline.index`:`milvus_schema`(schema 工厂按 backend 分形态)、`milvus_io`(upsert 条件不写 sparse、
  search 加 `query_text` 分支)、`corpus_rows`(bm25 冷备只 dense)、`embedding_client`(`EndpointClient` dense-only 已容忍缺 sparse)。
- 复用 `query.retrieve.hybrid`(向 `search` 传 `query_text`;`_sparse_for` 本切片**不动**,bge 分支照旧、bm25 分支走 text)。
- 复用 `config`:加 `sparse_backend` + analyzer/bm25 参数位(**add-only**,默认 bge = byte 等价)。
- **零新第三方依赖**(pymilvus 自带 Function/analyzer);**零 LLM**;dense 仍 vLLM BGE-M3(空间不变、免重灌 dense)。
- ⚠ **`setuptools<81` 钉子复核**:该钉子为 pymilvus 2.4 需 `pkg_resources`(CLAUDE.md 环境节);pymilvus 2.5 是否仍需 → PLAN 前置验证,谨慎放开。

## 4. Commands

```bash
# 升级共享栈到 Milvus 2.5(全局单例栈,须先协调空闲;见 §7 Ask-first)
demo down -v && demo up                                  # compose 起 Milvus 2.5.x + alembic + seed

# bm25 backend 端到端(dense 走内网 vLLM endpoint,sparse 由 Milvus BM25 从 text 算)
PIPELINE_SPARSE_BACKEND=bm25 \
PIPELINE_EMBEDDING_MODE=endpoint PIPELINE_EMBEDDING_BASE_URL=<vLLM> \
  demo ingest <dir> --manifest <xlsx>
PIPELINE_SPARSE_BACKEND=bm25 demo search "证监会公告〔2023〕15号 的处罚标准"   # hybrid=BM25+dense

# bge backend 回归(Milvus 2.5 上仍绿,byte 等价)
demo verify smoke && demo verify reconcile && demo verify idempotency
.venv/bin/python -m pytest pipeline/tests/test_milvus_io.py pipeline/tests/test_s5.py \
  query/tests/test_sparse_boost_integration.py -q
.venv/bin/ruff check .
```

## 5. Project Structure(增量)

```
libs/common/common/milvus_schema.py   # audit_corpus_schema(sparse_backend): bge=现状 | bm25=text 开 analyzer + Function(BM25)→sparse_vec
libs/common/pyproject.toml             # pymilvus >=2.5,<2.6;setuptools 钉子复核
pipeline/pyproject.toml                # pymilvus >=2.5,<2.6
pipeline/pipeline/index/milvus_io.py   # 建集合(bm25 挂 Function + sparse index metric BM25);upsert 条件不写 sparse;
                                       #   search(..., query_text) bm25 分支:sparse req 传 query_text;bge 分支不变
pipeline/pipeline/index/corpus_rows.py # bm25:冷备只 dense、rebuild 从 text 重算(sparse 免冷存)
pipeline/pipeline/config.py            # + sparse_backend: str="bge" (env PIPELINE_SPARSE_BACKEND) + analyzer/bm25 参数(⚠默认)
compose.yaml                           # milvusdb/milvus:v2.5.x(patch 钉死)
pipeline/tests/
  test_milvus_bm25.py                  # 单元:schema 工厂 bm25 形态(含 Function/analyzer/BM25 metric)、search query_text 路由、
                                       #   upsert bm25 不含 sparse key、冷备 bm25 只 dense
  test_milvus_bm25_integration.py      # 真栈(Milvus 2.5 + vLLM/本地 BGE-M3 dense):bm25 端到端 + 发文字号精确命中 + rebuild 重算
  # 既有 test_milvus_io / test_s5 / test_sparse_boost_integration:bge 回归 byte 等价 on 2.5
docs/bm25-docs/SPEC-BM25.md / PLAN-BM25.md / TASKS-BM25.md / RTM-BM25.md / bm25_devlog.md
```

## 6. Code Style(接缝 / 分形态)

schema 工厂按 backend 分形态(bge = 现状 byte 等价;bm25 = 加 analyzer + Function):

```python
def audit_corpus_schema(sparse_backend: str = "bge") -> CollectionSchema:
    text = FieldSchema("text", DataType.VARCHAR, max_length=2000,
                       enable_analyzer=(sparse_backend == "bm25"),
                       analyzer_params={"type": "chinese"} if sparse_backend == "bm25" else None)
    sparse = FieldSchema("sparse_vec", DataType.SPARSE_FLOAT_VECTOR)   # bge:自写;bm25:Function 产出
    schema = CollectionSchema([...dense_vec, sparse, ...text...], description="audit_corpus")
    if sparse_backend == "bm25":
        schema.add_function(Function(name="text_bm25", function_type=FunctionType.BM25,
                                     input_field_names=["text"], output_field_names=["sparse_vec"]))
    return schema
```

`milvus_io.search` bm25 分支(**只加分支,bge 路径 byte 等价**):

```python
if self._sparse_backend == "bm25":
    sparse_req = AnnSearchRequest([query_text], "sparse_vec",     # 原文字符串,Milvus 分词算 BM25
                                  {"metric_type": "BM25"}, limit=topk, expr=expr)
else:  # bge:现状,传稀疏向量
    sparse_req = AnnSearchRequest([_sparse_for_milvus(sparse)], "sparse_vec",
                                  {"metric_type": "IP", "params": {}}, limit=topk, expr=expr)
res = col.hybrid_search([dense_req, sparse_req], RRFRanker(), limit=topk, output_fields=out_fields, ...)
```

> 融合层(`hybrid_search` + `RRFRanker`)对"稀疏怎么来的"无关 —— bm25 只换稀疏**生产者**,RRF 语义零改。

## 7. Boundaries

- **Always**:`sparse_backend` **默认 `bge` → byte 等价**(既有本地行为 + 全部既有测试不回归);**RRFRanker 融合语义零改**;**dense 仍 BGE-M3 1024 维**(免重灌 dense);config **add-only**、env 覆盖(`PIPELINE_SPARSE_BACKEND`);bge 路径在 Milvus 2.5 上冒烟不回归;写序 `PG→upsert→flush→INDEXED` 不变、staging 永不可见。
- **Ask first**:**升级共享 Milvus 栈到 2.5** —— 栈是**全局单例**(CLAUDE.md,多 worktree 共用一 compose/DB/collection),升级前须协调对方空闲 + `demo down -v && demo up` 取干净栈,**绝不并发跑集成**;**pymilvus 主版本 bump** 波及全仓 import → **合并前全仓模型门跑一次**;**analyzer 选型**(jieba 内置 vs 自定义发文字号 filter)—— 若 bm25 集成冒烟发现发文字号(`证监会公告〔2023〕15号`)被切碎、精确命中不达标,升级为自定义 analyzer 属**范围变更**(SPEC/RTM 同步);**`setuptools<81` 钉子放开**须先复核 pymilvus 2.5 不再需 `pkg_resources`。
- **Never**:边界传用户身份/JWT;**检索后过滤**;改 `status`/`corpus_type`/`perm_tag` 前置过滤语义(bm25 分支不碰 `expr`);换 dense 模型/维;`WeightedRanker`;**bge 与 bm25 稀疏混一个 collection**(空间不同);联网下载。

## 8. Success Criteria(可测)

1. `sparse_backend=bm25` schema 工厂:`text` 开 analyzer(中文)+ 挂 `Function(BM25)`→`sparse_vec`、sparse 索引 `metric_type=BM25`(单元断言);`bge` 形态与现状**逐字段一致**(byte 等价守护)。
2. bm25 摄取:`upsert` payload **不含** `sparse_vec` key(Milvus function 产出),冷备**只 dense**(单元断言 `sparse_vec_cold=None` 路径);端到端 `REGISTERED→…→INDEXED` 全通、幂等重跑零重复(集成)。
3. bm25 查询:`search(query_text=...)` 走 BM25 sparse req + dense req + `RRFRanker`;**发文字号/条款号精确命中**较纯 dense 提升(集成构造可判别断言,复用 `test_milvus_search_text` 范式)。
4. bm25 `rebuild`:从 PG `chunks.text` 重灌、Milvus 重算 BM25,召回与首灌一致(集成)。
5. **bge backend + 既有测试在 Milvus 2.5 上全绿**(`test_milvus_io`/`test_s5`/`test_sparse_boost_integration` byte 等价);`demo verify smoke/reconcile/idempotency` 通过。
6. **静默降级护栏**:`sparse_backend=bge` + endpoint 空 sparse → 摄取 **fail-fast**(单元 monkeypatch endpoint 返空 → 抛;不再静默退 dense-only)。
7. Milvus 2.4→2.5 升级冒烟:`demo down -v && demo up` 起 2.5.x、collection/index 建成、既有 bge 数据路径通。
8. `config.sparse_backend` add-only、默认 `bge`;`pymilvus>=2.5,<2.6` 全仓 import 无破、`alembic check` 无漂移(schema 未改 PG)。
9. 全仓全量 + ruff 全绿;**DAG 无环**(`query→pipeline→common`,不新增跨包依赖)。

## 9. Open Questions(默认待 gate)

| # | 事项 | 处置(默认 / 待定) |
|---|---|---|
| **目标** | A1 vs A2 | ✅ **A1**(Milvus 2.5 原生 BM25);内网 Docker 19.03 上 2.5 官方确认可行。 |
| **范围** | boost 是否本切片 | ✅ **核心 BM25 先行**,`sparse_boost` BM25 版留下切片。 |
| **文本源** | BM25 输入字段 | ✅ **复用 `text`**(截断 2000;条款级块基本 <2000,长块轻微损失,文档化)。 |
| Q1 | 中文 analyzer 选型 | 默认 **jieba 内置**(`analyzer_params={"type":"chinese"}`);发文字号切词达标性 **bm25 集成冒烟验证**;不达标 → 自定义 analyzer(Ask-first 范围变更)。 |
| Q2 | BM25 参数 `k1`/`b` | 默认 Milvus 缺省(`k1=1.2`/`b=0.75`)⚠;V0 标定占位,不对甲方承诺。 |
| Q3 | rebuild 冷备契约 delta | bm25 backend **sparse 不冷存**(Milvus 从 text 重算),与 v1.6 §491"dense/sparse 双冷存零重编码"不同 → **写进 SPEC/devlog 契约 delta**;dense 仍冷存,rebuild 仍免重编码 dense。 |
| Q4 | Milvus 2.5 patch 版本 | 钉死具体 patch(如 `v2.5.x`)⚠;内网镜像准入需拉离线镜像;PLAN 定版本。 |
| Q5 | 内网自查前置 | compose ≥1.25.1 + CPU AVX2(Milvus SIMD 要求)——PLAN 前内网自查(不阻塞 SPEC/本地开发,本地已满足)。 |

## 10. 关系(与既有契约 / 切片)

- **CP-005-②(embedding 网关双输出)**:本 SPEC 是其"sparse 双输出前置验证被 vLLM 证伪"后的**收口**——放弃"要求网关吐 sparse",改由 **Milvus 侧 BM25** 承担词法通道;dense 仍走网关(vLLM)BGE-M3。
- **SPEC-SPARSE(§5.4)**:其**查询层 token 提权**机制绑 BGE-M3 lexical token 空间;bm25 backend 下失效 → **BM25 版 query 文本增强**(发文字号重复加权 + `dict_scenario_terms` 同义词追加)**留下切片**。bge backend 下 §5.4 机制**不变**。
- **v1.6 §8.2 / §491**:`audit_corpus` 字段全集不减(bm25 是 `sparse_vec` 的**产出方**变化,非字段增删);rebuild 冷备契约对 bm25 backend 有 delta(Q3)。
- **predeploy 线**:承接 `EndpointClient`(dense-only 容忍已在);本切片补齐"内网 vLLM only-dense 下仍有词法通道"。
- **边界契约(CP-011)**:检索面 corpus_type/前置过滤口径零改,`/v1/query` 消费不变(稀疏生产者变化对边界透明)。

## 11. 验证清单(进 Phase 2 / PLAN 前)

- [x] 六大块齐全 · [x] 成功标准可测 · [x] 边界三档 · [x] spec 落盘 · [x] A1 分水岭(Docker 19.03 上 Milvus 2.5)官方文档确认
- [ ] **人工复核批准**(尤其:§1 边界"核心先行 / 复用 text"、§7 Ask-first **升级共享 Milvus 2.5 需协调全局栈** + pymilvus bump 全仓门 + `setuptools` 钉子、§8 SC5 bge byte 等价 + SC6 静默降级护栏、§9 Q3 rebuild 冷备契约 delta、Q5 内网自查前置)
