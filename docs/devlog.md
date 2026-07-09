# 开发日志(devlog)— 阶段索引

文档处理管线 → audit-ai monorepo → 制度查询智能体(功能1)的**按时间轴阶段索引**。
**本文件已瘦身为索引**:每阶段只留 名称 + 日期 + PR + 一句产出 + 指针;**决策/踩坑的 git 给不了的细节已下沉到各模块 `*_devlog.md`**(见 `CLAUDE.md`「模块开发记忆索引」),改某模块前读对应 devlog。
机械叙述(改了哪些文件 / 测试数 / 迁移号 / 加了什么回归测试)一律以 git log/diff 为准,本文不复述。
> 规格:`file-processing-workflow-docs/SPEC*.md` / `PLAN*.md` / `TASKS*.md`;查询侧 SDD:`query-agent-docs/`;升格:`migration_devlog.md` + `CP-009`;上游生产设计:`文档处理与语料库构建_技术框架设计_v1.6.md`、`制度查询与制度比对智能体_RAG技术框架设计_v1.5.md`。

## 工作方式(贯穿全程)

spec-driven:`SPEC` → `PLAN`(分阶段)→ `TASKS`(任务级验收)→ 逐模块 TDD 实现 → 验证(pytest + ruff,集成测连真栈)→ 停下等用户审批 → 下一个。
- **决策逐个用问答定,不打包**;用户常自带技术方案(如页码对齐、双模式),给空间先听其思路。
- 起步范围 = M1(骨架 + light 解析跑通 S0–S5);M2/M3/查询侧/V16/P0 逐轮追加,各停门控。
- 分工(Claude 规划+实现 ↔ Codex 审查)、测试职责、审查修复闭环 — 见 `CLAUDE.md`「开发协作流程」(已固化,不在此复述)。

## 关键决策记录(按时间,带 why;细节见模块 devlog)

1. **范围 = M1 起步**(验收 V1/V2/V4/V5),M2/M3 后续。
2. **嵌入默认本地 FlagEmbedding(BGE-M3)**,endpoint 留 env(env 桩 fail-fast 在 `__init__`)→ `index_devlog`。
3. **fixtures = 真下载外规 + 自拟内规 + 脚本构造坏样例**(`build_fixtures.py --all` 一键重建)。
4. **页码 = 规范渲染件 + 文本对齐**(用户提出):不猜 docx 分页,soffice 渲染 PDF 作页码权威,结构从 docx XML 抽,`page_align` 单调两指针回填 → `parsing_devlog`。
5. **检索遇阻显式退化 dense-only**(不静默,schema 不变)→ `index_devlog`。
6. **Milvus 独立 collection**(不与查询 demo 共库)。
7. **batch02 用真实修订对**(信息披露 182→226 + 官方修订说明,226 声明替代 182)。
8. **chunker 条内尾块合并**(`target_token_min` 原是死参)→ `structuring_devlog`。
9. **clause_tree bis/.1b 接通**(normalize 早支持、classify 入口够不着)→ `structuring_devlog`。
10. **单段超长条语义边界拆 + 硬切兜底(标 oversize),token_count 量内容** → `structuring_devlog`。
11. **META_REVIEW 双模式(A 默认 / B-严)**:闸的本意是权威边界担责,非抓冲突;"无冲突"≠"正确" → `metadata_devlog`。
12. **查询侧弃 WeightedRanker、选择性 sparse token 提权**(RRF 基于秩无法表达通道权重)→ `query_devlog §5.4`。
> 决策 1–10 = 摄取侧 M1;11 = 阶段 W;12 = 查询侧 SPARSE。后续轮次决策(consumed-when-present、防注入参数化 SQL、不出裸结论三重保障…)见对应模块 devlog。

## 阶段索引

