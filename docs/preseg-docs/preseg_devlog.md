# preseg 模块开发记忆(CP-010 · 决策/踩坑/口径钉子)

> 只记 git 给不了的。SDD 产物:SPEC/PLAN/TASKS/RTM-PRESEG 同目录;决策 D1–D10 主本在
> 《调研报告》v0.2 §6(docs/制度查询智能体_预切块数据源适配_调研报告_v0_2.md)。

## 2026-07-06 T1–T12 实施(worktree feat/preseg)

### 口径钉子(跨任务,后来者必读)

- **corpus_type 双重身份拆分**:`corpus_type` 同时是 Milvus 分区/检索归属(B6)与 profile 分派键
  ——preseg 语料若落自立分区会**脱出主检索**。定案:manifest/documents 仍填 `P-INT/P-EXT/P-CASE`
  (分区语义);preseg **通道性**由 `dv.source_format=="preseg"`+扩展列标识;**QC 档案键**才用
  `P-PRESEG`(s2 选 profile_key 与 corpus_type 解耦)。SPEC §3.2 据此精化。
- **效力状态映射在 S0 应用**(PLAN 原划 S4):manifest 原值仅 S0 可见,S4 读 PG 拿不到;
  T8 改辖 s5 侧——`version_chain.resolve_live_status`:源权威件(`version_status_source=="source"`)
  保源值,否则 `live_status` 推导。**否则 s5 会把源说"已废止"的历史件翻回 effective**。
- **IR 只服务 QC**:s1 preseg 分支合成 IR(paragraph 块,page=None)仅供⑥文本质量哨兵;
  **s3 直接消费 raw 块流**(IR `extra="forbid"` 承载不了 clause_label,也不该承载——搬运保真)。
- **搬运保真**:源块文本原样入 chunk(不加面包屑前缀、不改写);案例摘要用"问题汇总"人工文本
  (替代规则截断版);对源内容的一切"修"都归源系统(单向只读推论)。

### 否决的方案(为什么没走)

- ❌ 独立直采通道(方向 B):eval/reprocess/rebuild 对状态机外数据行为未定义,工具链双轨债;
  与 B8"配置缝替换"哲学冲突。
- ❌ preseg 落自立 Milvus 分区:脱出主检索(见口径钉子)。
- ❌ SQL→Milvus 直投(跳过我们 PG):破 B2 重建权威/引用回查/回滚单元;PG=权威时点快照,
  Milvus=可丢弃索引(D9 明确"必经 PG")。
- ❌ 元数据完整率作 QC 门:源的元数据缺漏我们无权修,拦=制造覆盖缺口(D10,降报告项)。

### 踩坑(非显然,会再踩)

- **`Pattern.match(s, pos)` 下 `^` 恒锚串首**:pos>0 必失配——推导器章/节/条顺序消费的正则
  不能带 `^`(match 本身锚定 pos)。
- **既有 `case_ref_align._clause_no` 边界 bug**:「第X条之一第Y款」中「条之→之」合形消掉了
  款尾剥除锚点「条」→ 补 `split("第",1)[0]`;普通条零影响(case 通道 84 passed 回归)。
- **`ruff format alembic/versions` 会重排既有迁移文件**:autogenerate 后只 format 新迁移,
  否则 diff 混入历史文件噪声(T4 时剔除过)。
- **openpyxl 空单元格**:`_read_manifest` 把 None 归 ""——preseg 行级校验按 str 强转后 strip 判空。

### 遗留(接缝已留好)

- **T5.5 SQL puller**:待甲方库 schema/只读账号(内网部署测试时拿);落点=只读 session 按水位
  query → 产批次快照 → `register_preseg_batch` 原路。**跨仓边界哨**:v0.4 §2 把"外部数据源
  接入"划给 biz——先 ai 侧推进,若 biz 收口只挪 puller。
- `preseg_raw` 伪路径批次占比告警阈值 ⚠ 待真样例标定(Ask-first)。
- `issuer_level_src` → Milvus INT8 序:暂走既有字典派生,独立映射待值域(SPEC §9-2)。
- 效力状态映射表值域 ⚠ 工程假设,真导出到手须校订(Ask-first 增删)。
- 源库↔PG 拉取对账(D10 新增项)落在 puller 批次报告,随 T5.5。

