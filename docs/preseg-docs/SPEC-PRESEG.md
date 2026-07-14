# Spec: 预切块数据源适配(P-PRESEG · CP-010)

> SDD 阶段:**Phase 1 / SPECIFY —— 待人工复核批准后进 PLAN**。
> 上游输入:《制度查询智能体_预切块数据源适配_调研报告》v0.2(§2.3 B1–B8 冲击表 + **§6 决策记录 D1–D8**,本 SPEC 一律以其为准,不再复述论证)。
> 背景:甲方要求**快速重构 + 内网部署测试**;源系统(法规制度平台)语料已按"条"预切块、案例→法规链接现成。

## 0. 约束装配(输入面清单)

- **权威上游**:调研报告 v0.2(事实基准=代码实现,澄清①);v1.6 §22.2 P-MISC 定义(降级/子集先例模板);硬契约(chunk_id 公式、PG add-only、写序 PG→Milvus→flush→INDEXED、IR 契约)。
- **消费者清单**:制度查询智能体(hybrid 检索 + 案例桥接双通道——本次的直接受益方);eval 工具链(T2/reconcile/rebuild 必须兼容,T4 对本 profile 禁用);audit-biz `/v1/query`(未来检索面,corpus_type P-* 口径,双轨红旗已记录不展开);比对智能体(仅记录,不改)。
- **红线**:数据流单向只读不回写;PG 权威/Milvus 可重建投影;schema add-only;默认路径零 LLM;机制替换走配置缝不物理删除(B8);信创离线内网(无外网依赖)。

## 1. 切片边界(本轮做什么 / 不做什么)

**做**:①P-PRESEG 摄取 profile(预切块块流→现管线,方向 A);②案例结构化直装(cases + 违反法规子表 + 涉案人员,点亮桥接);③manifest 自动生成的接收契约(源元数据→扩展 manifest);④效力状态映射器(D3);⑤profile 配置缝做实(B8 前置);⑥配套 add-only 迁移。

**不做**(均有决策依据):查询侧 entity_type 过滤(D7 暂缓,仅落库+写投影);款层(D5);页码及回填(D2,零页码设计);比对智能体/图谱;corpus_type 双轨收敛(跨仓事项);监管问答(继续自建 P-QA);源系统"合规审查记录"(不入库);LLM 任何环节(case_l2 引用抽取通道对本 profile 整体停用,被结构化数据替代)。

## 2. Objective(建什么 / 何为完成)

在**不改 AI 内核、不改检索架构、状态机零改**的前提下,让源系统导出语料端到端入库并被查询智能体消费。完成 = 样例批次(fixtures)`REGISTERED→…→INDEXED` 全通;幂等重跑零重复;**案例桥接/精确反查集成测命中**(现空转通道被点亮);全部既有测试绿。

## 3. 数据接收契约(本 SPEC 核心新增)

源导出实际形态未知(原 P0-2)→ **定义我方接收契约(intake contract),用一层薄转换脚本吸收源实际形态**(接缝;源格式明确后只改转换脚本,管线不动)。

### 3.1 批次目录形态

```
<batch_dir>/
├── manifest.xlsx          # P-PRESEG 扩展 manifest(§3.2;可由转换脚本自动生成——决策 D1"manifest 自动生成"落点)
├── blocks/<filename>.jsonl  # 每文档一个块文件(§3.3)
└── cases.jsonl            # 案例记录(仅案例批次;§3.4)
```

### 3.2 P-PRESEG manifest(扩展列集,精确匹配;仿 §22.2 P-MISC"必填列重写"先例)

现 11 列保留,新增列:`source_doc_id`(源系统主键,**幂等键**)、`content_hash`(源内容哈希,幂等键第二分量)、`effective_status`(源效力状态原值,经映射器→version_status,D3)、`issuer_level_src`(法律位阶/效力层级原值)、`tags`、`file_no`(内规文件编号)、`source_created_by`。`supersedes` 列由"关联文件"关系自动生成(替代/废止类;依据/参照类本期不承接,记录)。

