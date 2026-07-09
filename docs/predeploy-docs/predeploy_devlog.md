# 甲方内网预部署对齐 · 开发记忆(决策/否决/非显然约束)

> 本工作树 `audit-ai-predeploy`(分支 `feat/predeploy`,由 `audit-ai-preseg`/`feat/preseg` 改名而来)
> 用于把**甲方内网环境要求**逐项对齐。preseg(CP-010 预切块适配,T1–T12)作为本分支基座继续叠。
> 只记 git 给不了的——决策 + 为什么(尤其否决方案)、非显然契约约束。

## 2026-07-09 模型端点远程化(嵌入 + 重排)

### 需求澄清(关键:纠正甲方误解)

- 甲方最初要求"向量化 + 重排模型也适配 **OpenAI 接口**"。核查后定性为**甲方误解**:
  OpenAI 标准 `/v1/embeddings` **仅 dense**、且 **无 rerank 端点**。若照做 →
  **丢掉 sparse 通道 → 混合检索退化为纯语义**,对制度/法规语料(条款号/机构名/术语的精确命中)
  是实打实的质量损失。
- 定案:两个端点按 **BGE 系常用 API 标准**远程化,**保留原有混合检索(dense+sparse)不降级**。
  端点背后跑的就是 BGE 系模型(BGE-M3 出 dense+sparse、BGE-reranker 出相关性)。

### 否决的方案(为什么没走)

- ❌ **OpenAI dense-only 嵌入**:丢 sparse,破混合检索。且需同改**写入**(空 sparse)与**检索**
  (跳 sparse 通道)两侧,变更面反而更大(见 `milvus_io` schema 有 `SPARSE_FLOAT_VECTOR`)。
- ❌ **LLM-as-reranker**(用 `/chat/completions` 做 listwise 打分):慢/贵/稳定性差;既然有
  BGE-reranker 网关,直接走 Jina/Cohere 风 `/rerank` 更对。
- ❌ **换非 BGE-M3 的嵌入模型**(如 text-embedding-3):维度不同(1536≠1024)、与**已入库
  BGE-M3 向量不在同一空间** → 须全量重灌 + 改 Milvus dense 维。故端点**必须托管 BGE-M3 本体**。

### 设计(接缝早已留好,本次填实 + 加后端)

- **嵌入** `pipeline/pipeline/index/embedding_client.py::EndpointClient`(`mode=endpoint`):
  `POST {base_url}{endpoint_path}` `{model,input,return_dense,return_sparse}` → 逐条取
  `endpoint_dense_field` / `endpoint_sparse_field`,**sparse 归一为 `{token_id(str):权重}`**
  (兼容 dict 与 `[{index,value}]` TEI 风)→ 映射现有 `Embedding` → 下游 milvus_io / 混合查 / 冷备
  **零改**。字段/路径**可配**以适配 TEI/Xinference/vLLM。构造期 fail-fast(缺 base_url 即抛)。
- **重排** `query/query/rerank/reranker.py::APIReranker`(`rerank_backend="api"`):
  Jina/Cohere 风 `POST {base_url}/rerank` → 按 `relevance_score` 降序;`top_n` 截断时**缺项候选
  补回原序、不丢**(与 `bge` 全量重排语义一致)。
- **配置**(add-only,`local`/`none`/`bge` 默认路径行为零变化):
  - `[embedding]` `endpoint_path/model/dense_field/sparse_field/timeout`;env
    `PIPELINE_EMBEDDING_BASE_URL`(**优先于 OPENAI_BASE_URL**)/ `PIPELINE_EMBEDDING_API_KEY` /
    `PIPELINE_EMBEDDING_ENDPOINT_MODEL`。
  - `[query]` `rerank_endpoint_base_url/api_key/path/top_n` + `rerank_backend` 扩 `api`;env
    `QUERY_RERANK_BASE_URL` / `QUERY_RERANK_API_KEY` / `QUERY_RERANK_PATH`。

### 非显然约束 / 钉子(后来者必读)

- **嵌入端点 base_url 与 LLM base_url 解耦**:内网嵌入服务常独立部署,故新增专用
  `PIPELINE_EMBEDDING_BASE_URL`,优先于共享的 `OPENAI_BASE_URL`(后者仍是回落 + LLM 用)。
- **sparse 必须 token_id→权重**:这是与既有已索引 BGE-M3 向量同空间的前提;任何正确的 BGE-M3
  服务返回的 token id 都在固定 XLM-RoBERTa 词表内,故 drop-in 兼容。若服务返回别的稀疏表示 → 不可用。
- **摄取(pipeline)与查询(query)共用 `EmbeddingClient.from_config`** → 同一 `mode` 保证两侧
  同空间。切 endpoint 时**两侧一起切**,勿一侧 local 一侧 endpoint(除非确认远程 BGE-M3 与本地逐位一致)。
- **混合查已有 sparse 空 → dense-only 兜底 + 标记**(`milvus_io.py`),故端点若配错 sparse 字段
  不会崩、但会静默退化;部署联调须验证 sparse 非空(见验收)。
- **切端点无需重灌的前提**:端点托管的是 **BGE-M3 本体**(1024 维、归一 dense、同 sparse 词表)。
  换模型即须全量重新嵌入(`demo rebuild` 不行,rebuild 是零重编码回灌旧向量)。

### 验收(联调真网关时)

- `PIPELINE_EMBEDDING_MODE=endpoint` + `PIPELINE_EMBEDDING_BASE_URL=...` 起 `demo search`,
  确认召回正常、**sparse 非空**(混合查未走 dense-only 兜底路径)。