### 摄取管线(M1–M3 + V16 + P0)
| 阶段 | 日期/PR | 一句产出 | 详见 |
|---|---|---|---|
| A 底座 | M1 | config/ir/states/pg(0001)/milvus/objectstore/pg_io | contracts / index / orchestration devlog |
| 流 L 纯逻辑 | M1 | normalize / clause_tree / chunker(chunk_id)/ page_align | structuring / parsing devlog |
| 流 P/SP fixtures+渲染 | M1 | 真外规下载 + 自拟内规 + 坏样例 + soffice 渲染对齐 | parsing devlog;git |
| B 接入到质检 | M1 | orchestrator / s0 登记去重 / light_parser / s1 / s2 七指标 / queue / CLI | orchestration / parsing / qc devlog |
| C 结构化·元数据·向量化 | M1 | s3 切块 / s4+L1 元数据 / version_chain / 嵌入 / milvus / s5 / search·meta CLI | structuring / metadata / index devlog |
| D 版本切换·幂等·报告 | M1 | finalize 原子切换 / idempotency / reprocess / report / M2 占位 | index(finalize)/ eval(report)devlog |
| 检查点 D 走查 | M1 | batch01 = 9 INDEXED+1 DEGRADED+1 QUARANTINED;修指标3 插入条 + 跨法引用 + 做全小数 | qc / structuring devlog |
| M2 验证套件 | M2 | T2 smoke / T4 replay / reconcile / rebuild / golden / finalize 跑 T2·T4 留痕 | eval / structuring(golden)devlog |
| M3 E1 义务打标 | M3 | 零-LLM 正则义务打标(探针定词表)+ report 全量打磨;V8 | enrich / eval devlog |
| W Web 工作台 + 双模式 + 目录区域化 | 2026-06;PR #1 另分支 | demo-web(thin shell)+ META_REVIEW 双模式 + clause_tree 目录区域化 | web / metadata / orchestration / structuring devlog |
| V16 生产 v1.6 保真 | 2026-06-18/19;PR #4 | Milvus/PG/manifest v1.6 全字段(0005–0007)+ 四类语料 profile 路由 + E2 LLM 打标 + cases 表 + 案例语料调优 | contracts / structuring / metadata / enrich / qc / index devlog |
| S 评测工具 doc_test | 2026-06-24;PR #13 | `tools/doc_test/` 真实 PDF 三目标评测(非生产包,免审合入) | git;parsing devlog(冒烟发现) |
| P0 Foundation | 2026-06-26;PR #17 | 字典/业务域迁移 + IR ocr_conf/表格 markdown(T0.1/T0.2,0009) | contracts / structuring devlog |
| P0 Phase 1 | 2026-06-26;PR #18 | 切块 internal_refs / case_ref_align / xlsx parser-only(收窄)(0010) | structuring devlog |
| P0 Phase 2 案例 L2 | 2026-06-26;PR #20 | 案例引用外规对齐 + 违规事由分类(默认关,0011) | metadata / enrich devlog |
| CP-010 P-PRESEG 预切块源适配 | 2026-07-06;feat/preseg | 源系统(法规制度平台)预切块语料+现成案例链接接入:P-PRESEG 档案(配置缝)/推导器/S0 源幂等键/S1 装载/S3 adapter/案例结构化直装(零 LLM)点亮桥接双通道/效力状态源权威/QC 哨兵化(0013–0014);决策 D1–D10 | preseg-docs/(SPEC/PLAN/TASKS/RTM/devlog)+ 调研报告 v0.2 |
| P0 Phase 2 续 业务域 L2 | 2026-06-26;PR #22 | L2 业务域多值打标 + profile 分档(P0 LLM 4 触点全清) | metadata / index devlog |
| P0 续 ref_resolver R4 | 2026-06-28;feat/ref-resolver-r4 | 跨文档指代三级查(文号→标题→dict_aliases 别名)+ 四态 standoff(resolved/ambiguous/pending_target/unresolved)+ R3/R4 span 去重(零迁移) | structuring devlog |
| P0 续 扫描件 OCR | 2026-06-29;feat/ocr-mineru | MinerU pipeline 后端(in-process do_parse → IR + ocr_conf + 表格)+ s1 路由(扫描件/图片→OCR,PIPELINE_OCR_BACKEND 默认关向后兼容)+ 白名单 jpg/png;spike risk-first(multiprocessing spawn 约束)+ 零迁移 | parsing devlog |
| CP-010 P-PRESEG 实施(T1–T12) | 2026-07-06;worktree audit-ai-preseg,branch feat/preseg(**未合并**) | 甲方源系统预切块语料全量适配(13 commits):profile 配置缝→clause_label→norm 推导器(golden 37/37)→18 列+JSONL 接收契约→迁移 0013→S0 源幂等注册+虚拟文档→S1 装载+QC 哨兵化→块流适配器→s5 尊重源效力状态权威→案例结构化直装点亮 CP-007 双通道桥接→e2e/eval 兼容;顺带修 main 既有 2 处隐性坏测(564fb55 字典换版未同步测试断言)+ r1 集成缺幂等重连(补 r3 同款补丁)。**待 Codex 审查(cwd 须切至该 worktree,origin/main...HEAD)后开 PR,尚未合并 main** | preseg_devlog.md(该 worktree)+ SPEC/PLAN/TASKS-PRESEG |