> **版本链接缝现状(2026-07-15 达梦真数据探查,接缝保留)**:候选源字段 `SOURCE_LAW_ID`(全为空串)、`NEW_CODE`/`ABOLISH_CODE`(join 到另一 `LAW_BASIC.CODE` 命中 **0**)——**当前数据无可用替代/废止关系,故本轮转换脚本产出的 `supersedes` 列为空**(转换脚本 `export.py` 保留该接缝 `# 待定`,不硬编码放弃)。**契约不变**:`supersedes` 自动生成 + S4 版本链登记机制保留;未来数据若含可解析关系,或甲方确认改用别的关系源,再据实填(属范围变更,须 SPEC/RTM 同步 + 报备)。同 `source_doc_id` 换 hash 的自动 `revise_replace` 版本链不受影响,照常工作。

### 3.3 块记录(blocks JSONL,每行一块)

```jsonc
{
  "block_seq": 3,               // 源顺序,必填
  "clause_label": "第二十一条",  // 源条款标识原文,可空
  "text": "……",                 // 必填
  "is_table": false             // 可选
}
```

- **clause_path_norm 推导器**(B1 收口):`clause_label` → `normalize_clause_no`(复用 normalize.py 现有函数)→ 实现口径 norm(`章?/条`,插入条 `21-1`,款级**舍款取条**、款号存原文不丢,D5)。推导失败 → 伪路径 `preseg/{block_seq}` + `chunk_type=preseg_raw` 标记(仿 `qa/{n}`、`case/{k}` 先例);伪路径块不参与引用末段对齐(降级留痕)。
- **seq**:源一块一 chunk,`seq=0`;单块超预算时按句末边界二次切分 seq 递增(纯文本切分,不依赖 block 流机制)。
- **页码**:全链为空(D2)。引用固定三级(条款→文档→版本)+ chunk 级显式标识。

### 3.4 案例记录(cases.jsonl,每行一案)

基础字段(案例名称/发文单位/发生时间/问题汇总/原文链接/案例描述/发文文号/发文日期/案例类型/标签)+ `violated_regulations[]{title, clause_label, content}` + `persons[]{identity, name, type, reason}`。

- **虚拟文档合成**(D4):每案一 `doc_version`(`source_format="preseg"`,raw 为附件或空+标记),cases→doc_versions 外键不动,**bridge/r5 消费链零改**。
- **直装**:`violated_regulations` → 复用 `align_cited` + `PgRegLookup` 归一对齐(纯逻辑,title 已有剥《》处理)→ `cases.cited_regulations`;对齐失败仍置 `ref_unresolved`(现状口径,不建队列);`persons` → `cases.persons` JSONB(D6,不进检索/Milvus);"问题汇总"→ `case_summary` chunk 文本(替代规则截断版),"案例描述"→ `case_section` chunk。
- **case_l2 LLM 通道对本 profile 停用**:新增配置 `case_ref_source = structured | llm`(B8 配置缝,默认按 profile:P-PRESEG=structured,P-CASE=llm 现状)。

## 4. 管线行为(P-PRESEG;状态机零改,分支全在 stage 内部,与 P-QA/P-CASE 同构)

| 阶段 | 行为 |
|---|---|
| S0 | 新增 blocks/cases 接入路径(不走文件 magic-number 白名单);幂等键=`source_doc_id + content_hash`(替代 SHA-256 文件哈希语义);import_batches 批次/回滚单元不变(B7) |
| S1 | "解析"退化为**装载**:块文件 → 合成 IR(每块一 block,`page=None`)——IR 契约保真,下游零感知 |
| S2 | 启用子集 = ⑥文本质量 + 新增**必填元数据完整率**(§22.2 先例);①②③④⑦禁用;**启用集+阈值进 profiles.yaml(§6 配置缝)** |
| S3 | `profile_router` 加 P-PRESEG 分支 → `preseg_adapter.build_preseg_specs`(块流→ChunkSpec:推导器+seq+零页码) |
| S4 | 全复用:字典对齐(issuer 精确等值现状)、版本链登记(supersedes 自动列)、META_REVIEW 闸、**效力状态映射器**(D3:映射表+未知值→meta_confirm 队列;新列 `version_status_source` 留痕);业务类别→`biz_domains` 走现成 manifest 优先闸(L2 自然停用);适用对象→文档级继承写 `chunks.entity_type`+Milvus 投影(D7,过滤暂缓) |
| S5 | 全复用(嵌入/索引/冷备/upcoming 判定) |
| finalize | T4 对 P-PRESEG 跳过(D2);T2 冒烟照跑 |