- `QUERY_RERANK_BACKEND=api` + `QUERY_RERANK_BASE_URL=...` 确认重排改变序且候选数不减。
- 单元(零网络,已绿):`test_embedding_endpoint_client.py`、`test_reranker_api.py`。

### 遗留

- 真网关 wire 细节按具体框架(TEI 分 `/embed`+`/embed_sparse` 双端点 / Xinference / vLLM)可能需
  微调字段映射——当前**通用契约 + 字段可配**,拿到真端点文档后锁默认值即可。
- 端点侧超时/重试/限流的生产级退避策略(当前复用既有指数退避)待真栈压测标定。

## 2026-07-09 生产构建剔除人操作面入口(demo/demo-web,方案 A)

audit-ai 生产 = Java 之后无身份·无状态端点,不需人操作的 CLI/Web 工作台。**方案 A**:代码保留供
dev/运维,仅**生产构建剔除入口**(减小人操作面/攻击面),不做重构删除(方案 B 前置=先定人工队列/
元数据确认/验证在生产由谁驱动,未定)。

- **机制**:`pipeline/pyproject.toml` 声明 `dynamic = ["scripts"]`(移除静态 `[project.scripts]`)+
  `pipeline/setup.py` 按 env 条件注入 console_scripts:默认(dev)注册 `demo`/`demo-web`;
  `PIPELINE_BUILD_PROFILE=production`(构建/安装期设)→ **不注册任何 console_scripts**。
- **验证**(不碰共享 venv):`pip wheel ./pipeline --no-deps --no-build-isolation` 建两份 wheel 查
  `entry_points.txt`——dev 含 demo/demo-web,production 无 `entry_points.txt`。
- **生产 ops 兜底**:CLI/Web 代码在;确需临时运维仍可 `python -m pipeline.cli` / `python -m pipeline.web.app`
  (二者 `__main__` 已在)。
- **构建命令**:`PIPELINE_BUILD_PROFILE=production pip wheel pipeline/ --no-deps`(生产 wheel,无入口);
  `pip install -e pipeline`(dev,含入口)。
- **非显然**:改动只影响未来构建/安装;**现有共享 venv 的 `demo` 不受影响**(旧元数据仍在),
  勿在共享 venv 重装做验证(会改全体 worktree 的入口)。
- **叶子面前提**:核心(编排/stage/域函数)零反向依赖 cli/web(仅测试 + 入口引用),故剔除入口不伤核心;
  但 `cli.py` 内嵌驱动/收尾逻辑(`_drive_batch`/`_finalize_*`/`_approve_doc`/`reprocess_to_indexed`)
  未下沉域模块 → 方案 B 真删前须先抽取。

## 2026-07-09 字典精简:裁 issuers/departments/violation_types(甲方预结构化冗余)

甲方知识库(《法规制度平台表头整理.xlsx》)已把 `业务类别/适用对象/发文单位/发文部门/法律位阶/案例类型`
预结构化 → 我方原为"打标约束空间"的字典大面积落空。逐字典评估(见对话)后裁 3 留 2 待定 2:

- **保留**:`dict_biz_domains` / `dict_entity_types`——查询侧真正的**标量过滤维度**(R4
  `_ALLOWED_EXPR_FIELDS = {chunk_type, biz_domain, entity_type, perm_tag}`);值本就从 manifest 流到
  chunk(不经字典),字典只作查询词表。`dict_aliases` 保留(口语简称→canonical,与甲方数据无关)。
- **裁**:`dict_issuers` / `dict_departments` / `dict_violation_types`。
- **待定**:`dict_aliases`(留了)/ `dict_scenario_terms`(独立查询增强,默认关,未动)。

### 为什么可裁(关键判据)

- **富集默认全关**(E2/L2/case_l2)→ 字典的"打标白名单"职责本就休眠;preseg 结构化案例还**绕过 case_l2**。
- **issuer_level 空转**:`corpus_rows._issuer_level` 从 `dict_issuers` 派生 chunk.issuer_level,但
  **查询侧零消费**(R4 不过滤 issuer_level,同 perm_tag"写而不用")→ 整条 dict_issuers→issuer_level 链
  空转,裁掉只让 issuer_level 恒 0,功能零影响。真做分层检索时 issuer_level 应取自甲方**法律位阶**
  (`issuer_level_src`→INT8,preseg 遗留),非我方 3 档 demo 字典。

### 改动(add-only:停 seed + 去派生,**不删表/列**)

- `pg_io.seed_dicts`:只 seed biz_domains/entity_types/aliases;删 issuers/departments/violation_types。
- `corpus_rows`:删 `_issuer_level`/`_ISSUER_LEVEL_RANK`,`issuer_level = 0`(前瞻字段注释)。
- 删 3 个 seed CSV;`tools/doc_test/bootstrap_dicts.py` 不再自举 issuers/violation(否则重建已删 CSV)。
- 测试:`test_pg_io`/`test_seeds_p0` 改断言;`test_e2_tag` **自带最小 dept seed**(自包含,不靠全局 seed)。
  真栈跑通 47 passed(含 E2 部门打标集成)。
- **消费方(E2 部门 / case_l2 违规打标)代码保留**——默认关、无值即降级;**未retire 打标能力**(如需
  彻底退役是更大改)。dict 表 + `get_issuers`/`get_departments`/`get_violation_types` 访问器 add-only 留存(休眠)。
