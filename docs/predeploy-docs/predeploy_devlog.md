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

## 2026-07-09 全拆自建反推通道 + LLM 富集(preseg-only)

甲方预结构化 → "爬取→LLM 反推→打标"自建通道弃用,生产走 preseg-only。用户决策"全拆(连富集也移除)"。

- **删**:3 模块 `enrich/e2_tag` · `meta/l2_llm` · `meta/case_l2`;3 反推工具 `tools/doc_test/{classify,
  gen_manifest,bootstrap_dicts}`;4 测试(test_e2_tag/l2_llm/case_l2 + query/test_cited_regulations_bridge_contract)。
- **改**:`s4_meta` 变纯规则(去 `_safe_biz_l2`/`case_l2.apply`,留 l1_rules/case_extract);`cli._structuring`
  去 E2 wiring + `_safe_e2`;`config` 去 e2/l2/case_l2 toggles + `[llm]` 段 + `LlmConfig`(query 用自己的
  llm 配置);`profiles` 去 `sampling_rate`,`case_ref_source` 默认改 `rule`(非 structured=规则抽取)。
- **保留**:E1 义务(零 LLM 正则)· preseg cases_ingest(结构化案例)· `llm_client.py`(**query gateway 仍用,
  不删**)· `case_ref_align`(纯逻辑)· 查询侧全部 LLM 节点(R1/N0/N1/N3/R5)。
- **踩坑钉子(会再踩)**:`PgRegLookup`(零 LLM 的 PG 外规查询工具,`RegLookup` 协议实现)原在 case_l2,
  但 **preseg `cases_ingest.extract_case_structured` 函数内懒导入它**——顶层 import 扫描漏掉,删 case_l2 后
  preseg_e2e 才炸(`ModuleNotFoundError`)。→ 移到新 `meta/reg_lookup.py`(纯 PG,与 case_ref_align 协议同伴)。
  **教训:删模块前 grep 要带函数内懒导入(`grep 'import.*<mod>'` 全仓,非仅顶层)。**
- **验证**:`--collect-only` 全仓 1006 tests / 0 收集错;真栈 150+ passed(config/s4_meta/preseg_e2e/
  orchestrator/e1/web/cases_ingest…);ruff 绿。全仓模型门留合并前。

## 2026-07-10 Codex(PR47 全量差异)review 修复 + 预切块生产摄取入口

Codex 按 `origin/main...HEAD` 全量审计出 2 Required(F1/F2)+ 1「需确认验收合同」(F3)+ 文档漂移。逐项处置:

- **F1 隔离件被永久判重**(`s0_register._find_by_source_key`):源幂等查重原不排除死态,首次因密级缺失/
  blocks 违约进 `QUARANTINED` 后,补密级(同 `source_doc_id+content_hash`,内容不变)重提在校验前就返
  `DUPLICATE`,修正永进不了管线。→ 加 `_DEDUP_DEAD_STATES`(QUARANTINED/PARSE_FAILED/REJECTED)排除;
  死态重提走 `_latest_by_source_id`→`REVISE_REPLACE` 替代旧死版、继承 logical。回归测
  `test_quarantined_same_hash_repair_reingests`(连真 PG)。
- **F2 MinIO `exists()` 吞非 404**(`object_store.py`):`except Exception: return False` 把鉴权/桶不存在/
  网络错都当"对象不存在",`write_once` 随后 `put_object` 破坏写一次证据语义。→ 仅 `code=="NoSuchKey"` 返
  False,余上抛。**实证核对 minio-py**:`stat_object`(HEAD)404+object_name→`NoSuchKey`、桶不存在→
  `NoSuchBucket`、403→`AccessDenied`、网络异常无 `.code`,故区分正确。
  **踩坑钉子**:F2 代码改动**破坏了既有 2 测试**——旧 `_FakeMinio.stat_object` 抛 `RuntimeError("NoSuchKey")`
  没 `.code` 属性 → 新逻辑当非 404 上抛。改 fake 为拟真 `_FakeS3Error`(带 `.code`)+ 补 2 条非 404 上抛回归测。
  **教训:改异常判别逻辑后必跑用 fake 的既有单测,fake 也得跟真 SDK 的错误形态。**
