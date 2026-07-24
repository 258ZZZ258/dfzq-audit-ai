# Devlog: Milvus 原生 BM25 稀疏通道(CP-012)

> 决策 + 为什么(尤其否决方案)+ 非显然踩坑 + 契约约束。机械 what/when 看 git log(commit 前缀 `bm25:`)。
> 实现 2026-07-24,分支 `feat/bm25-sparse`;Milvus 2.5.27 + pymilvus 2.5.18。

## 触发与目标决策(A1)

- **触发**:内网 BGE-M3 部署在 **vLLM** 上,嵌入端点只吐 dense、不吐 sparse(lexical_weights)——CP-005-②
  "endpoint 须暴露 dense+sparse 双输出"前置验证**被证伪**。现状后果:`EndpointClient` 拿不到 sparse →
  每 chunk 空 sparse → 每次查询静默退 `milvus_io` 的 dense-only 兜底,法规域丢发文字号/条款号精确命中。
- **目标 = A1**(升 Milvus 2.5 用原生 BM25 从 text analyzer 产稀疏,替代 BGE-M3 lexical;dense 仍 vLLM BGE-M3)。
  - **否决 A2**(客户端 BM25 自管 IDF):greenfield 下升级零数据迁移,A2 的 IDF 冻结 + 摄取/查询两侧模型同步纯负担无对冲。
  - **否决 B**(换能吐 sparse 的 serving,如 TEI/Xinference sidecar):**vLLM-only 是甲方硬约束**,不能再起服务。
  - **要保的是"稀疏通道",不是 BGE-M3 sparse 本身**:融合层(`hybrid_search`+`RRFRanker`)对稀疏怎么来的无关,
    BM25 的 term→weight 稀疏向量能塞进同一 `sparse_vec` 字段。
- **分水岭已核实**:内网锁定 **Docker 19.03 x86 Linux**;Milvus 2.5.x 官方最低支持 **Docker 19.03+ / Compose
  1.25.1+**(`milvus.io/docs/v2.5.x/prerequisite-docker.md`)→ A1 在内网可行。

## realized 设计(sparse_backend 配置缝,默认 bge byte 等价)

- `[embedding] sparse_backend = bge|bm25|none`(默认 **bge = 现状**,add-only,env `PIPELINE_SPARSE_BACKEND`)。
  bge 路径全程 **byte 等价**;bm25 opt-in 给内网。schema/upsert/search/冷备各按 backend 分形态,**融合层零改**。
- **schema 工厂** `audit_corpus_schema(sparse_backend, analyzer_type)`:bm25 时 `text` 开 `enable_analyzer`(jieba
  中文)+ `schema.add_function(Function(BM25) text→sparse_vec)`;字段全集不变(§8.2)。
- **摄取**:bm25 时 `_to_milvus_dict` **剔除 `sparse_vec` key**(function 输出字段,误传 Milvus 拒插);dense 仍 vLLM。
- **查询**:`search(query_text)` bm25 分支 sparse `AnnSearchRequest` 传 **query 原文字符串**(Milvus 分词算 BM25,
  metric=`BM25`);hybrid.py 三检索路传 `query_text=query`;缺 query_text/none → dense_only 兜底(不静默,标 mode)。
- **冷备/rebuild**(契约 delta,见下):bm25 `sparse_vec_cold=None`(免冷存),rebuild 从 text 重算。
- **静默降级护栏**:`s5.guard_sparse_backend`——bge 后端却拿空 sparse(非空文本块)→ fail-fast(治现 vLLM 隐患)。

## 契约 delta(须知)

- **rebuild 冷备**(vs v1.6 §491 "dense/sparse 双冷存零重编码"):bm25/none 后端 **sparse 不冷存**
  (`sparse_vec_cold=None`),`reloadable_chunks`/`rows_from_cold*` 按 backend 只校验 dense;rebuild 从 `chunks.text`
  重灌、Milvus BM25 function 重算稀疏。**dense 仍冷存**,dense 侧 rebuild 仍零重编码。cold 函数加 `sparse_backend="bge"`
  默认参(bge 调用方 byte 等价),6 调用点(s5 index/finalize/cli activate/reconcile×2/rebuild)传实际 backend。
- **BM25 sparse 索引 metric = `BM25`**(非 bge 的 `IP`)+ `bm25_k1`/`bm25_b`(config,⚠ V0 默认 1.2/0.75)。

## 非显然踩坑(pymilvus 2.5 / Milvus BM25)

- **pymilvus 2.5 内省 API**:`schema.functions`(list)、`fn.type == FunctionType.BM25`(fn.type 是 int)、
  `fn.input_field_names`/`output_field_names`;field `enable_analyzer` 落 `field.params["enable_analyzer"]`,
  `analyzer_params` 存为 **JSON 字符串**(`'{"type":"chinese"}'`)。写测试前用小脚本探真、别猜。
- **function 输出字段不可 insert**:BM25 的 `sparse_vec` 由 function 从 text 产,摄取端**必须不传该字段**,否则 Milvus 拒插。
- **query 传原文**:bm25 检索 `AnnSearchRequest.data = [query_text]`(原始字符串,Milvus 侧分词),非稀疏向量。
- **jieba analyzer 对发文字号**:`证监会公告〔2023〕15号` 精确匹配 rank#1 达标(**查询与入库同分词**,即便 jieba 切 〔〕
  方式一致即匹配);**跨全/半角归一属查询侧 sparse_boost 增强**(留下切片),非 analyzer 职责。
- **search 的 `except Exception` 会吞 AttributeError**:白盒测试 `MilvusIO.__new__` 绕过 `__init__` → `self.sparse_backend`
  缺属性 → 在 try 内抛 → 被兜底吞成 dense_only(hybrid 断言假失败)。修:白盒 fixture 补 `m.sparse_backend="bge"` 镜像 __init__。
- **setuptools 钉子**:pymilvus 2.5 import 期已不拉 `pkg_resources`(验),但仍声明 `setuptools>69` → 保守留 `<81`。

## 留下切片 / 待办

- **sparse_boost 的 BM25 版**(发文字号提权 + `dict_scenario_terms` 词典扩展):bge 版绑 BGE-M3 token 空间,bm25 下失效;
  BM25 版 = **query 文本增强**(发文字号重复加权 + 法言同义词追加进 query 串)。核心先行,此项**留下切片**——
  BM25 高 IDF 已覆盖大部分发文字号精确命中(T8 已验 rank#1),扩展面(口语→法言术语断层)才需它。
- **内网自查(不阻塞本地)**:`docker-compose ≥1.25.1`(19.03 配 Compose V1,别升 V2 插件)+ CPU `AVX2`(Milvus SIMD)
  + Milvus `v2.5.27` 离线镜像准入。切内网 vLLM endpoint 时设 `PIPELINE_SPARSE_BACKEND=bm25` + `PIPELINE_EMBEDDING_MODE=endpoint`。
