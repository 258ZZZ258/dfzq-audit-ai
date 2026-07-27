# 甲方源库仿真环境(mock source)

甲方内网法规制度平台(达梦 DM8)源库的**本地可跑替身**:8 表结构逐列复刻 + 按真数据探查的
值域/分布造数,让 `pipeline/preseg_export.py` 的 `DmSource` 这条 SQL 路径**不必等内网只读账号**
就能端到端联调。

```bash
# 起库(独立 compose project,端口 5434,不碰主栈 5432 / audit-demo 5433)
docker compose -f tools/mock_source/compose.mock-source.yaml up -d

# 建表 + 造数(默认 300 部法规,约 1/50 真库;--edge-cases 追加定向边缘样本)
.venv/bin/python tools/mock_source/seed.py --laws 300 --edge-cases

# 端到端导出(preseg_export.py 未改路径)
PRESEG_SOURCE_DSN='postgresql+psycopg://dcetl:dcetl@127.0.0.1:5434/dcetl' \
    .venv/bin/python -m pipeline.preseg_export /tmp/batch-mock

# 一键验收:造数 → 导出 → 断言 13 个边缘分支各自落对
.venv/bin/python tools/mock_source/verify.py

# 停库(-v 连数据一起删)
docker compose -f tools/mock_source/compose.mock-source.yaml down -v
```

---

## 这个东西**不是**什么:它没有复刻甲方的 ETL 管道

甲方源库里的 `TB_INFA_PUSH` / `TB_INFA_PUSH_INFO` 是 Informatica 工作流的**执行日志表**
(哪次 run 加载了哪张表、成功多少行、几点起止),**不是管道定义**。手上全部资料
(`东方/东方知识库/`:一份 497 行的 `dcetl_schema_mermaid.md` 照片 + 我方三份探查 SQL 的结果照片)
只覆盖**目标端结构**与**数据形态**,不含任何转换逻辑。

复刻真正的 ETL 管道所缺的东西,一项都没有:

| 缺口 | 说明 |
|---|---|
| 源系统标识 | `SOURCE_SYSTEM` 只有列定义,无一行实际值;法规原文的最上游来源(采购库/爬虫/人工录入)未知 |
| 字段映射 | source column → target column 的 mapping,零信息 |
| 转换规则 | Snowflake `ID` 与业务 `CODE` 的生成算法、`PATH_CODE` 层级编码规则、`DEL_FLAG`/`DATA_VERSION` 维护策略 |
| 加载策略 | 全量/增量、CDC 方式、水位、幂等与去重键 |
| 调度 | workflow 依赖、频率、重试;Informatica mapping XML / workflow 定义 |

一个佐证:探查发现的几个现象——**每个逻辑节点恒 2 物理行**、`SOURCE_LAW_ID` 全空串、
`NEW_CODE`/`ABOLISH_CODE` join 回 `LAW_BASIC.CODE` 命中 0、3,736 个空串桥接锚——都是那条
ETL 管道的产物或缺陷,而**没有任何材料能解释它们为什么长这样**。解释不了成因,就谈不上复刻。

本目录做的是**产物端复刻**:让源库的"样子"在本地可得,供我方 puller/转换脚本开发验证。
`tb_infa_push*` 两表照建并灌了一条示例 run 记录,是为结构完整 + 未来若做增量拉取可按
`WORKFLOW_RUN_ID`/`BUSI_DATE` 定水位 —— 不代表还原了甲方的调度。

---

## 真值域出处(改造数前必读)

`seed.py` 顶部的分布常量**不是编的**,逐条锚定 2026-07-14/15 达梦真库探查
(脚本:`东方/东方知识库/preseg_explore{,2,3}.sql`;结果照片:同目录 `sql脚本结果{,2,3}/`)。

| 常量 / 造数口径 | 探查段 | 真值 |
|---|---|---|
| 各表规模 | F2 | LAW_BASIC 15,305 · LAW_CONTENT 655,703 · CASE_BASIC 4,695 · **CASE_PARTY 0** · CASE_PUNISH 55,299 |
| `STATUS_DIST` | A2 | inuse 9,795 / abolish 3,815 / modified 1,291 / draft 382 / pending 19 / test_run 3 |
| `SCOPE` 恒 0 | A1 | 全库 15,305 部 SCOPE=0(全外规);fail-closed 仍作跨环境兜底 |
| `SUIT_OBJ_DIST` | A4 | 空 5,868 / 证券 4,626 / 通用 2,570 / 期货 1,159 / 基金 635 / 其他金融机构 317 / 顿号多值 129 / **竖线多值 1** |
| `LEVELS_DIST` | A5 · I3 | **JSON 数组串**,24 种取值;最长组合 79 字符 |
| `TAG` 恒空串 | F3 | 15,305 行 TAG 全为空串(非 NULL) |
| 版本链三字段恒空串 | E1 · H2 · H3 | 100% 非空,但 `SOURCE_LAW_ID` 全空串、`NEW_CODE`/`ABOLISH_CODE` join 命中他法 **0** |
| 每节点 2 物理行 | B2 · G1 · G2 | 同 `(LAW_CODE, INDEX_NO)` 恒 2 行,**同 CODE、异 snowflake ID、内容一致** |
| `PATH_CODE` 形态 | B1 | 点分祖先 CODE 路径,每段 opaque(`ELA7…`+3 位序号=39 字符);人类编号在 `TITLE` |
| 桥接命中率 | C1 · I2 | 未命中 3,737,其中 **3,736 是空串锚**(`null_anchor=0`)、仅 1 条悬空 |
| `PUNISH_LAW` 语义 | C2 | `PUNISH_LAW`=法规名、`PUNISH_LAW_TITLE`=条款标识,**与 dcetl 文档描述相反,以数据为准** |