- **F3 preseg 生产摄取无入口**(决策=**加薄 `python -m` 驱动 + 写合同**):`register_preseg_batch`+`_drive_batch`
  原只在 `test_preseg_e2e` 手接,生产/运维无入口、且 `_drive_batch` 是 cli 私有函数。→ 新
  `pipeline/preseg_ingest.py`(`python -m pipeline.preseg_ingest <batch_dir>`),照 E2E 两步登记+驱动。
  **为何 `python -m` 而非 demo 子命令**:方案 A 生产构建剔除 demo/demo-web console_scripts,但 `python -m`
  模块执行不受影响 → 给仓外转换脚本/运维稳定入口,不冲突。**合同**:甲方预切块语料批量灌库=转换脚本产出
  批次目录(SPEC-PRESEG §3 接收契约)后调本入口;**B 模式**(`auto_confirm_meta_no_conflict` 开,默认)当场
  过 s5 直达 INDEXED——**需嵌入模型 + Milvus**;**A 模式**至多到 `META_REVIEW`,到终态需 worker 上下文再驱动。
- **文档漂移清理**(Optional):`PROMPTS.md` 删已删模块的死提示词(L2 业务域/案例 L2 T2.1·T2.2/E2 条款),
  留查询侧 R5/N0/N1/N3(仍在用);文件头改为"查询智能体提示词"。`README.md` 富集层描述去 E2、`[toggles]`
  只留 `e1_enabled`、env 去已弃用 `OPENAI_MODEL` 补 `PIPELINE_EMBEDDING_*`。`llm_client.py` 顶注改为"查询
  gateway 复用、管线富集已移除"(该模块现**仅** query gateway 懒导入消费)。**未动**历史 devlog / SDD 文档
  (SPEC/PLAN/TASKS/RTM/調研報告)——是时间点记录,重写=篡改历史,「全拆」最新条已记。
- **验证**:`test_minio_object_store`(9)/`test_preseg_s0`(6,连真 PG)/`test_preseg_ingest_entry`(5,零栈
  monkeypatch)+ 相邻 object_store/ondemand/s0 共 47 passed;`python -m pipeline.preseg_ingest --help` 正常;
  ruff 绿。端到端 REGISTERED→INDEXED 仍由模型门 `test_preseg_e2e` 覆盖。

## 2026-07-10(续)Codex 三条数据完整性 review 修复

三处「静默错误」类隐患——不崩、但会悄悄丢数据/污染检索/错解坏输入,危害大于显式崩溃。逐项修 + 回归测:

- **content_hash 未与实际块内容核验 → 变化件被静默丢弃**(`s0_register._register_one_preseg`):源幂等去重用
  manifest 声明的 `content_hash`,且**读 blocks 前**就返回 DUPLICATE。若源改了内容却没更新 content_hash
  (源幂等键失真),变化件被当重复丢弃、永不入库。→ dedup 命中时**交叉核验**实际块 `sha256` vs 现存版
  `source_hash`:一致才真 DUPLICATE;不一致 → 置 reason 走**隔离**(非静默丢),并经 `_latest_by_source_id`
  版本链 `revise_replace` 替代旧版供人工核实。回归测 `test_content_hash_stale_content_changed_quarantines`。
  **非显然**:块字节提前读一次算 `actual_hash`,同值复用给 DocVersion.source_hash(不重算);blocks 契约违约的
  except 用 `reason = reason or …` 不覆盖 hash 失真 reason。
