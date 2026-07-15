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

## 2026-07-10(四)Codex 四轮 review:非 UTF-8 JSONL 绕过隔离(最后一条阻塞)

前三轮全部确认修复正确;仅剩一条输入错误路径缺口:

- **非 UTF-8 JSONL 绕过隔离**:`read_blocks` / `_read_jsonl` 直接 `read_text(encoding="utf-8")`,非法编码抛
  `UnicodeDecodeError`,而调用方只 `except PresegFormatError` → ① blocks 不按设计进 QUARANTINED;② cases 在普通
  文档已登记后中止整批(部分写入);③ 命令以未处理异常退出而非出可审计拒收报告。→ ⑴ reader 加 `_read_text`:
  `UnicodeDecodeError` 统一转 `PresegFormatError`(read_blocks / read_cases 两路都走);⑵ S0 blocks 改
  `parse_blocks(data.decode("utf-8"), fn)` catch `(UnicodeDecodeError, PresegFormatError)`——**顺带消除 TOCTOU**
  (原"读一份字节存储 + read_blocks 再读一份校验"两次读,文件可在两读间变;现单次读 data)。回归测:reader 层
  blocks/cases 各一例;S0 集成两例(非 UTF-8 blocks→QUARANTINED;非 UTF-8 cases→整文件拒收 + 文档照登)。
- **s1_parse 已安全**:装载路径 `parse_blocks(data.decode(...))` 早已 `except (PresegFormatError, UnicodeDecodeError,
  ValueError)`(line 77)→ 优雅隔离,不在本次范围。
- **部署注意(Codex,非阻塞)**:上轮把 preseg `source_hash` 从字节 sha 改为语义规范化哈希。若某环境曾用旧提交
  写过 preseg 数据,其 `source_hash` 是字节哈希,升级后首次重跑会因哈希语义不同而 dedup 交叉核验判"内容变"→
  误隔离一次。**但 preseg 尚未部署到任何真环境**(demo 栈的 b01-25 是 register_batch 非 preseg 路径)→ 无存量
  preseg source_hash,不构成阻塞;真部署前若已有 preseg 数据则需清理/回填/兼容旧哈希。
- **验证**:reader + s0 共 49 passed / 1 skipped(模型门);全仓 612 收集 0 error;ruff 绿。属 PR#47 Codex
  第四轮 review——Codex 结论:修此条 + 补测试后即可进最终合并复审。

## 2026-07-14 真实内网库(ZNFG_IAM_LAW_* 8 表)到手 → 对齐重构(CP-010 续)

