-- =====================================================================
-- 甲方源库(法规制度平台,达梦 DM8)8 表结构 —— PostgreSQL 仿真版
--
-- 字段/类型/列宽逐列抄自甲方机器上 `~/Documents/dcetl_schema_mermaid.md`
-- (照片存档:东方/东方知识库/图片/IMG_1109–IMG_1123)。
-- ⚠ 用户整理的 `知识库结构.md` 是**有损精简版**(漏 SCOPE / SUIT_OBJ_CODE / HAS_CONTENT /
--   ABOLISH_CODE / NEW_CODE / EXT_01-03 / 列宽等),**不可作为建表依据**——本文件以照片为准。
--
-- 达梦 → PG 类型映射(语义等价,不影响 DmSource 的 SQL):
--   VARCHAR(n)        → VARCHAR(n)      列宽保真(export.py 的 _bound 边界校验靠它)
--   TEXT (CLOB 别名)  → TEXT
--   TIMESTAMP(36,6)   → TIMESTAMP(6)    微秒精度
--   INTEGER(10)/INT(10) → INTEGER
--   NUMBER(动态精度)  → NUMERIC
--   CHAR(4)           → CHAR(4)
--
-- **无物理主键 / 无外键**(源库如此,dcetl §4.1)——关系全靠应用层 CODE 字段维护。
-- 仿真库刻意保持这一点:任何"顺手加个主键"都会掩盖源库真实的重复行/悬空引用问题。
--
-- **列名小写**:PG 对未加引号标识符 fold 成小写,DmSource 的 `SELECT CODE, NAME ...`
-- 同样被 fold → 能匹配。返回的 dict 键因此是小写,由 DmSource._rows 统一 upper()
-- 归一(见 preseg_export.py 注释)。
-- =====================================================================

DROP TABLE IF EXISTS tb_infa_push;
DROP TABLE IF EXISTS tb_infa_push_info;
DROP TABLE IF EXISTS znfg_iam_law_basic;
DROP TABLE IF EXISTS znfg_iam_law_content;
DROP TABLE IF EXISTS znfg_iam_law_content_detail;
DROP TABLE IF EXISTS znfg_iam_law_case_basic;
DROP TABLE IF EXISTS znfg_iam_law_case_party;
DROP TABLE IF EXISTS znfg_iam_law_case_punish;


-- ========== 1. ETL 基础设施(Informatica 推送日志;dcetl §2)==========
-- ⚠ 这两张表是甲方 ETL 工作流的**执行日志**,不是管道定义。转换逻辑(源系统标识、
--   字段映射、CODE/PATH_CODE 生成规则、增量水位、调度)不在任何已知资料里。
--   建它们是为了结构完整 + 未来若做增量拉取可按 WORKFLOW_RUN_ID / BUSI_DATE 定水位。

CREATE TABLE tb_infa_push (
    busi_date         VARCHAR(8)   NOT NULL,  -- 业务日期 YYYYMMDD
    source_system     VARCHAR(240) NOT NULL,  -- 源系统标识(真值未知)
    etl_date          VARCHAR(30),            -- ETL 执行时间串
    start_datetime    TIMESTAMP(6),
    end_datetime      TIMESTAMP(6),
    etl_status        NUMERIC,                -- ETL 状态码
    flag              CHAR(1) DEFAULT '0',    -- 处理标记 0=待消费 1=已完成
    workflow_run_id   NUMERIC,                -- Informatica workflow 实例 ID
    get_time          TIMESTAMP(6)            -- 记录抽取时间
);

CREATE TABLE tb_infa_push_info (
    busi_date         VARCHAR(8),
    schema_name       VARCHAR(50),            -- 目标 schema
    table_name        VARCHAR(500),           -- 目标表
    etl_date          VARCHAR(30),
    etl_status        NUMERIC,
    start_time        TIMESTAMP(6),
    end_time          TIMESTAMP(6),
    successful_rows   NUMERIC,
    failed_rows       NUMERIC,
    trans_errors      NUMERIC,
    workflow_run_id   NUMERIC                 -- 关联 tb_infa_push
);


-- ========== 2. 法规主表(dcetl §3.1)==========
-- 真数据(2026-07-14 探查):15,305 行;SCOPE 全 0(全外规);TAG 全空串;
-- SOURCE_LAW_ID / NEW_CODE / ABOLISH_CODE 全部非空但无一指向他法(占位空串)。
-- ⚠ 本文件内避免裸百分号:psycopg 会把它当参数占位符,整份 DDL 直接报 ProgrammingError。