**表结构**(列名/类型/列宽)出自甲方机器上 `~/Documents/dcetl_schema_mermaid.md` 的照片
(`东方/东方知识库/图片/IMG_1109–1123`)。⚠ 用户整理的 `知识库结构.md` 是**有损精简版**
——漏了 `SCOPE`、`SUIT_OBJ_CODE`、`HAS_CONTENT`、`ABOLISH_CODE`、`NEW_CODE`、`EXT_01-03`
以及全部列宽,**不可作为建表依据**。

---

## 保真优先于"好看"

以下几处看着像 bug,是**故意复刻的**——掩盖它们等于让联调在仿真上绿、在真库上炸:

- **每个逻辑节点写 2 物理行**:`export.blocks_from_contents` 的去重与冲突拒收逻辑,只有在有
  重复行时才被真正执行。
- **空串而非 NULL**:桥接锚、`TAG`、版本链字段在真库都是 `''`。`WHERE x IS NULL` 与
  `WHERE x = ''` 在这里是两回事,写错了在仿真上就能抓到。
- **`CASE_PARTY` 0 行**:真库该表空,`persons[]` 通道在真数据上永远空转。默认不造数据,
  要测那条通道得显式 `--with-parties`(并且知道自己在测一条真库上没数据的路径)。
- **8 表无主键无外键**:源库如此。加唯一约束就造不出重复行了。

---

## 列宽边界的可达性(一个实施期发现)

`preseg/export.py` 的 `_COL_WIDTHS` 定义了落 PG 前的边界校验。把它与源列宽对照后,
有些分支在真 schema 下**不可能触发**:

| 目标字段(列宽) | 源列(列宽) | 可达? |
|---|---|---|
| `title` 512 | `LAW_BASIC.NAME` 400 | ✗ 源比目标窄,越界造不出来 |
| `source_code` 256 | `LAW_CONTENT.CODE` 256 | ✗ 等宽 |
| `source_law_id` 256 | `SOURCE_LAW_ID` 256 | ✗ 等宽 |
| `sub_type` 32 | `LEVELS` 4000 | ✓ **真数据里就有 17 部超宽**(JSON 数组多值组合达 79 字符) |
| `issuer` 128 | `ISSUE_AUTH_CN` 4000 | ✓ |
| `doc_number` / `file_no` 128 | `DOC_NO` 2000 | ✓ |

**`sub_type` 那行值得注意**:真库有 17 部法规的 `LEVELS` 是多值 JSON 组合(如
`["SECURITIES_ASSOCIATION","FUTURES_ASSOCIATION","ASSET_MANAGEMENT_ASSOCIATION"]`,79 字符),
超 `sub_type` 列宽 32 → 按现行 fail-closed 口径**整件拒收**。占比 0.11%(17/15,305),
但这是真库上会真实发生的拒收,不是假想。`--edge-cases` 的 `levels-overflow` 样本定向覆盖它。

> 顺带:`LEVELS` 存的是 JSON 数组串,现在被原样塞进 `sub_type` / `issuer_level_src`。
> 是否该解析出数组元素再落库,属口径问题,**未在本次改动范围内** —— 记录在此备议。

---

## 与真库的已知差异(联调结论的适用边界)

用 PG 冒充达梦,是拿方言覆盖换零改造和可移植性。以下差异**仿真验不出来**,仍需内网真库验证:

1. **SQL 方言**:达梦特有语法(`ROWNUM` 等)在 PG 上行为不同。`DmSource` 当前的 SQL 是纯
   `SELECT/WHERE/ORDER BY`,不含方言相关构造 —— 这是 PG 替身成立的前提。**日后往 `DmSource`
   加达梦特有语法,这个前提就破了**。
2. **列名大小写**:达梦返回大写键,PG 返回小写。`DmSource._rows` 统一 `upper()` 归一
   (幂等,对达梦无副作用),差异被这层吸收。
3. **日期返回型**:达梦驱动可能返回带时分秒的 `datetime`,PG 返回 `date`。`export._date`
   两种都处理,但**真实返回型只能在真库确认**。
4. **隔离级**:`preseg_export.run` 要求 `REPEATABLE READ`,PG 原生支持;达梦驱动能否设置、
   语义是否一致,待真库验。
5. **性能**:仿真 300 部 / 1.8 万行,真库 15,305 部 / 65.6 万行(约 36 倍)。
   `contents_for` 是逐法规查询(N+1),真库规模下的耗时**未验证** —— 这正是拿到只读账号后
   要优先测的。
6. **内容真实性**:正文是模板生成的合成文本,不能用来评估切块质量、检索召回或条款树 F1。
   那类评估必须用真语料。

---

## 相关

- 转换脚本:`pipeline/pipeline/preseg/export.py`(纯转换)+ `pipeline/pipeline/preseg_export.py`(`DmSource` + 入口)
- 契约与决策:`docs/preseg-docs/SPEC-PRESEG.md`、`docs/preseg-docs/preseg_devlog.md`
- 内网对齐记录:`docs/predeploy-docs/predeploy_devlog.md`