### 制度查询智能体(功能1,独立 `query/` 包;DAG `query→pipeline→common`)
> 全部细节在 `query-agent-docs/query_devlog.md`(每路一节)+ SPEC/PLAN/TASKS/RTM/GAP。本表仅时间线。
| 阶段 | 日期/PR | 一句产出 | 详见 |
|---|---|---|---|
| Q R1+R2 + 协作/记忆收口 | 2026-06-22~23;PR #5/#6/#7 | R1 依据查询(覆盖感知拒答 + 八路骨架 + LangGraph)+ R2 变更(零 LLM 条款级 diff)+ 协作流程入 CLAUDE.md + devlog 归位 | query_devlog R1/R2 |
| R R3+R6 + RTM | 2026-06-23~24;PR #9–#12 | R3 相似案例+桥接(一案一卡)+ R6 统计(防注入参数化 SQL)+ RTM 全覆盖证明 | query_devlog R3/R6 |
| R4 多文档列举 | 2026-06-24;PR #14 | enumerate 高 k + Milvus 标量/E1 义务过滤 + TABLE;`milvus_io.search` 加 extra_expr(add-only) | query_devlog R4 |
| R5 判定型(八路收官) | 2026-06-24;PR #15 | judgmental + 三段式 + 不出裸结论三重保障 + §9.2 接口;八路全实装 | query_devlog R5 |
| RERANK §5.5 | 2026-06-25;PR #16 | bge-reranker-v2-m3 重排(默认 none byte 等价);FlagReranker 不兼容 transformers 5.x→直载 | query_devlog §5.5 |
| SPARSE §5.4 | 2026-06-26;PR #19 | sparse 发文字号提权 + 词典扩展(弃 WeightedRanker);worktree 隔离 | query_devlog §5.4 |
| REVIEW §9.2 | 2026-06-26;PR #21+#23 | Kimi 忠实性复核接真模型(独立 review_model + 喂条文原文);RL-1 翻 ✅、⏳ 待真 gateway 跑绿 | query_devlog §9.2 |
| N0 多轮归并 + R7 闭环 | 2026-06-28;worktree feat/query-n0-multiturn | N0 实装(LLM 为主默认开 + 规则版离线兜底 + fail-safe)+ R7 澄清闭环(跨请求重入,非图内环);ask(history)+CLI --history-json;状态契约零改、单轮 byte 等价;真-LLM 门控⏳待跑绿 | query_devlog N0 |
| N1 HyDE 查询改写 | 2026-06-29;worktree feat/query-n1-hyde | N1 HyDE 实装(口语→假设性法言→embed(原问+法言)作 dense);Retriever 内 dense 接缝(不污染 route)、HyDE 专管 dense / sparse 归 §5.4;LLM 为主默认开 + stub no-op byte 等价 + fail-safe;仅主 retrieve(R1/R5);真-LLM 门控⏳待跑绿、默认值/召回待 §13 V0 | query_devlog N1 |
| N3 问题分解(前端收官) | 2026-06-29;worktree feat/query-n1-hyde(续 N1) | N3 实装(复合问句拆子查询→retrieve fan-out→候选并集综合);Retriever 内接缝(retrieve 重构抽 _search_candidates,单查询 byte 等价)、与 HyDE 自然组合;LLM 为主默认开 + stub no-op + fail-safe + §0.3 不迭代;仅复合触发、max_sub 封顶。**查询理解前端 N0/N1/N3 三节点收官**;门控⏳待跑绿、复合占比/质量待 V0 | query_devlog N3 |
| §9.3 Langfuse 观测(P3 首块) | 2026-06-29;worktree feat/query-observe-langfuse | Tracer 接缝(NoopTracer 默认零网络 / LangfuseTracer 懒导入 + module contextvar 串联 graph↔Retriever + flush + fail-safe 吞)+ make_tracer 离线安全;ask 包 trace(归并句/scene/route_type),Retriever 发 HyDE/子查询 event 挂同一条;**默认关 Noop byte 等价**、只读旁路(开/关 result 一致)、langfuse 可选 extra。判据:增益能力默认开、外发基建默认关。门控⏳待真 Langfuse 跑绿 | query_devlog §9.3 |
| **B-API 前端接缝(HTTP API)** | 2026-07-01;worktree feat/query-api | 参考产品原型 V3:SDD 出 SPEC/PLAN/TASKS-API(§13 决策 8 项)→ 12 任务 TDD。§10 契约**加法** structured/meta(缺省省略保 byte 等价)+ 四-Tab 装配(内规/外规/案例分区、min-max 归一、⚠缺省省略)+ 会话持久化(`query_*` 表 add-only **0012** + store)+ FastAPI 骨架/统一错误/鉴权 stub + 会话/问答(同步)/条款回查/推荐/上传/导出端点 + **真流式(两次调用:chat_json 引用 + `pipeline.llm_client` +stream 正文)→ SSE 端点**。离线 330 passed;**集成/alembic apply/真流式 gateway 门留合并前全仓门**(并行栈协调) | query_devlog 阶段 B-API + SPEC/PLAN/TASKS/RTM/GAP-API |
| audit-biz 边界二 `/v1/query`(重做,替代关闭的 PR#45) | 2026-07-07;branch fix/boundary-v1-query | 按 boundary.v1.yaml v1.1.0 无状态/无身份 `POST /v1/query`:`routes_boundary` 薄壳 over `QueryAgent.ask`(**不改内核、不动前端 `contract.py`/`export_xlsx`**——PR#45 曾捎带改形破前端契约)。`Retriever.scoped()` contextvar 把 Java 预计算 filters 下推 Milvus 前置过滤(`corpora`/`extra_expr`/`topk`/`include_superseded` 对**答案路径**生效,退出必复位);分区路由单点 `_corpora_for`(qa→P-QA、case→P-CASE、枚举仅内外规);perm_tag 走 `r4_listing.array_any_expr` 加固复用;`audit_project`→显式 422;鉴权移 `auth.require_internal_token`(常数时间+fail-closed);SSE 帧复用 `sse.format_sse`+首帧 keep-alive;citation.score 从 ask 同一次检索 collector 派生**免二次检索**。**待批**:error `B105` 回灌 biz yaml、热路径 PG 依赖措辞。query 全包 354 passed / 0 failed | BOUNDARY-v1-query-api.md + BOUNDARY-biz-contract-pointer.md |

### 升格 + 基础设施
| 项 | 详见 |
|---|---|
| audit-ai 原地升格(Step 0–7) | `migration_devlog.md` + `CP-009` + `migration-manifest.md` |
| `/clear` 自动存档 hook | `~/.claude/hooks/save-devlog-on-clear.sh`(CC 配置层,不入仓库;空 commit 门控 + 只记 git 给不了的内容) |
| worktree 游离文件污染 PR 审查(2026-06-28) | 并行 worktree 下若 agent 将文件写到错误工作树(untracked),Codex 审查该 worktree 的另一 PR 时 diff 会意外含入该文件。实例:query_devlog.md 游离在 P0 主工作树,混入业务域 PR #22 审查。**规则:提 PR 前必跑 `git status` 确认所有文件已在正确工作树且已 commit。** |
| audit-biz 姊妹仓拆分 + 后端起步路线(2026-06-30~07-01) | 后端按 v0.4 设计拆两仓(非 monorepo 合并):本仓(audit-ai,Python 推理/解析)原地不动;新建 **audit-biz**(`~/Projects/audit-biz`,Java/Spring Boot,唯一对外入口+鉴权+CRUD,甲方硬约束)。「中文沟通」/「决策点用选择题」两条 memory 提升到用户全局 `~/.claude/CLAUDE.md`(免逐仓复制),Python 专属三条留本仓。v0.4 边界文档主本迁 audit-biz(该文件在本仓从未 git 跟踪,迁移零历史包袱);本仓侧当时留了指针存根(未提交)——**核实当前 working tree 已不存在此 stub,待确认/按需补建**。**关键发现**:本仓无 v0.4 §8 所需的 biz↔ai 边界 API(`demo-web` 是内部 thin-shell 工作台;前端向的阶段 B-API `query/query/api/*` 另由 PR #39 落地会话/消息/SSE 交互面,非边界层),biz 要调的 `/retrieve` 等边界端点尚未落地 → 排序上不先做"ai web 升级"或"biz 骨架"任一方,改走 **contract-first**:先锁边界契约(Track0,已在 audit-biz 完成 SPEC/PLAN/TASKS + Checkpoint A 字段对照表冻结),再 Track A(biz,对 stub 先行,已做到 A4/Testcontainers)‖ **Track B(本仓 FastAPI 端点 B1–B4,尚未开工)**并行。B3 需给 `query/query/contract.py` 的 `Citation` 加轻量模式(细节见 `query_devlog.md` 对应条目)。完整决策/否决方案见 audit-biz 仓自身 `docs/devlog.md` + `docs/audit-biz-docs/*-BOUNDARY.md`。 |
| main CI 红排查修复 + PR 清理收尾(2026-07-02;PR#41) | 根因两层、第二层被第一层掩盖:ruff E501(`tools/doc_test/cleanup_residue.py`)挡住 pytest 从未真跑,盖住了 `qc_thresholds.yaml` 的 `page_anchor_complete_min` demo 泄漏值(PR#38 回退 demo 隔离栈配置时漏了这一个文件);两者均修,PR#41 后 main CI 恢复绿(自 PR#35 以来首次)。连带处置:关闭 PR#33(`tools/doc_test/` 语料工具链已被 `demo/corpus-bringup` 线并入 main 且更完整——diff 核对后分支 0 个文件含 main 缺失内容,强行合并是净负收益,故关闭而非 rebase)+ 直接合并 PR#32/#34(base 陈旧 behind 46、未经 Codex 审 + 未经模型门控,记欠账待干净栈补跑全仓门控,与 PR#35 那笔并计)。**环境技巧**:共享栈被占用时,本地无栈复现 CI 用 `PIPELINE_DB_DSN` 指死端口(`127.0.0.1:1`)+ Milvus 域名指 `.invalid` + 不设模型,令三类集成全 skip,可安全验证 CI-like 环境而绝不碰共享栈。 |
| CLAUDE.md/AGENTS.md 本地文件意外丢失+恢复(2026-07-03) | 810c701 `git rm --cached` 停跟踪+gitignore 后,本地工作区副本也跟着不见了(非预期——该 commit 本意只是停止跟踪、保留本地文件);当次会话因此一度只加载到 `.claude/CLAUDE.md`(CodeGraph 一节),缺了根 `CLAUDE.md` 全部项目约定。恢复用 `git show 810c701^:CLAUDE.md`(及 `AGENTS.md`)从父 commit 捞回,两文件恢复后仍在 gitignore 内。**提醒**:被 gitignore 的"本地私有"文件一旦从工作区丢失,`git status`/`git log` 都看不出"丢失"这件事本身,只能凭经验想到去改动前的父 commit 捞。 |
| `/clear` 自动存档 hook 断头修复(2026-07-03,方案 A) | 病根:SessionEnd 触发的 detached headless claude 把 devlog 摘要写盘后,提示词明确"不 commit/push",却没有任何一环负责把内容收进 git——"写盘有人管、收编没人管",导致工作区常年积压未追踪的 devlog 改动,且有被下一次 `git add -A` 裹进不相干 PR 的风险。修复(已落地并在临时仓验证 4 种场景):worker 写完后由**脚本**(非模型)做 pathspec 限定 commit,只收 `docs/devlog.md`/`docs/devlogs/`/`docs/query-agent-docs/query_devlog.md` 三个路径,不碰其余 staged 内容;detached HEAD/rebase/merge 进行中静默放弃(退回"留脏"而非出错);无变更不产生空提交。模型侧提示词未动,提交是确定性脚本行为而非模型自觉。原脚本备份 `~/.claude/hooks/save-devlog-on-clear.sh.bak-2026-07-03`。 |
| 开发流程 v2 蓝图(讨论产出,尚未落地,2026-07-03) | 复盘 PR#39(query-api 与 audit-biz 边界契约漂移,四轮 Codex 审查+337 测试全绿却"建错了东西")后诊断出 6 项结构性流程漏洞:①SPEC 前无「约束装配」阶段——现有 SDD 只验证 build-the-thing-right,不验证 build-the-right-thing(未强制枚举约束来源/真实消费者);②约束靠 agent 记忆、门靠人自觉,执行成本高的门在时间压力下必被跳且不留痕;③审查只有代码正确性/安全维度,没有架构/边界符合性维度;④知识与状态没有"刷新责任人",声明"已合并""待落地"的文档会悄悄过期(biz 仓 CLAUDE.md 曾停留在"HTTP 层尚未落地"两天,与 CLAUDE.md/AGENTS.md 意外丢失是同类"写入有纪律、失效无机制"问题);⑤多人协作靠口头不靠仓库(活分给同事后远端零 PR/零分支);⑥共享可变资源(测试栈)无租约,靠"跑前确认对方空闲"的口头纪律(对应此前 audit-demo 栈被误删事故)。提出的 v2 蓝图:入库 `docs/CONSTITUTION.md`(架构不变量/边界红线/决策权限分级,工具无关、CLAUDE.md 等只做三行薄适配)+ SPEC 增「约束装配」章(权威上游枚举+消费者清单+红线核对表)+ PLAN/TASKS 加风险登记表+门标注+ PR 模板 checklist(触边界?契约先行?devlog 已记?)+ waiver 纪律(跳门必须留记录,欠账可见)+ CI 契约门(pydantic 模型↔biz 仓 pinned `boundary.v1.yaml`)+ 知识过期定期扫描。**未实施**,落地优先级建议(若只做三件):①宪法+流程文档入库 ②PR 模板+waiver 纪律 ③CI 契约门(等 `/v1/query` 落地即上)。 |

## 已建链路(当前全貌,live reference)

`demo ingest`(s0 登记+版本关系+SHA 去重审计)→ s1(渲染+解析+page_align)→ s2(七指标质检)→ STRUCTURING 复合(s3 切块 + E1 义务打标 + s4 元数据校验)→ META_REVIEW(**A 模式**全件入 meta_confirm 人工闸;**B-严**:无冲突全新件直通、冲突件+修订件仍入闸)→ `meta confirm`(approve)→ EMBEDDING(s5 嵌入+冷备)→ INDEXING(Milvus 索引 + 翻 effective)→ INDEXED → **finalize**(带 supersedes 自动置旧版 superseded;跑 T2/T4 留痕)。
`search` 混合查出四级引用(默认 effective,`--include-superseded` 见旧版);degrade 重入索引终于 DEGRADED_INDEXED;失败件入统一队列(qc_fix/quarantine/meta_confirm 三类)经 `dispose` 处置;`demo verify smoke/replay/reconcile`、`rebuild`、`report` 出验证指标。
四类语料按 `corpus_type` profile 路由(P-INT/P-EXT 条款树、P-QA 问答对、P-CASE 要素+cases 表);E2/L2/案例 L2 LLM 富集默认关。
**检查点 A/B/C/D/M2/M3 全达成,V1–V8 全过;查询侧八路全实装。**

## 测试与运行约定

- venv:`.venv/bin/python -m pytest -q`、`.venv/bin/ruff check .`(testpaths 含 pipeline/common/eval/query)。
- 集成测连真栈、栈未起则 skip;各自按 batch_id 反 FK 序清理。**测试文件基名须全仓唯一**(pytest prepend + tests 无 `__init__.py`,撞名致收集报错)。
- 模型门控集成假定**干净栈**(SHA 去重):跑前 `demo down -v && demo up` 或清库,否则走查残留致去重撞车。**提交前必跑模型门控全量**(无模型时 skip,漏回归)。
- 真模型:`PIPELINE_EMBEDDING_MODEL` 指 modelscope 本地缓存(完整)+ `HF_HUB_OFFLINE=1`;未设则相关测试自动 skip,绝不联网。
- 迁移 add-only:autogenerate → upgrade → `alembic check` 无漂移;`alembic/versions` 纳入 ruff,autogenerate 后 `ruff check --fix alembic/versions && ruff format alembic/versions` 再提交。
- 行宽 100,ruff(E/F/I/UP/B);CJK 注释易超行 → 独立行/缩短。
- Milvus standalone 偶发瞬时 gRPC 卡死:`ps -o etime,cputime` CPU≪墙钟即卡 → `demo down -v && demo up`(详见 `web_devlog`)。