CREATE TABLE znfg_iam_law_basic (
    etl_src_date      INTEGER,                -- ETL 来源加载日期
    etl_src_code      CHAR(4),                -- ETL 来源系统码
    id                VARCHAR(256),           -- Snowflake 唯一 ID
    old_id            VARCHAR(256),
    scope             INTEGER,                -- 0=外规 1=内规 2=标准(分类权威,fail-closed)
    code              VARCHAR(180),           -- 业务唯一码,真值形如 ELA7+32hex(36 字符)
    name              VARCHAR(400),
    doc_no            VARCHAR(2000),          -- 文号
    issue_auth_cn     VARCHAR(4000),          -- 发布机关
    issue_auth_code   VARCHAR(4000),
    suit_obj_code     VARCHAR(180),           -- 适用对象(中文多值,顿号/竖线混用)
    issue_date        DATE,
    invalid_date      DATE,
    effect_date       DATE,
    status_code       VARCHAR(180),           -- inuse/abolish/modified/pending/draft/test_run
    modify_info       TEXT,                   -- 修订历史
    forbidden_msg     TEXT,
    source_law_id     VARCHAR(256),           -- 版本链候选(真数据全空串)
    levels            VARCHAR(4000),          -- 法规层级,**JSON 数组串** ["NATIONAL_LAWS"]
    tag               VARCHAR(256),           -- 版本标签(真数据全空串)
    create_time       TIMESTAMP(6),
    update_time       TIMESTAMP(6),
    del_flag          VARCHAR(4),             -- A=有效 D=删除 U=修改
    has_content       INTEGER,                -- 是否有正文
    data_version      VARCHAR(128),
    owner             INTEGER,
    source            INTEGER,
    new_code          VARCHAR(512),           -- 版本链候选(真数据 join 命中 0)
    creator_id        VARCHAR(64),
    updator_id        VARCHAR(64),
    ext_01            VARCHAR(250),
    ext_02            VARCHAR(250),
    ext_03            VARCHAR(250),
    abolish_code      VARCHAR(2048)           -- 版本链候选(真数据 join 命中 0)
);


-- ========== 3. 法规目录/章节/条文(dcetl §3.5)==========
-- 真数据:655,703 行。**每个逻辑节点恒 2 物理行**(同 CODE、异 ID、内容一致)——
-- 源 ETL 的产物特征,成因不明(资料无转换逻辑)。export.py 按 CODE 去重 + 内容冲突拒收。
-- PATH_CODE = 点分的**祖先 CODE 路径**(每段是 opaque 哈希 ID,不含可读编号);
-- 人类编号("第X条"/"第一章 xxx")在 TITLE 里 —— 故 clause_path_norm 从 TITLE 派生。

CREATE TABLE znfg_iam_law_content (
    etl_src_date      INTEGER,
    etl_src_code      CHAR(4),
    id                VARCHAR(256),
    old_id            VARCHAR(256),
    code              VARCHAR(256),           -- 条款唯一码 = law_code(36) + 3 位序号 = 39 字符
    law_id            VARCHAR(256),           -- FK → znfg_iam_law_basic.id
    law_code          VARCHAR(180),           -- FK → znfg_iam_law_basic.code
    path_code         VARCHAR(1020),          -- 点分祖先 CODE 路径
    is_catalog        INTEGER,                -- 1=目录/章节 0=正文条目
    title             VARCHAR(2000),          -- "第一章 基本规定" / "第二十一条"
    index_no          INTEGER,                -- 法规内全局顺序(0-based)
    content           TEXT,
    create_time       TIMESTAMP(6),
    creator_name      VARCHAR(1020),
    creator_id        VARCHAR(256),
    update_time       TIMESTAMP(6),
    updator_id        VARCHAR(256),
    del_flag          VARCHAR(4),
    data_version      VARCHAR(128),
    new_content_code  VARCHAR(512),
    new_path_code     VARCHAR(1024),
    new_law_code      VARCHAR(512)
);


-- ========== 4. 条文富文本/多媒体详情(dcetl §3.6)==========
-- 主表正文为空时，转换脚本消费 CONTENT_TYPE=0 的文本段；图片/视频(1/2)跳过。

CREATE TABLE znfg_iam_law_content_detail (
    etl_src_date      INTEGER,
    etl_src_code      CHAR(4),
    id                VARCHAR(256),
    law_code          VARCHAR(180),
    law_content_code  VARCHAR(256),           -- FK → znfg_iam_law_content.code
    content_order     INTEGER,
    content_type      INTEGER,                -- 0=文本 1=图片 2=视频
    content           TEXT,                   -- 文本/HTML/base64
    create_time       TIMESTAMP(6),
    creator_id        VARCHAR(256),
    update_time       TIMESTAMP(6),
    updator_id        VARCHAR(256),
    del_flag          VARCHAR(4),
    data_version      VARCHAR(128)
);


-- ========== 5. 监管处罚案件主表(dcetl §3.2)==========
-- 真数据:4,695 行。