拿到甲方内网**真实数据库表结构**(`东方/东方知识库/知识库结构.md`,照片识别整理:8 表)。
**关键背景**:调研报告 v0.2 与 SPEC-PRESEG 是基于**UI 表头截图**做的(报告 §2.1 明确"类型全未知、
均待样例"),现在是真 DB schema——比当年假设精确得多。**接缝设计成立**(源形态落在"薄转换脚本"位置),
但真 schema ① 解锁两个当年被迫放弃的结构红利,② 填实几处"待样例"空白,③ 暴露一处口径出入。

### 8 表 → 现契约/PG 映射(逐表)

- `ZNFG_IAM_LAW_BASIC`(法规主)→ manifest 行 + doc_versions:`CODE`→source_doc_id(**逻辑键,跨版本稳**)·
  `NAME`→title · `DOC_NO`→doc_number/file_no · `ISSUE_AUTH_CN`→issuer · `EFFECT/INVALID_DATE`→生效/失效 ·
  `STATUS_CODE`→效力(→status_map)· `LEVELS`→issuer_level_src · `TAG`→tags · `SOURCE_LAW_ID`+`MODIFY_INFO`→版本链 ·
  `DEL_FLAG`(A/D/U)→软删 · `DATA_VERSION`→版本。
- `ZNFG_IAM_LAW_CONTENT`(章节/条文)→ blocks/<law>.jsonl:`IS_CATALOG`=1 章节标题块(不成 chunk)/=0 正文 ·
  `TITLE`→clause_label · `INDEX_NO`→block_seq · **`PATH_CODE`→权威层级路径** · `CONTENT`→text · **`CODE`→源条款锚**。
- `ZNFG_IAM_LAW_CONTENT_DETAIL`(详情)→ `CONTENT_TYPE` 0文本拼入 / 1图片 / 2视频。
- `ZNFG_IAM_LAW_CASE_BASIC`(案件主)→ cases.jsonl:`CODE`→source_case_id · `NAME`→case_name ·
  `PUB_AUTH_CN`→issuing_org · `EVENT_DATE`→occurred_at · `CASE_DESC`→description · `SUMMARY`→problem_summary ·
  `URL`→source_url · `PUNISH_BASIS`(处罚依据全文)。
- `ZNFG_IAM_LAW_CASE_PARTY`(涉案主体)→ persons[] JSONB(D6):真表 20+ 列(罚没金额/证券代码/行业/地区/板块/
  经办人…),JSONB 原样承载,转换脚本带全。
- `ZNFG_IAM_LAW_CASE_PUNISH`(处罚依据,**桥接核心**)→ violated_regulations→cited_regulations:
  **`LAW_CODE`+`LAW_CONTENT_CODE`=源已算好的精确条款外键** · `PUNISH_LAW_TITLE`→title · `PUNISH_LAW`→content。
- `TB_INFA_PUSH`/`_INFO`(ETL)→ import_batches/增量:`WORKFLOW_RUN_ID`→批次 · `BUSI_DATE`+`ETL_STATUS`→增量/回滚。

### 决策(用户逐项拍板 2026-07-14)

- **交付形态**=**内网可直连源库**:转换脚本直接 SELECT 8 表。**源库=达梦 DM8**(2026-07-14 用户确认)——
  DM 大量兼容 Oracle,正对上 schema 的 NUMBER(0)/TIMESTAMP(36,6)/TEXT-CLOB/Snowflake ID 形态。
  驱动 `dmPython`(DBAPI)+ SQLAlchemy `dm` 方言(`sqlalchemy-dm`);DSN 走 config/env,不硬编码凭证。
  **信创注意**:dmPython 是达梦官方轮子,需内网从达梦装包源装(离线),不在 PyPI 常规源——P5 部署清单列明。
- **本轮捕获红利**=精确案例桥接 · 权威条款层级 · 版本链登记(三项动管线)+ 效力状态填实 · 富涉案主体(两项仅转换脚本)。
- **本轮跳过**=图片/视频(`CONTENT_DETAIL.CONTENT_TYPE=1/2`):当前契约无富媒体概念,纯文本优先,**仅记录**留二期。

### 最大红利:案例桥接 fuzzy → 精确(否决"维持现状")

现 `cases_ingest.extract_case_structured` 拿 `{title, clause_label}` 走 `align_cited`+`PgRegLookup` **模糊匹配**
(文号/标题命中 + 条号归一比对末段),会 `ref_unresolved` 失败。而 `CASE_PUNISH.LAW_CONTENT_CODE` 是**源系统已算好
的精确外键**——SPEC 背景"案例→法规链接现成"说的就是它,但**当前实现没用**(老契约 `{title,clause_label,content}` 把
CODE 丢了)。
- **机制**:chunks 加 `source_code` 存 `LAW_CONTENT.CODE` → 摄取案例时用 `LAW_CONTENT_CODE` **确定性反查**目标条款的
  `doc_no + clause_path_norm` → 喂**完全不变的** query 侧 bridge(`norm_ref = 文号|条款路径`)。
- **不碰红线**:是**摄取期落库**(不是检索后回查 PG 装 citation),query/输出契约/检索架构**零改**;无 CODE 时(非 preseg
  批次)**回落** align_cited,行为兼容。属**质量提升非降级**(`ref_unresolved` 大幅下降),与 D2 页码降级一并向甲方报备。
- **否决**:❌ 改 query 侧 bridge 键为 CODE——跨仓、破 `norm_ref` 契约、波及 audit-biz,得不偿失;精确反查放摄取侧即可,
  bridge 一字不改。

### 权威层级 & 版本/效力填实

- **层级**:`derive.py` 从 `clause_label` 文本正则推 norm、推不出落伪路径 `preseg/{seq}`+`preseg_raw`。真表给 `PATH_CODE`
  (层级路径)+`INDEX_NO`(顺序)+`IS_CATALOG`(目录/正文判别=正好"章节点不出 chunk")→ **权威、零推导失败**。adapter 带源
  路径直接用,无源回落 derive(兼容非 preseg 批次)。
- **效力/版本**:status_map(D3)当年是草案 + "待真值域";现 `STATUS_CODE`/`EFFECT_DATE`/`INVALID_DATE` 是真效力字段,
  `SOURCE_LAW_ID`/`MODIFY_INFO`/`DATA_VERSION` 是真版本链依据 → 填实。

### 口径出入(留痕待核)

裁字典那次(2026-07-09)依据是"甲方已把**业务类别/适用对象**预结构化"(《表头整理.xlsx》=UI 表头)。但真 DB `LAW_BASIC`
**无 业务类别/适用对象 专列**(只有 `TAG`/`LEVELS`/`FORBIDDEN_MSG`)→ `biz_domain`/`entity_type` 源头**现不明**(可能藏
`TAG`、可能 UI 派生、可能未拍到的表)。**默认处置**:能从 `TAG` 解析就映射,否则留空(D7 过滤本就暂缓),不阻塞;真值域待
甲方对接会确认。

### Phase 0 已落(add-only 迁移 0015 + 模型)

- `chunks.source_code`(String(64),index)——精确桥接锚。
- `doc_versions.invalid_date`(Date)——失效日期,与 effective_date 成对。
- `doc_versions.source_law_id`(String(64),index)——版本链源。
- 三列均可空无回填,对既有行安全;版本链/效力其余列(source_doc_id/content_hash/version_status/version_status_source/
  effective_date/version_relation/supersedes_version_id/tags…)迁移 0013/0014 已在,**不重复加**。
- **冻结不动**(SPEC §7 Never):chunk_id 公式 · 写序 PG→Milvus→flush→INDEXED · query 侧 bridge/输出契约/检索架构 · 状态机。

### 落地进度(2026-07-14 同日)

- **P1 契约扩展**(reader):blocks +`source_code`/`clause_path_norm`/`is_catalog`;vregs +`law_code`/`law_content_code`;
  沿用 Codex 四轮"坏类型整文件拒收"严校验。**钉子**:`blocks_content_hash` canon **纳入三新字段**——`clause_path_norm`
  进 chunk_id、`source_code` 决定桥接、`is_catalog` 决定成不成块,不纳入则"仅改结构元数据"的变化件被误判 DUPLICATE 漏更新
  (preseg 未上真环境,改 canon 无存量哈希冲突)。
- **P2 权威层级**(adapter/ChunkSpec/S3):`is_catalog`(源 IS_CATALOG,权威)优先判目录块不成 chunk,`clause_path_norm`
  (源 PATH_CODE 算好)优先做 norm——**有权威路径即真条款,绝不落 preseg_raw 伪路径**;缺失回落 derive(向后兼容非源批次)。
  `ChunkSpec.source_code`→`chunks.source_code`(S3 落库)。
- **P3 精确桥接**(reg_lookup/cases_ingest,**最高价值**):`PgRegLookup.resolve_exact(law_content_code)` 取**同一 chunk 及其
  doc_version** 的 `doc_no`+`clause_path_norm`——与 query 侧对该条款算的 `norm_ref` 键**逐位一致**,桥接确定性点亮。
  `_align_violated`:有锚精确直连(`match=exact`)、无锚/锚未入库回落 `align_cited` fuzzy(`match=fuzzy`,保序,不静默丢)。
  **红线核对**:摄取期落库、非检索后回查装 citation;query 侧 bridge/输出契约**一字未改**;限 effective P-EXT(同 fuzzy 语义)。
  **验证**:`query/tests/test_preseg_bridge_integration.py` 绿 = 端到端点亮。
- **P4 效力/版本填实**(reader/s0):契约补 `source_law_id`(版本链源)+`invalid_date`(失效日期),S0 接进 doc_versions。
  **钉子**:`invalid_date` 仅作 **provenance / 时间窗展示**,**不做"日期过期→abolished"推断**——D3 效力状态由 `effective_status`
  →`status_map` **源权威**,日期推断会与之冲突。`STATUS_CODE` 真值域仍未知(schema 只标"法规状态"),`status_map` 草案沿用
  (未知值→meta_confirm),真码表由 P5 转换脚本/甲方对接会锁。fixture manifest 重生成为 21 列。
- **验证累计**:reader 35 / adapter 15+S3 / s0 / cases_ingest(精确桥接 PG 集成)/ 桥接集成 —— 全套 190+ passed / 模型门 skip;
  迁移 0015 已 `alembic upgrade` 到主测试栈(5432 audit_pipeline);ruff 全绿。**契约破坏性变更(SPEC §7 Ask-first)**:blocks/vregs/
  manifest 均扩展——已由用户"捕获结构红利"决策授权,转换脚本(P5)按新契约产出。

### P5 转换脚本(直连达梦)已落

- **模块**:`pipeline/preseg/export.py`(纯转换:blocks/case/manifest 装配,FakeSource 可单测)+
  `pipeline/preseg_export.py`(`python -m pipeline.preseg_export <out_dir>`:DmSource 达梦 SQLAlchemy 直连 + 编排落盘)。
  达梦 DSN 走 env `PRESEG_SOURCE_DSN`(不硬编码方言/凭证);两步分离——导出可离线核对产物,灌库(preseg_ingest)另跑。
- **高置信已做实**(不依赖未知项):`LAW_CONTENT.CODE`→blocks.source_code、`CASE_PUNISH.LAW_CONTENT_CODE`→精确桥接锚、
  `IS_CATALOG`→is_catalog 权威、CASE_PARTY 全字段→persons(D6)、`DEL_FLAG≠D` 过滤、content_hash=`blocks_content_hash`
  (声明==实际,幂等健壮)。
- **验证**:`test_preseg_export.py` 3 测——转换产物 **round-trip 过 reader 接收契约**(不 PresegFormatError)、精确锚串上
  (law_content_code==block.source_code)、删除/空件过滤、content_hash 确定。零栈。
- **部署 seam 清单(达梦真样例/甲方对接会锁定)**:①`PATH_CODE`→clause_path_norm 格式(暂省→adapter 从 TITLE 派生,
  is_catalog 权威仍生效)②`STATUS_CODE` 值域(暂透传→status_map 未知值 meta_confirm)③`LEVELS`→内/外规分型 + 密级
  ④biz_domain/entity_type 源头(schema 无专列,§口径出入)⑤日期字段达梦返回型/格式 ⑥`DEL_FLAG=U` 处置 ⑦`supersedes`
  由 `SOURCE_LAW_ID`/`MODIFY_INFO` 生成的格式(现暂 source_law_id 存原值 + 同 source_doc_id 换 hash 自动 revise_replace)。
  ⑧**dmPython 驱动**信创内网离线装(达梦官方轮子,非 PyPI 常规源)。

### P6 验收

全仓 ruff `All checks passed!`;1075 tests collected 0 error;触及面集成 213 passed/5 skipped(模型门,真 PG 5432)。
迁移 0015 已 `alembic upgrade` 主测试栈。**遗留**:①端到端 `test_preseg_e2e`(模型门)在真栈补跑一次含新契约批次;
②**报备甲方**:案例桥接 fuzzy→精确(质量提升,ref_unresolved 假阴大幅降)并入 D2 页码降级报备;③部署 seam 清单待样例锁定。

## 2026-07-14(续)Codex review 修复 + 重大发现:知识库结构.md 有损,真 schema 在图片

**⚠ 重大发现**:用户提供的 `东方/东方知识库/知识库结构.md` 是**有损精简版**;完整 schema 是
`东方/东方知识库/图片/IMG_1109-1123.HEIC`(一份带 Field/Type/Nullable/Description 表 + mermaid 的英文 md 截图)。
md 漏了安全/正确性相关的列,Codex 逐图核对后报出。**真 schema 关键补正**(HEIC→PNG 核对):
- `ZNFG_IAM_LAW_BASIC.SCOPE INT(10)`:**0=外规 / 1=内规 / 2=标准**——权威内外规分类列(md 完全没有)。
- `SUIT_OBJ_CODE VARCHAR(180)`=适用对象(entity_type 源)、`ABOLISH_CODE VARCHAR(2048)`=废止引用、
  `NEW_CODE`/`NEW_CONTENT_CODE`/`NEW_PATH_CODE`=换码映射、`HAS_CONTENT`、`CREATOR_ID/UPDATOR_ID`(source_created_by 源)。
- **列宽**:`LAW_CONTENT.CODE VARCHAR(256)`、`LAW_BASIC.CODE VARCHAR(180)`、`SOURCE_LAW_ID VARCHAR(256)`、
  `NAME VARCHAR(400)`、`DOC_NO VARCHAR(2000)`、`ISSUE_AUTH_CN VARCHAR(4000)`、`PATH_CODE VARCHAR(1020)`。
- `DEL_FLAG VARCHAR(4) **NULL**`、日期为 `DATE(13)`/`TIMESTAMP(36,6)`。

**Codex 10 findings 全处置**(1 critical + 8 warning + 1 suggestion):
- **[critical] SCOPE fail-open**:导出器原用 `LEVELS` 猜内外规且缺省 P-EXT/public → 内规可能被标 public 越权披露。
  修:`classify_scope(SCOPE)` 权威映射(0→P-EXT/public、1→P-INT/internal、2→P-EXT/internal 密级保守),
  **未知/空 → 拒收该件不导出(fail-closed),绝不默认 public**。
- **[warn] 列宽**:源键列宽 > PG 列宽会 S0/S3 DataError 断批。修:迁移 0016 拓宽键列 `chunks.source_code`/
  `doc_versions.source_doc_id`/`source_law_id` 64→256;描述列 `_fit` 按 PG 宽截断 + 审计告警(不断批);键超 256 拒收(不截断破幂等)。
- **[warn] 日期规范化**:达梦返回 date/datetime,`str(datetime)` 含时分秒 → S0 `fromisoformat` 静默落 None。
  修:`_date()` 统一 ISO 日期(datetime 取 `.date()`)。
- **[warn] DEL_FLAG NULL 漏行**:`DEL_FLAG <> 'D'` 对 NULL 求值 UNKNOWN 在库端静默排除。修:SQL 改 `(DEL_FLAG IS NULL OR <> 'D')`(全 5 查询)。
- **[warn] 桥接顺序依赖(F7)**:精确桥接在案例 S4 当下查 chunks,同批法规未到 S3 时锚查空→固化 fuzzy/unresolved 无重试;
  `_drive_batch` worker 引擎不保证法规先于案例。修:`reconcile_preseg_case_refs` **批后对账**重解析 `ref_unresolved` 案例
  (定点改两列,不经 merge 覆盖),接进 `preseg_ingest.run`;跨批亦自愈。回归测 `test_reconcile_fixes_case_first_ordering`。
- **[warn] 陈旧产物**:复用 out_dir 时空 cases 不删旧 `cases.jsonl` → 旧案例再摄取。修:`_clean_out_dir` 落盘前清 blocks/cases/manifest。
- **[warn] 文件名碰撞**:`_safe_filename` 非单射(A/B 与 A?B 同名)。修:基名 + `sha1(CODE)[:8]` 后缀 + seen_files 去重。
- **[warn] 快照一致性 + N+1**:每查询新连接 → 主/子表异时点。修:全导出**单连接单事务**(尽力 REPEATABLE READ)。
- **[warn] 凭证进 argv**:`--dsn` CLI 泄露凭证。修:**去 --dsn**,DSN 仅走 env `PRESEG_SOURCE_DSN`。
- **[sugg] PATH_CODE 词法序**:字符串排序 1.10<1.2 误序。修:blocks 按 `INDEX_NO`(源 0-based 序)+ CODE 排,不按 PATH_CODE 串。

**seam 更新**:内外规分型不再是 seam(SCOPE 权威已接);biz_domain/entity_type 源=`SUIT_OBJ_CODE`(待值域/编码表);
source_created_by=`CREATOR_ID`(待接)。**验证**:迁移 0016 已 upgrade 主栈;全仓 ruff 绿;1082 collect 0 error;
export 9 + cases_ingest(含 reconcile)+ s0/reader/adapter + 桥接集成 全绿。

## 2026-07-14(再续)达梦真数据探查 → seam 全锁定(用户跑 SQL,`preseg_explore*.sql`)

有了达梦连接后逐项探查真数据(结果图 `东方/东方知识库/sql脚本结果*/`),把 seam 用真值锁死,并纠了多处初版臆测:

- **STATUS_CODE 是英文码不是中文**(A2):inuse 9795 / abolish 3815 / modified 1291 / draft 382 / pending 19 /
  test_run 3。`status_map` 重写:inuse→effective · abolish→abolished · modified→superseded · pending/draft→upcoming;
  **test_run(3 条测试数据)转换脚本层跳过**(`SKIP_STATUS`)。
- **draft = 征求意见稿**(G8 样例名全带"(征求意见稿)",HAS_CONTENT=1、EFFECT_DATE 空)——**非"入库未完成"半成品**;
  真草案有正文未生效 → `upcoming`(标记未生效,不污染现行法召回)正确。
- **PUNISH_LAW/PUNISH_LAW_TITLE 语义与文档相反**(C2 真数据):PUNISH_LAW=**法规名**、PUNISH_LAW_TITLE=**条款标识**。
  `_violated` title/clause **反转 bug 已修**(以数据为准)。
- **精确桥接真数据命中 96.7%**(C1):11.4万处罚引用,null 锚 0,未命中仅 3,737 → 最大红利被真数据强验证。
- **SCOPE 全库=0**(A1,15,305 部全外规)→ corpus/perm 实际全 P-EXT/public;classify_scope fail-closed 仍作兜底。
- **SUIT_OBJ_CODE=中文适用对象多值**(A4,顿号/竖线混用)→ `entity_types_of` 多分隔符拆;`CREATOR_ID→source_created_by` 接上。
- **PATH_CODE=点分 opaque CODE 路径**(B1,哈希段非可读编号,人类编号在 TITLE)→ `path_code_to_norm` 恒 None **是对的**(TITLE 派生)。
- **LAW_CONTENT 每节点 2 物理行**(B2)——**同 CODE 异 snowflake ID、内容一致,全表 COUNT(DISTINCT CODE)恒=1**(G1/G2)
  → `blocks_from_contents` **按 CODE 去重**(保留首见,source_code/文本/桥接不受影响)。
- **版本链字段 100% 非空**(E1:SOURCE_LAW_ID/ABOLISH_CODE/NEW_CODE 全满)→ 疑占位/自引用,值语义待 E2;自动 supersedes 继续缓。

**查询侧跟进(记待办)**:真适用对象词表=通用/证券/基金/期货/其他金融机构… 与 demo `dict_entity_types`(证券公司/证券从业人员)
粒度不同 → D7 过滤要对得上须换 query 侧词表。**验证**:全仓 ruff 绿;export 13(含 SCOPE/日期/去重/适用对象/status_map)+
触及面 117 passed/4 skipped(真 PG)。

## 2026-07-14(四续)Codex 二轮 review 修复(8 findings,含 1 critical)

前一轮真数据 seam 锁定后 Codex 二轮复审,8 条逐项处置:

- **[critical] 0016 alter_column 破 add-only**:改 3 个既有列类型违反 schema add-only 硬契约,downgrade 收窄丢数据。
  修:**删 0016**,把 source_code/source_law_id 宽度(256)**折叠进 0015 的 add_column**(新列,一步到位);
  `source_doc_id` **不动**(0013 已并 main;真实 code ~39 字符 << 64,足够)。栈已 downgrade 0014→re-upgrade,单 head 0015。
- **[warn] 描述列截断丢法律元数据(F2)+ 案例字段无校验(F3)**:`_fit` 截断改 **`_bound` 超列宽即拒收**
  (PresegExportError→skip 该件+审计,不截断/不 DataError);覆盖法规(title/doc_number/issuer/…)+ 案例
  (source_case_id/doc_number/issuing_org/case_type/source_url)全部落 PG 定长列。
- **[warn] 导出非原子(F4)**:`_clean_out_dir` 先删后建,源读取失败会销毁有效批次。修:**staging 临时目录完整
  构建 + 成功后 `os.replace` 原子换入**;异常清 staging、out_dir 原样。
- **[warn] 对账仅本批(F5)**:reconcile 只扫 `batch_id` → 跨批晚到法规不补早批案例。修:**去 batch_id 过滤,
  全局扫 ref_unresolved preseg 案例**(规模优化记待办)。回归测 `test_reconcile_is_global_cross_batch`。
- **[warn] 快照 fail-open(F6)**:isolation 设置失败被吞→弱隔离出不一致快照。修:**去 try/except,设置失败即中止**(fail-closed)。
- **[warn] 去重不验内容(F8)**:仅按 CODE 丢第二行,不比内容、无 ORDER BY。修:去重时 **比对 `_content_sig`
  (TITLE/CONTENT/IS_CATALOG/INDEX_NO),冲突告警留痕**;DmSource contents 查询加 `ORDER BY INDEX_NO,CODE,ID` 保确定性。
- **[warn] status_map SPEC 漂移(F7)**:SPEC 仍是旧草案。修:**SPEC-PRESEG §4 更新真值域表**(英文码 + draft=征求意见稿→
  upcoming + test_run 排除)+ Ask-first 留痕(待报备甲方)。
- **验证**:全仓 ruff 绿;1094 collect 0 error;export 19 + 触及面 124 passed/4 skipped(真 PG);单 head 0015。

## 2026-07-15 Codex 三轮 review 修复(6 条)+ 版本链探查结论 + 效力映射批准

**explore3 结论**:版本链三字段**当前数据无可用关系**——`SOURCE_LAW_ID` 全空串(distinct=1)、`NEW_CODE`/
`ABOLISH_CODE` join 他法命中 **0** → **本轮转换脚本产出的 `supersedes` 为空**;**接缝保留(非放弃契约)**,
未来数据/关系源确认再填(见 SPEC §3.2 版本链接缝现状,Codex 四轮 S6 修正措辞)。`CASE_PARTY` 空(甲方只灌
处罚→法规链,无当事人)。未命中锚 3737 中 **3736 是空锚**(I2)——指导 R2/S1 的候选筛选。

**Codex 三轮 6 findings**:
- **R1 导出仍非原子**:staging 后仍先 rmtree 旧目录再 replace,有窗口。修:**拒绝写入非空目录**(绝不销毁已有批次)
  + 空目录 rmdir 后 `os.replace` 到不存在目标(原子)。
- **R2 全局对账无界 + N+1 + 坏对象崩批**:修:①**只重试"有锚未解析"案例**(`_align_violated` 给 fuzzy 保留
  `law_content_code`,reconcile 按此筛,排除 3736 空锚永久 fuzzy);②**单条坏 raw 隔离**(try/except 不阻塞收尾)。
- **R3 去重冲突仍静默留首见**:改 **同 CODE 内容冲突 → 拒收整部法规**(可审计,不假设首见权威)。
- **R4 respondent 未校验**:`persons[0].name`(投影 `cases.respondent` VARCHAR(256))补 `_bound`。
- **R5 source_created_by 未校验**:`CREATOR_ID`(→VARCHAR(64))改 `_bound`(原漏用 `_s`)。
- **R6 status_map Ask-first**:decision 方**已批准**(2026-07-15)draft→upcoming + test_run 跳过;SPEC §4 记批准。
- **验证**:全仓 ruff 绿;1096 collect;export 21 + 触及面 122 passed/4 skipped(真 PG);单 head 0015。

## 2026-07-15(续)Codex 四轮 review 修复(7 条,多为三轮 reconcile 改动的连锁)

- **S1 fuzzy 成功不升级 exact**:对账原 gate `ref_unresolved=True` + 条目 `resolved=False`,漏掉"有锚却被同名旧法规
  fuzzy 成功命中"的案例(resolved=True)→ 精确法规后到也不升级、长期留错版本。修:**触发按 `match!=exact` 筛**
  (JSONB `@> [{"match":"fuzzy"}]` 下推,可 GIN 索引),不看 resolved/ref_unresolved。
- **S3 内部源锚泄漏 query API**:三轮把 `law_content_code` 写进 `cited_regulations` → `structured.py:120` 原样塞进
  `related_regulations`(契约 `list[str]`)泄漏内部锚。修:**撤回锚标记**,对账改用已有 `match` 字段筛(不落库内部锚)。
- **S2 对账 broad-except fail-open**:try 连 `_align_violated`(DB/逻辑)一起吞、静默 continue → DB 挂也返 0。
  修:**只裹 ObjectStore 读+解析**,`_align_violated` 的 DB/逻辑错**向上抛**(不 fail-open);坏 raw 隔离。
- **S1/R2 有界**:对账**只在本批新增了法规(非 P-CASE)时跑**(没新法规则无案例能新解析);跨批自愈=法规批触发全局扫。
- **S4 respondent 校验绕过**:`_bound` 先 strip 再校验且丢返回值 → 前导空格名(strip 后短、原值 301)漏过 → S4 DataError。
  修:按 **persons[0].name 原值长度**校验(不 strip)。
- **S5 staging 泄漏**:提交阶段(rmdir/os.replace)不在 try 内,replace 失败留残 staging。修:**提交也进 try**,失败清 staging。
- **S6 版本链契约**:devlog 单方面写"放弃"→ 改**"当前数据无可用关系、`supersedes` 本轮为空、接缝保留"**;SPEC §3.2 加版本链接缝现状(契约不变,范围变更须报备)。
- **验证**:全仓 ruff 绿;1096 collect;export 24 + cases_ingest(含 fuzzy→exact 升级 + 有界对账)+ 触及面 129 passed/4 skipped(真 PG)。

## 2026-07-15(三续)Codex 五轮:退掉过度设计的批后对账 + 修 query 契约

Codex 五轮 4 条**全在 reconcile**(批后全局对账连生 4 轮 findings:T1 漏 upcoming→activate、T2 坏 raw 隔离不全、
T4 全库 N+1、外加 T3 query 契约遗留)。**根因**:`run_until_idle` 逐轮 lockstep 推进——转换脚本把法规+案例放
**同一批**,同批法规 S3 必在案例 S4 前一轮完成,`resolve_exact` 在 S4 **直接命中**(≈96.7%),**批后全局对账本就
是多余的过度设计**。故**退掉**而非继续打补丁:
- **删 `reconcile_preseg_case_refs`**(函数 + preseg_ingest 调用 + 两个 reconcile 测)。
- preseg_ingest 文档说明顺序要求;SPEC-PRESEG §8.3 记 lockstep + fuzzy 兜底(边缘:跨批晚到/upcoming→activate →
  退 fuzzy,重灌案例即升级 exact;未来量大再引内部持久重试队列——待办,不入 cited_regulations/不泄漏 API)。
- 回归测 `test_out_of_order_falls_to_fuzzy_then_upgrades_on_reingest`(乱序→fuzzy;重跑 S4→exact)。
- **T3 query 契约**:`structured.py` 把 `cited_regulations`(dict 列表)原样塞进 `CaseHit.related_regulations`
  (契约 `list[str]`)。加 `_related_regs` 投影为"法规名 条款路径"字符串,**不泄漏** resolved/match/law_content_code;
  跨层测 `test_related_regulations_preseg_dict_projected_to_strings`。**遗留问题(非本轮引入,align_cited 一直产 dict)顺手修**。
- **验证**:全仓 ruff 绿;1096 collect;触及面 108 passed/4 skipped(真 PG)。cases_ingest 更简(exact@S4 + fuzzy 兜底,无对账层)。

## 2026-07-15(四续)Codex 六轮:五轮"退对账"依据的前提是假的 → 收窄成批内对账 + 纠正恢复口径

Codex 六轮 2 条 warning **都指向五轮那次退对账引入的裂缝**——**根因是五轮的前提被证伪**:
- **F1「lockstep 顺序保证不存在」**:核到代码——`_structuring` 在**同一步内**跑 S3(建 chunk)+ S4(案例桥接),法规与案例**同轮**进 STRUCTURING;`docs_in_states` **无 `ORDER BY`**,轮内处理顺序由 DB 堆扫描决定。故"同批法规 S3 必在案例 S4 前一轮完成"**不成立**(S3/S4 同轮同步,≈96.7% 只是当前 ctid 堆序的经验产物)。同轮内案例 S4 抢先 → fuzzy,**删对账后无自愈**。
- **F2「恢复动作是假的」**:文档写"重灌案例升级 exact",但 `find_existing_case_doc` 只按 `content_hash(+source_case_id)` 判重 → 同内容重灌 = **DUPLICATE no-op**,不建版本、不重跑 S4。五轮的回归测 `_run_case` 直接对同 dvid 重调 stage、**绕过 S0 幂等** = 假绿。

**决策(用户选)**:F1 用**批内作用域对账**(非原样恢复全局对账);F2 恢复动作改成**真实命令 `reprocess`**。
- **重加 `reconcile_preseg_case_refs`,但收窄为批内**:`WHERE batch_id==bid`,只重解析**本批** fuzzy 案例;去掉五轮 findings 缠斗的全局层(added_law 门 / 全库 @> 扫描 / upcoming→activate 跨批门)。O(本批案例),**确定性自愈同批竞态、与扫描顺序无关**。preseg_ingest 在 `_drive_batch` 后自动调。跨批/upcoming 边缘仍退 fuzzy 兜底(honest 保留)。
- **恢复口径纠正**:SPEC §8.3 + preseg_ingest 注释删"重灌即升级"假描述,改**`reprocess <case_dvid>`**(重置 REGISTERED→复用同 dvid 重跑 S3/S4);明确重灌=DUPLICATE no-op、不是恢复动作。
- **测试改真**:①`test_same_batch_race_healed_by_batch_reconcile`(fuzzy→批内对账→exact,+ 批内作用域:别的 batch_id 不动本案);②`test_reingest_same_case_is_duplicate_noop_not_recovery`(证 F2:重灌=DUPLICATE、复用原 dvid);③模型门 e2e `test_preseg_case_reprocess_recovers_bridge`(公开 reprocess 入口重处理虚拟案例→重回 INDEXED+exact,验 F2 真实恢复)。
- **验证**:全仓 ruff 绿;触及面 PG-only 14 passed(真 PG);e2e reprocess 测为模型门(本会话 BGE 未起 → skip,correct-by-construction)。

## 2026-07-15(五续)Codex 七轮:批内对账的 perf/reliability + 恢复测试假绿(全在六轮改动上)

Codex 七轮 3 warning 全在六轮那次批内对账 + e2e reprocess 测:
- **F1 e2e 恢复测试无 fuzzy 前置**:`driven` 已把法规+案例完整驱动,案例调 reprocess 前**本就 exact** → 只验了 exact→reprocess→exact,即便 reprocess 不升级 fuzzy 也过(还因模型门 skip)。→ **拆两测**:①PG-only `test_reprocess_rerun_recovers_fuzzy_to_exact_idempotent`(**genuine** 前置:案例先跑断言 fuzzy → 法规建块 → reprocess 语义重跑 S3/S4 → exact → 再跑幂等;可实跑验证);②e2e `test_preseg_case_reprocess_via_public_entry` 降格为**只证公开入口对虚拟案例跑通**(reprocess_to_indexed→INDEXED+投影),删恢复假断言。
- **F2 批内对账仍逐案 N+1**:每 target 读 raw + 逐 ref `resolve_exact`,空锚/未命中还进 `align_cited` 的标题+chunk 查询;devlog 记 3737 fuzzy 中 3736 空锚 → 首批/幂等重跑数千次无效 DB 往返。→ **改定点批量**:一次收锚 → **单次** bulk 查 P-EXT `chunks.source_code` 建映射 → 逐案**只升级命中锚的 fuzzy 条目**(`align_cited` 逐项 1:1,按位对齐),**无命中锚案例零 DB 往返**(pre-check 不开 session)。非锚标题 fuzzy 不再在对账里重解析(退 `reprocess` 全量恢复,SPEC §8.3 ③)。
- **F3 静默吞存储失败**:对账是保 exact 的唯一机制,但 broad `except: continue` 把 ObjectStore/UTF-8/JSON 失败静默咽掉、ingest 仍报成功却留 fuzzy。→ **收窄**:只捕 `UnicodeDecodeError`/`JSONDecodeError`(格式损坏:warning+计数,留 fuzzy 可 reprocess),**存储/基础设施错不捕获、向上抛**(入口非零退出)。
- **验证**:全仓 ruff 绿;preseg 触及面 PG-only 10 passed(真 PG,含 genuine fuzzy→exact→幂等);e2e 模型门(本会话 skip)。