## 2026-07-27 源库仿真环境(`tools/mock_source/`)

甲方源库(达梦)只在内网、只读账号未到手 → `DmSource` 那条 SQL 路径**从未连过任何库**
(只有 `FakeSource` 单测)。建 PG 仿真替身把它跑起来,不必等账号。

### 决策:PG 冒充达梦(否决达梦 docker)

- ✅ **PG + 逐列复刻的 8 表**:`DmSource` 的 SQL 是纯 `SELECT/WHERE/ORDER BY`,无达梦特有构造
  → 原样可跑;`create_engine` 从 DSN 前缀推方言 → 把 `PRESEG_SOURCE_DSN` 指向仿真库即可,
  **preseg_export.py 的查询逻辑零改**。
- ❌ **达梦官方 docker**:镜像 x86 且需注册下载,ARM Mac 跑不起来;为覆盖方言差异付不成比例的代价。
  **代价记账**:方言/日期返回型/隔离级/性能(真库 36 倍规模,`contents_for` 是 N+1)这四类
  仿真验不出,仍须内网真库验 —— 已写进 README「与真库的已知差异」,别把仿真绿当成真库绿。
- ❌ **只造 fixtures 不建库**:那是既有 `FakeSource` 的覆盖面,验不到 SQL 层(列名大小写、
  多语句 DDL、驱动行为)—— 而这次三个坑全在 SQL 层。

### 口径钉子:仿真必须复刻"像 bug 的东西"

真库那些反直觉形态(每逻辑节点 2 物理行、空串而非 NULL 的桥接锚、全空串的版本链字段、
`CASE_PARTY` 0 行)**一律照抄**。掩盖任何一个,受它保护的代码路径(去重/冲突拒收、
`= ''` vs `IS NULL`)在仿真上就永远走不到 → 仿真绿、真库炸。

### 发现:`_COL_WIDTHS` 的边界有真/假之分

把 `preseg/export.py` 的 `_COL_WIDTHS` 与源列宽对照后,部分 `_bound` 分支在真 schema 下
**不可达**(源列比目标窄,越界数据造不出来):`title` 512 ← `NAME` 400、`source_code` 256
← `LAW_CONTENT.CODE` 256(等宽)、`source_law_id` 256(等宽)。真正可达的是
`sub_type` 32 ← `LEVELS` 4000、`issuer` 128 ← `ISSUE_AUTH_CN` 4000、`doc_number` 128 ← `DOC_NO` 2000。

> ⚠ **待决(未在本次改动范围内)**:`LEVELS` 存的是 **JSON 数组串**(`["NATIONAL_LAWS"]`),
> 被原样塞进 `sub_type`(列宽 32)。真库 17 部法规的 `LEVELS` 是多值组合(最长 79 字符)
> → 按现行 fail-closed 口径**整件拒收**,占比 0.11%(17/15,305)。是"该解析数组元素再落库"
> 还是"确认可拒收",属口径问题,需决策方拍板。`--edge-cases` 的 `levels-overflow` 样本已定向覆盖。

### 踩坑(SQL 层,会再踩)

- **`DmSource._rows` 隐含依赖大写列名**:转换层按 `law.get("CODE")` 取值;达梦返回大写键,
  **PG 把未加引号标识符 fold 成小写** → 不归一则所有 `get("CODE")` 静默返回 None,
  导出一批"字段全空但结构合法"的批次(比报错更坏)。已加 `{k.upper(): v}` 归一
  (对达梦幂等)。键的大小写由驱动/方言决定,**不是契约**。
- **psycopg 把 SQL 里的裸 `%` 当参数占位符**:`schema.sql` 注释里一个"100%"就让整份 DDL
  报 `ProgrammingError: incomplete placeholder`。DDL 文件内避免裸百分号。
- **SQLAlchemy `text()` 不收多语句**:多语句 DDL 走 `conn.exec_driver_sql()`(同时绕开
  `text()` 对 `:` 的参数解析)。

### 验收

`tools/mock_source/verify.py` 一键跑通:造数 → 导出 → 断言 13 个定向边缘样本各自落对
(7 拒收/跳过 + 6 通过且字段正确)+ 桥接锚落到 blocks/cases。随仿真数据或 export 口径漂移即红。