CREATE TABLE znfg_iam_law_case_basic (
    etl_src_date      INTEGER,
    etl_src_code      CHAR(4),
    id                VARCHAR(256),
    code              VARCHAR(512),           -- 案件业务唯一码
    name              VARCHAR(2000),
    doc_no            VARCHAR(2000),
    pub_auth_cn       VARCHAR(4000),          -- 发布机关
    pub_auth_code     VARCHAR(4000),
    pub_date          DATE,
    doc_type          VARCHAR(180),
    event_date        DATE,
    case_desc         TEXT,                   -- 案件描述 → case_section chunk
    summary           VARCHAR(4000),          -- 问题汇总 → case_summary chunk
    url               VARCHAR(2000),
    punish_basis      TEXT,
    tag               VARCHAR(256),
    create_time       TIMESTAMP(6),
    update_time       TIMESTAMP(6),
    del_flag          VARCHAR(4),
    data_version      VARCHAR(128),
    new_code          VARCHAR(512)
);


-- ========== 6. 涉案主体(dcetl §3.3)==========
-- ⚠ 真数据:**0 行**(甲方未灌)。仿真默认也留空以保真;seed.py --with-parties 可造数
--   用于测 persons[] 通道(该通道在真库上永远空转)。

CREATE TABLE znfg_iam_law_case_party (
    etl_src_date      INTEGER,
    etl_src_code      CHAR(4),
    id                VARCHAR(256),
    party_index       INTEGER,                -- 主体顺序(从 1 起)
    case_code         VARCHAR(512),           -- FK → znfg_iam_law_case_basic.code
    name              VARCHAR(512),
    type_cn           VARCHAR(200),
    identity_cn       VARCHAR(400),
    viol_type_cn      VARCHAR(800),
    fine_amt          VARCHAR(200),
    confiscate_amt    VARCHAR(200),
    crim_fine_amt     VARCHAR(200),
    punish_cur_cn     VARCHAR(200),
    affiliation       VARCHAR(800),
    sec_code          VARCHAR(200),
    sec_sname         VARCHAR(800),
    ind_cn            VARCHAR(800),
    district_cn       VARCHAR(400),
    sector_cn         VARCHAR(400),
    handler           VARCHAR(800),
    status            VARCHAR(200),
    fbd_year          VARCHAR(200),
    fbd_date          VARCHAR(200),
    prison_term       VARCHAR(40),
    defend_status     VARCHAR(400),
    create_time       TIMESTAMP(6),
    update_time       TIMESTAMP(6),
    del_flag          VARCHAR(4),
    data_version      VARCHAR(128),
    new_case_code     VARCHAR(512)
);


-- ========== 7. 处罚依据(案例↔法规条款桥接;dcetl §3.4)==========
-- 真数据:55,299 行。**最大红利表**:law_content_code 直连条款,精确桥接替代 fuzzy 标题对齐。
-- 未命中 3,737 条中 3,736 是**空串锚**(不是 NULL),仅 1 条指向不存在的 content。
-- 字段语义以真数据为准(与 dcetl 文档描述相反):
--   punish_law       = 法规名(如"上市公司信息披露管理办法(2025年修订)")
--   punish_law_title = 条款标识(如"第二十二条")

CREATE TABLE znfg_iam_law_case_punish (
    etl_src_date      INTEGER,
    etl_src_code      CHAR(4),
    id                VARCHAR(256),
    case_code         VARCHAR(512),           -- FK → znfg_iam_law_case_basic.code
    law_code          VARCHAR(512),           -- FK → znfg_iam_law_basic.code
    law_content_code  VARCHAR(512),           -- FK → znfg_iam_law_content.code(精确桥接锚)
    punish_index      INTEGER,
    punish_law        VARCHAR(2000),          -- 实为法规名
    punish_law_title  VARCHAR(2000),          -- 实为条款标识
    content           TEXT,
    create_time       TIMESTAMP(6),
    update_time       TIMESTAMP(6),
    del_flag          VARCHAR(4),
    data_version      VARCHAR(128),
    new_content_code  VARCHAR(512),
    new_law_code      VARCHAR(512),
    new_case_code     VARCHAR(512)
);


-- 检索用索引(源库有无未知;仿真库加上以免大表全扫拖慢联调。
-- 不加唯一约束——源库真有重复行,唯一约束会造不出保真数据)。
CREATE INDEX ix_law_content_law_code ON znfg_iam_law_content (law_code);
CREATE INDEX ix_law_content_code     ON znfg_iam_law_content (code);
CREATE INDEX ix_case_party_case      ON znfg_iam_law_case_party (case_code);
CREATE INDEX ix_case_punish_case     ON znfg_iam_law_case_punish (case_code);
CREATE INDEX ix_law_basic_code       ON znfg_iam_law_basic (code);