**效力状态映射表(草案,实施期据真值域修订)**:现行有效→`effective`;已废止/失效→`abolished`;被替代/已修订→`superseded`(需目标版本,无则 meta_confirm);尚未生效→`upcoming`;**其余未知值→meta_confirm 队列**。冲突规则:源语料源优先并写 `pipeline_events` 留痕;自建语料链推导不动(D3)。

**效力状态映射表(真值域已定,2026-07-14 达梦真数据探查 A2;替代上方草案)** —— `STATUS_CODE` 是**英文码**:

| STATUS_CODE | 计数 | → version_status | 说明 |
|---|---|---|---|
| `inuse` | 9,795 | `effective` | 现行有效 |
| `abolish` | 3,815 | `abolished` | 已废止 |
| `modified` | 1,291 | `superseded` | 已修订(被新版替代;无目标版本时 meta_confirm) |
| `pending` | 19 | `upcoming` | 待生效 |
| `draft` | 382 | `upcoming` | **征求意见稿**(有正文未生效,探查 G8 证实;非入库半成品)——标未生效不污染现行法召回 |
| `test_run` | 3 | (整件跳过) | 测试数据,转换脚本层 `SKIP_STATUS` 排除,不入库、计入 skipped 审计 |
| 其余未知值 | — | (保默认 `effective`)+ meta_confirm | 不猜,人工定 |

> **Ask-first 变更留痕 + 批准(SPEC §7)**:上表新增英文枚举 + `draft→upcoming` + `test_run` 排除,属"效力映射
> 枚举增删"。依据=达梦真数据探查(SQL 确认 draft=征求意见稿、test_run 仅 3 条测试数据)。
> **已批准(2026-07-15,决策方拍板)**:draft→upcoming(未生效,可前瞻查询用、不当现行法)、test_run 整件跳过。
> 中文别名(现行有效/已废止…)保留作历史/自建批次容错。实现:`pipeline/preseg/status_map.py`。
> (仍与桥接 fuzzy→精确一并向甲方正式报备存档,不阻塞实施。)

## 5. Schema 变更清单(add-only,一批 Alembic 迁移)

- `doc_versions` +`source_doc_id`(索引)、`content_hash`、`version_status_source`、`issuer_level_src`、`tags`(JSONB)、`file_no`、`source_created_by`
- `cases` +`persons`(JSONB)、`occurred_at`(Date)、`source_url`
- 无新表;`chunks`/Milvus schema 不动(entity_type 列已存在,本次仅打通写入)

## 6. profile 配置缝(B8 前置,本次做实)

`config/profiles.yaml` 从"只有 sampling_rate"扩展为真档案:per-profile `qc_indicators`(启用集)、`qc_threshold_overrides`、`sampling_rate`、`case_ref_source`。**indicators.py 的硬编码启用字典改读配置**(现有 P-INT/P-EXT/P-QA/P-CASE 行为写入 yaml 作默认,行为零变更——回归保证)。切块路由(profile_router)维持代码分支不进配置(切块是代码不是参数)。

## 7. Boundaries(三档)

- **Always**:chunk_id 公式一字不改;写序 PG→Milvus→flush→INDEXED;所有新列 add-only + server_default 安全;决策 D1–D8 为准;fixtures 覆盖每个新分支;伪路径块必须带可查询标记。
- **Ask first**:接收契约(§3)破坏性变更;效力状态映射表增删枚举;D7/D8 默认采纳项的推翻;供源系统的转换脚本范围扩张;`preseg_raw` 伪路径块超过批次阈值(推导器失效信号)时的处置。
- **Never**:改检索架构/输出契约;物理删除现有解析/切块/case_l2 能力(配置停用≠删除);LLM 进本 profile 任何默认路径;回写源系统;为本 profile 改动 P-INT/P-EXT/P-QA/P-CASE 既有行为(配置缝迁移除外,且须回归证明零变更)。

## 8. Success Criteria(具体可测)