- **远程嵌入响应未严格校验 index → 向量错配文本**(`embedding_client._extract_rows`):原仅「全部有 index 才
  排序」、且**不校验 index 是否恰为 0..n-1 排列**。部分缺 index / 重复 / 缺号 → 按序对齐把向量挂到别的文本,
  静默检索污染(难察觉)。→ index 一旦出现:**每条都须有、为整数(排除 bool)、且构成完整排列**,否则抛错。
  回归测:乱序复位 / 重复 / 缺号 / 部分缺 index 四例。
- **预切块 reader 接受错误字段类型 → 后续崩或错解**(`preseg/reader.py`):`is_table=bool(...)` 把字符串
  `"false"` 判成 True(错解表格);`clause_label`/`title` 非字符串照存(下游 normalize 崩);
  `violated_regulations`/`tags` 非 list 不拦(enumerate 遍历键/字符生成垃圾)。→ 补类型校验,坏类型**整文件
  拒收**(行级汇总)。附带修 `block_seq=true`(bool 是 int 子类)被当 1 混入。回归测 7 例(blocks 3 + cases 4)。
- **验证**:三处测试 42 passed;相邻 preseg 链 + embedding 81 passed / 7 skipped(模型门);ruff 绿;
  全仓 599 tests 收集 0 error。属 PR#47 上 Codex 第二轮 review。

## 2026-07-10(三)Codex 三轮 review:两条修复补强 + 两处路径穿越

前一轮 A/B 修得不彻底,加两处安全穿越。四条:

- **content_hash 用字节哈希破坏语义幂等**(修正上轮 #1):上轮拿**原始 JSONL 字节 sha** 与旧版 `source_hash`
  比,但 SPEC 幂等键是**源 content_hash 且明确替代文件 SHA 语义**——相同块内容仅调空格/JSON 键序/换行,字节
  哈希就变 → 被误隔离 + 建新版本。→ 新增 `reader.blocks_content_hash`:对**解析后块记录**(按 block_seq 排序 +
  稳定字段序)算 sha,纯重格式化不改哈希(**镜像案例 `record_hash` 先例**)。s0 dedup 交叉核验改用此指纹,
  `source_hash` 存它。回归测 `test_reformat_only_still_duplicate`(键序重排+空格→仍 DUPLICATE)。
- **案例 reader 类型校验仍不完整**(补强上轮 #3):Codex 实测仍接受 `persons:["not-an-object"]`(下游
  `first.get()` 崩)、`issue_date:20260710`(下游 `date.fromisoformat` 抛 TypeError)、vreg `clause_label:21`
  (条号归一化崩)、`content:123`(违字符串契约)。→ `read_cases` 补:persons 每项须 object、9 个字符串字段
  (含日期串)出现即须 str、vreg.clause_label/content 须 str;`_iso_date` 加 `isinstance(str)` 纵深(防非 str 抛
  TypeError)。**关键**:案例元数据 `case_enrich` 回读 raw 存储的原始 JSON,故必须在 reader 边界拦(整文件拒收)。
- **ObjectStore 本地后端路径穿越**(上轮遗留未处理):`_path` 直接 `root / key`。`process_upload` 的 object_key
  可用**绝对路径**读 root 外文件;含 `..` 的 upload_id 可把 artifact 写出 root。→ `_path` 统一
  `(root/key).resolve()` + `is_relative_to(root)` 校验,拒绝绝对/`..` 逃逸。**MinIO 后端不走 `_path`**(直接
  client+key),仅约束本地 FS。回归测:绝对 key / `..` 逃逸 / 正常 key 三例。
- **preseg manifest filename 越过 blocks/**:`filename="../outside"` 会读批次目录他处 JSONL。→
  `_register_one_preseg` 加 `fn != Path(fn).name` 单一文件名校验,非法 → `REJECTED`(新增 FileOutcome 状态)不建库。
- **验证**:四条测试相关 78 passed;全仓 608 tests 收集 0 error;ruff 绿。端到端仍由模型门 `test_preseg_e2e` 覆盖。
  属 PR#47 Codex 第三轮 review。
