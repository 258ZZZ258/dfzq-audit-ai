# PROMPTS

本文件集中存放**查询智能体(query)所用 LLM 提示词**(既定约定)。

**M1 默认零 LLM 调用** —— 各节点默认关或可 `stub`;仅当对应 `[query]` 开关开启且
`llm_backend = gateway` 时才构造 LLM client 并调用,关闭 / `stub` 时走规则版或 passthrough(零网络)。

> 管线侧自建 LLM 富集(E2 条款打标 / L2 业务域·摘要 / case_l2 案例引用抽取)已整段移除——
> 生产走甲方预切块源结构化元数据(preseg-only),相关提示词随模块一并删除;如需查阅历史版本见 git。

## §9.2 R5 忠实性复核(judge_multimodel_review 时启用)

制度查询智能体 §9.2 / CP-007:R5 判定型三段式 ②框定 产出后,由**独立复核模型**(Kimi,
`review_model`,与主答 Qwen `llm_model` 分离,§9.1)逐块校验「该试探性表述是否被所引条款支持」
(faithfulness)。**默认关**——仅 `[query] judge_multimodel_review = true` 且 `llm_backend = gateway`
时构造复核客户端并调用;关闭时 `query/query/judge/review.py` `review_tentative` 直接 passthrough(零网络),
「无依据结论」红线由 `framing.strip_bare_conclusion` 形态后检兜底。

**fail-closed(硬规则,LLM05)**:LLM 输出不可信——仅当 `supported` 是**严格 bool `true`** 才判支持;
缺失 / 非 bool(如字符串 `"false"` 真值为 True)/ 任何其它值 → **判不支持**,该块降「待人工核实」,
绝不让畸形响应放过踩红线的表述。**不支持 → 降级**(不触发重生成);**仅施于 R5 判定型**。

**喂条文原文(硬规则,R5-REVIEW-NEEDS-CLAUSE-EVIDENCE)**:复核证据是**所引条款原文**,非仅题名/条号——
仅靠《题名》条号无从核忠实性,复核模型须看到条文正文才能判表述是否被支持。代码以
`query/query/judge/review.py` `_supported(content, clauses, llm)` 内联拼装,`<evidence>` =
各所引条款 `《doc_title》clause_path:text`(条文原文)**每条一行**(正文缺失记 `(正文缺失)`,fail-closed 兜底)。

### system

```
你是引用忠实性复核助手。判断给定表述是否被【所引条款原文】支持,只回 JSON {"supported": true 或 false}。
```

### user

```
表述:<content>
所引条款原文:
<evidence>
该表述是否被上述条款原文支持?
```

## §3.4 N0 多轮上下文归并(merge_context 时启用)

制度查询智能体 §3.4 / CP-007:查询理解前端入口 N0。根据多轮对话历史,把用户当前问句的**指代**
(它/该制度/上面那条)消解、**省略**的制度名/业务域补全,改写为**自足问句**送下游路由/检索。
**LLM 为主、默认开**——`[query] merge_context = true`(默认)且 `llm_backend = gateway` 时构造独立
**归并模型**(`merge_model`,None → 复用主答 `llm_model`,§9.1 N0 轻量调用)真改写;`stub`/关 → 走
`query/query/understand/merge.py` `_rule_merge` **规则版确定性归并**(R7 澄清闭环 + 代词/省略顺承,离线可测)。

**fail-safe(硬规则)**:N0 失败不阻断查询——真 LLM 抛/超时/返空 `merged_query` → **回落规则版/原句**
(`merge_context` try/except);空 history → no-op 原句(单轮 byte 等价)。

**只改写不作答(硬规则,§7.1 红线)**:N0 **只改写问句,绝不回答问题、绝不生成制度名称/发文字号/条款号**——
即便 LLM 在归并时编出貌似合理的错误法言,最终答案仍只能引用检索上下文中带 `clause_id` 的内容(引用 ID 注入兜底)。
代码镜像于 `merge.py` `MERGE_SYSTEM` / `build_merge_user`。

### system

```
你是审计制度查询助手的查询改写器。根据多轮对话历史,把用户当前问句改写为**自足问句**:消解指代(它/该制度/上面那条),补全省略的制度名/业务域(接上一轮主题)。**只改写问句,不要回答问题,不要编造制度名称、发文字号或条款号。**若当前问句已自足或无从补全,则原样返回。只输出 JSON:{"merged_query": "<改写后的自足问句>"}。
```

### user

```
对话历史:
用户:<上轮 user content>
助手:<上轮 assistant content>

当前问句:<query>

改写为自足问句。
```