1. fixtures 样例批次(内规+外规+案例各若干,含:插入条/款级引用/推导失败块/未知效力状态/多涉案人员)端到端 `REGISTERED→INDEXED`,`demo status` 全绿。
2. 同批次幂等重跑:chunk/case 零重复(source_doc_id+content_hash 幂等)。
3. **桥接点亮**:直装案例后,`bridge.cases_for_clauses` 精确反查命中 + R5 `resolve_cited_clauses` 桥接通道集成测通过(现空转→有数据)。

> **§8.3 精确桥接的顺序与批末对账(2026-07-15 修订)**:案例结构化直装在 S4 当下用 `CASE_PUNISH.LAW_CONTENT_CODE` 精确查 `chunks.source_code`(限 effective P-EXT)。**⚠ 不能依赖处理顺序保证命中**:`_structuring` 在**同一步内**跑 S3(建 chunk)+ S4(案例桥接),而 `run_until_idle`/`docs_in_states` **不保证同轮内文档处理顺序**(无 `ORDER BY`,顺序由 DB 堆扫描决定)——故同批案例 S4 可能先于被引法规 S3 执行,当场落 fuzzy(先前"lockstep 保证法规 S3 早于案例 S4"的说法不成立,≈96.7% 只是当前 ctid 堆序的经验产物)。**为此批末做批内作用域对账** `reconcile_preseg_case_refs(ctx, batch_id)`:只重解析**本批** fuzzy 案例(`cited_regulations @> [{"match":"fuzzy"}]`);批驱动 drain 后本批法规**必已建块** → 升级 exact,**确定性、与扫描顺序无关**;O(本批案例),非全局 N+1。由 `python -m pipeline.preseg_ingest` 入口在 `_drive_batch` 后自动调用。**边缘退化(有效兜底,非降级红线)**:①案例与被引法规**跨批**(案例早批入库、法规晚批到);②案例引用 `upcoming` 法规,后经 `activate` 转 `effective`。这两类**批内对账不覆盖** → 退 **fuzzy 标题对齐**(仍可命中,只是非精确)。**精确恢复动作 = `reprocess <case_dvid>`**(重置 REGISTERED → 复用**同 dvid** 重跑 S3/S4,此时法规已 effective+建块 → 升级 exact;见 `cli.reprocess_to_indexed`)。**⚠ 不是"重灌"**:同内容重灌走 `preseg_ingest` 经 S0 `find_existing_case_doc`(content_hash 判重)= **DUPLICATE no-op**,不重跑 S4、不升级桥接。若未来跨批边缘量大,再引**内部持久重试队列**(按 newly-effective `source_code` 定向消费,不入 `cited_regulations`/不泄漏 API)—— 待办,非本轮契约。
4. 效力状态映射:四态直落 + 未知值进 meta_confirm 队列,单测覆盖映射表全部枚举。
5. 推导器 golden 集(仿条款树 golden 先例):条款标识样例→norm,P=R=1.0;失败样例正确落伪路径+标记。
6. 配置缝回归:indicators 改读 yaml 后,四个既有 profile 的 QC 行为与改造前逐指标一致(对拍测试)。
7. 三级引用:P-PRESEG chunk 检索命中后 citations 页码字段为空且带降级标识,查询侧不报错(B4 容错)。
8. 全仓既有测试绿 + ruff 绿;迁移 `alembic upgrade + check` 零漂移。

## 9. Open Questions(不阻塞 SPEC 批准;PLAN/实施期收口)

1. 交换格式定稿:blocks 用 JSONL(本 SPEC 倾向)vs 全 xlsx——待第一份真导出到手校核,接缝已隔离。
2. `issuer_level_src` → Milvus INT8 序:并入现 `_ISSUER_LEVEL_RANK` 还是独立映射(内外规同序可比问题)。
3. 案例附件若后续可得:`preseg` 虚拟文档挂 raw 的回填工序(D2 已排除页码回填,此处仅存档原件)。
4. 部署测试数据:fixtures 自造(先行)vs 真导出样例(到手即替换)——建议 fixtures 先行不阻塞。
5. `preseg_raw` 伪路径块的批次占比告警阈值(⚠ 待样例标定)。

## 10. 决策引用

D1–D8 全文见调研报告 v0.2 §6;本 SPEC 中所有"(Dn)"标注处即其落点。B3 验收口径变更(四级→三级,D2)**需向甲方报备**,报备动作在 PLAN 里立项跟踪。
