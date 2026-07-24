# RTM: Milvus 原生 BM25 稀疏通道 需求可追溯矩阵(CP-012)

> SPEC-BM25 §8 九条 Success Criteria → 决策 → 任务 → 测试的映射。全绿 = 可交合并门。
> 状态图例:⏳ 计划 / 🟡 进行中 / ✅ 通过。实现完成 2026-07-24(T1–T9;Milvus 2.5.27 + pymilvus 2.5.18)。

| # | 验收标准(SPEC §8) | 决策依据 | 任务 | 测试(证据) | 状态 |
|---|---|---|---|---|---|
| 1 | schema 工厂:bm25 含 analyzer+Function(BM25)+sparse metric BM25;bge 逐字段对拍现状 | A1 / 复用 text | T3 | `test_schema_bm25`(6):bge 逐字段对拍 + 无 function / bm25 含 `schema.functions` + text `enable_analyzer` / analyzer_type 可配 | ✅ |
| 2 | bm25 upsert 不写 sparse(Milvus 产)+ 冷备只 dense + 端到端 INDEXED | A1 | T4/T7/T8 | `test_milvus_io_bm25`(upsert payload 无 sparse key、真栈 count==2);`test_e2e_bm25`(INDEXED + `sparse_vec_cold=None`) | ✅ |
| 3 | bm25 查询 `search(query_text)` 走 BM25+dense RRF;发文字号命中优于纯 dense | 核心先行 | T5/T8 | `test_milvus_io_bm25::test_search_bm25`(hybrid 非 dense_only、ids[0]==c1);`test_e2e_bm25`(发文字号 rank#1、hybrid_rank≤dense_rank) | ✅ |
| 4 | rebuild 从 PG text 重算 BM25,召回与首灌一致 | rebuild 契约 delta(Q3) | T7 | `test_milvus_io_bm25::test_bm25_rebuild_recomputes_from_text`;`test_cold_backend_bm25`(reloadable/`_cold_sparse` 分形态);`test_e2e_bm25` reindex 幂等 | ✅ |
| 5 | **bge byte 等价 on Milvus 2.5**:既有全量 + config/schema bge 形态零变更 | 默认 bge / B8 兑现 | T1/T2/T3 | `test_config_bm25`(默认 bge)+ `test_schema_bm25`(bge 对拍)+ 既有 `test_milvus_io`(11)/`test_s5`(5)/`test_milvus_search_*`(8)/reconcile·rebuild·idempotency(15)on 2.5 全绿 | ✅ |
| 6 | 静默降级护栏:bge + endpoint 空 sparse → fail-fast(不再静默退 dense-only) | 治现隐患 | T6 | `test_sparse_guard_bm25`(6):bge 空 sparse 抛 / bm25·none 不抛 / 空白文本不误杀 | ✅ |
| 7 | Milvus 2.4→2.5 升级冒烟:2.5.x 起、collection/index 建成、bge 数据路径通 | A1 分水岭(Docker 19.03 官方确认) | T1 | `demo down -v && demo up` on v2.5.27 + alembic 迁 0015 + create_collection(HNSW+SPARSE_INVERTED)成功 | ✅ |
| 8 | config `sparse_backend` add-only 默认 bge;pymilvus 2.5 全仓 import 无破;alembic check 零漂移 | 配置缝 / PG 零改 | T1/T2/T9 | `test_config_bm25`;pymilvus 2.5.18 全仓 import OK;`alembic check` = No new upgrade operations | ✅ |
| 9 | 全仓模型门全量 + ruff 全绿;DAG 无环(query→pipeline→common) | — | 合并门 | `pytest -q`(干净 2.5 栈 + 真 BGE-M3)= **1145 passed / 0 failed / 12 skipped**;`ruff check .` 全绿 | ✅ |

## Open Questions 追踪(SPEC §9)

| # | 事项 | 落点 | 处置状态 |
|---|---|---|---|
| Q1 | jieba analyzer 发文字号切词达标性 | T8 | ✅ `test_e2e_bm25`:`证监会公告〔2023〕15号` 精确匹配 rank#1(查询与入库同分词);跨全/半角归一属 sparse_boost(留下切片) |
| Q2 | BM25 `k1`/`b` 参数 | T2/T4 | ✅ 默认 Milvus 缺省(1.2/0.75),config 可调(⚠ V0 标定) |
| Q3 | rebuild 冷备契约 delta(bm25 sparse 不冷存) | T7 | ✅ 实现 + devlog 文档化:bm25/none `sparse_vec_cold=None`,rebuild 从 text 重算;dense 仍冷存 |
| Q4 | Milvus 2.5 patch 版本钉死 | T1 | ✅ `v2.5.27`(compose 钉死);内网离线镜像准入待内网 |
| Q5 | 内网自查(compose≥1.25.1 + AVX2) | T9 devlog | ⏳ 内网并行(不阻塞本地开发,本地 Docker 29.5.3 满足) |

## 偏差记录(实施期)

| 偏差 | 原因 |
|---|---|
| Milvus 2.5.27 / pymilvus 2.5.18(SPEC 写 2.5.x)| pip 解析 <2.6 得 2.5.18(> SPEC 引 2.5.15,PyPI 数据滞后);server 取最新稳定 2.5.27。均 2.5 线,BM25 就位 |
| `setuptools<81` 钉子保留(未放开)| pymilvus 2.5 import 期已不拉 `pkg_resources`(验),但仍声明 `setuptools>69` 运行期依赖 → 保守留 `<81` + 更新注释 |
| 白盒 fixture(search_text/search_expr)补 `sparse_backend="bge"` | `__new__` 绕过 `__init__` → search 新引用 `self.sparse_backend` 缺属性;镜像 __init__ 补设 |