## §3.1 N1 HyDE 查询改写(hyde 时启用)

制度查询智能体 §3.1 / CP-007:查询理解前端 N1。口语问句与法言条款词面断层、直接检索召回低,先让 LLM
写 1–2 句**假设性法言条款**(HyDE,Gao et al. 2022/2023),与原问拼接后 embed 作 **dense 向量**送混合检索,
缩小术语断层。**LLM 为主、默认开**——`[query] hyde = true`(默认)且 `llm_backend = gateway` 时构造独立
**HyDE 模型**(`hyde_model`,None → 复用主答 `llm_model`,§9.1 N1 轻量调用)真改写;`stub`/关 → `hyde_llm` 不建 →
`retrieve()` 用原问 dense(no-op、byte 等价)。**只改 dense**——sparse 法言扩展归 §5.4 dict 桥接,HyDE 不碰 sparse。

**fail-safe(硬规则)**:HyDE 失败不阻断检索——真 LLM 抛/超时/返空 `passage` → **回落原问 dense**
(`retrieve/hyde.py` `hyde_dense_text` try/except → None)。仅主 `retrieve`(R1/R5);R3/R4 不接。

**只写法言不作答(硬规则,§7.1 污染兜底)**:HyDE **只写假设性法言条款,绝不回答问题、绝不生成发文字号/条款号**——
即便编出貌似合理的错误法言,最终答案仍只能引用检索上下文中带 `clause_id` 的内容(引用 ID 注入),HyDE 错误**不污染答案**。
代码镜像于 `hyde.py` `HYDE_SYSTEM` / `build_hyde_user`。**默认值终定待 §13 V0 第5组 A/B 实测(§15-⑦)。**

### system

```
你是审计制度检索助手。针对用户的口语化问句,写出 1–2 句**假设性的法言法语条款表述**(模拟可能命中的制度条款原文风格),用于提升向量检索召回。**只写假设性条款表述,不要回答问题、不要编造发文字号或条款编号、不要加解释。**只输出 JSON:{"passage": "<1–2 句假设性法言条款>"}。
```

### user

```
口语问句:<query>

写出假设性法言条款表述。
```

## §3.3 N3 问题分解(decompose 时启用)

制度查询智能体 §3.3 / CP-007:查询理解前端 N3。**显式复合问句**(含多个并列子约束,如「同时管偏股+偏债
是否违规」)单向量检索难同时命中各子约束,先让 LLM **一次性**拆为 2–N 个独立子查询,`retrieve()`(R1/R5)对每子
查询 fan-out 检索、候选**并集**后综合(plan-execute 拆分,LangChain/LangGraph 主流)。**LLM 为主、默认开**——
`[query] decompose = true`(默认)且 `llm_backend = gateway` 时构造独立**分解模型**(`decompose_model`,None→复用
`llm_model`);`stub`/关 → `decompose_llm` 不建 → `_subqueries_for` 返 `[query]`(单查询 no-op、byte 等价)。仅主
`retrieve`(R1/R5);R3/R4 不接。

**不进 agentic 循环(硬规则,§0.3)**:分解只做**一次性**子查询拆分,**不迭代推理**(无 plan→retrieve→reason→
re-retrieve→synthesize);子查询并行检索后综合一次。`decompose_max_sub`(默认 4)封顶 fan-out 成本。

**fail-safe(硬规则)**:LLM 抛/超时/返空/单跳(拆出 ≤1)→ **回落 `[query]` 单查询**(`retrieve/decompose.py`
`decompose_subqueries` try/except),绝不阻断检索。

**只拆不作答(硬规则,§7.1 污染兜底)**:**只拆分改写,绝不回答问题、绝不编造制度名称或条款号**——子查询是检索
改写、不产 `clause_id`,即便错误拆分,最终答案仍只引检索上下文带 `clause_id` 者,不污染答案。代码镜像于 `decompose.py`
`DECOMPOSE_SYSTEM` / `build_decompose_user`。**复合占比/拆分质量待 §13 V0 实测。**

### system

```
你是审计制度查询助手的问题分解器。判断用户问句是否为**复合问句**(含多个并列子约束,如「同时管偏股+偏债」「A 和 B 是否都需要」)。若是,拆为 2–N 个独立子查询,每个聚焦一个子约束;若是单一问句,只返回一个。**只拆分改写,不要回答问题,不要编造制度名称或条款号。**只输出 JSON:{"subqueries": ["<子查询1>", "<子查询2>", ...]}。
```

### user

```
问句:<query>

判断是否复合问句,拆为子查询。
```
