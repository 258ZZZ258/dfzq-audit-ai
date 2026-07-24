# RTM: Milvus 原生 BM25 稀疏通道 需求可追溯矩阵(CP-012)

> SPEC-BM25 §8 九条 Success Criteria → 决策 → 任务 → 测试的映射。全绿 = 可交合并门。
> 状态图例:⏳ 计划(未实现)/ 🟡 进行中 / ✅ 通过。**本表在实现前建立,初始全 ⏳**。

| # | 验收标准(SPEC §8) | 决策依据 | 任务 | 测试(证据) | 状态 |
|---|---|---|---|---|---|
| 1 | schema 工厂:bm25 形态含 analyzer+Function(BM25)+sparse metric BM25;bge 形态逐字段对拍现状 | A1 / 复用 text | T3 | `test_schema_bm25`(bge 逐字段对拍;bm25 含 `schema.functions` + text `enable_analyzer` + sparse `SPARSE_FLOAT_VECTOR`) | ⏳ |
| 2 | bm25 upsert 不写 sparse(Milvus 产)+ 冷备只 dense + 端到端 INDEXED | A1 | T4/T7/T8 | `test_milvus_io_bm25`(payload 无 sparse key、query 得非空稀疏);`test_e2e_bm25`(端到端 INDEXED+幂等零漂移) | ⏳ |
| 3 | bm25 查询 `search(query_text)` 走 BM25+dense RRF;发文字号命中优于纯 dense | 核心先行 | T5/T8 | `test_milvus_io_bm25::test_search_bm25`(hybrid 非 dense_only);`test_e2e_bm25`(发文字号命中 > dense_only) | ⏳ |
| 4 | rebuild 从 PG text 重算 BM25,召回与首灌一致 | rebuild 契约 delta(Q3) | T7 | `test_rebuild_bm25`(rebuild 后发文字号同命中;`sparse_vec_cold=None`) | ⏳ |
| 5 | **bge byte 等价 on Milvus 2.5**:既有全量 + verify 三件套绿;config/schema bge 形态对拍零变更 | 默认 bge / B8 兑现 | T1/T2/T3 | `test_config_bm25`(默认 bge)+ `test_schema_bm25`(bge 对拍)+ 既有 `test_milvus_io`/`test_s5`/`test_sparse_boost_integration` on 2.5 + `verify smoke/reconcile/idempotency` | ⏳ |
| 6 | 静默降级护栏:bge + endpoint 空 sparse → fail-fast(不再静默退 dense-only) | 治现隐患 | T6 | `test_sparse_guard_bm25`(monkeypatch 空 sparse+bge→抛;bm25 不抛) | ⏳ |
| 7 | Milvus 2.4→2.5 升级冒烟:2.5.x 起、collection/index 建成、bge 数据路径通 | A1 分水岭(Docker 19.03 官方确认) | T1 | 升级门:`demo down -v && demo up` on 2.5.x + bge 全量绿 | ⏳ |
| 8 | config `sparse_backend` add-only 默认 bge;pymilvus 2.5 全仓 import 无破;alembic check 零漂移 | 配置缝 / PG 零改 | T1/T2/T9 | `test_config_bm25`;全仓 import 绿;`alembic check` 零漂移(合并门) | ⏳ |
| 9 | 全仓模型门全量 + ruff 全绿;DAG 无环(query→pipeline→common) | — | 合并门 | T9:`pytest -q`(干净 2.5 栈 + BGE-M3)+ `ruff check .` | ⏳ 合并门 |

## Open Questions 追踪(SPEC §9)

| # | 事项 | 落点任务 | 处置状态 |
|---|---|---|---|
| Q1 | jieba analyzer 发文字号切词达标性 | T8(命中即达标)| ⏳ 待集成验证;不达标→自定义 analyzer(Ask-first) |
| Q2 | BM25 `k1`/`b` 参数 | T2(config 占位)/T4(建索引) | ⏳ 默认 Milvus 缺省,V0 标定 |
| Q3 | rebuild 冷备契约 delta(bm25 sparse 不冷存) | T7 实现 / T9 文档化 | ⏳ 写入 devlog |
| Q4 | Milvus 2.5 patch 版本钉死 + 内网离线镜像 | T1 | ⏳ PLAN 定版本 |
| Q5 | 内网自查(compose≥1.25.1 + AVX2) | T9 清单 / 内网并行 | ⏳ 不阻塞本地开发 |

## 偏差记录(实施期回填)

| 偏差 | 原因 |
|---|---|
| _(实施期按 commit/devlog 回填)_ | — |
