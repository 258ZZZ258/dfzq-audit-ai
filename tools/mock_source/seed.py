"""甲方源库仿真数据生成器 —— 按 2026-07-14/15 达梦真数据探查的值域与分布造数。

用法::

    # 起库 + 建表 + 造数(默认 300 部法规,约 1/50 真库规模)
    docker compose -f tools/mock_source/compose.mock-source.yaml up -d
    .venv/bin/python tools/mock_source/seed.py --laws 300

    # 端到端联调:仿真库 → 批次目录(preseg_export.py 零改)
    PRESEG_SOURCE_DSN='postgresql+psycopg://dcetl:dcetl@127.0.0.1:5434/dcetl' \
        .venv/bin/python -m pipeline.preseg_export /tmp/batch-mock

**造数不是编故事** —— 每个字段的取值都锚定探查结果(见 README 的「真值域出处」表)。
凡真库确认的口径,仿真必须复制,**包括那些看起来像 bug 的**:每节点 2 物理行、
空串而非 NULL 的锚、全空串的版本链字段、0 行的 CASE_PARTY。掩盖它们就等于让联调
在仿真上通过、在真库上炸。

确定性:固定 random seed → 同参数重跑产出完全一致(便于回归对拍)。
"""

from __future__ import annotations

import argparse
import hashlib
import os
import random
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

# ── 真值域(全部来自达梦真数据探查,不可随手改;出处见 README)────────────────

#: A2 STATUS_CODE 真分布(15,305 部)。draft=征求意见稿(G8 证实有正文),test_run=测试数据。
STATUS_DIST = [
    ("inuse", 9795), ("abolish", 3815), ("modified", 1291),
    ("draft", 382), ("pending", 19), ("test_run", 3),
]

#: A4 SUIT_OBJ_CODE 真分布。**分隔符不统一**:顿号为主,竖线 1 条 —— 转换脚本两种都要吃。
SUIT_OBJ_DIST = [
    ("", 5868), ("证券", 4626), ("通用", 2570), ("期货", 1159), ("基金", 635),
    ("其他金融机构", 317), ("证券、期货", 71), ("证券、基金", 55),
    ("基金、期货", 3), ("证券|基金", 1),
]

#: A5 LEVELS 真分布。**JSON 数组串**,非裸值。多值组合的串长可达 76 字符 ——
#: export.py 把 LEVELS 原样塞 sub_type(PG 列宽 32)/ issuer_level_src(64),
#: 超宽即整件拒收。这些长值是**故意留着的**:它们在真库存在,联调必须撞上。
LEVELS_DIST = [
    ('["NOTIFICATION_ANNOUNCEMENT"]', 5315),
    ('["MARKET_CORE_INSTITUTIONS"]', 4001),
    ('["NORMATIVE_DOC"]', 2421),
    ('["DEPARTMENTAL_RULES"]', 1170),
    ('["JUDICIAL_INTERPRETATION"]', 536),
    ('["SECURITIES_ASSOCIATION"]', 461),
    ('["NATIONAL_LAWS"]', 361),
    ('["ADMINISTRATIVE_REGULATIONS"]', 285),
    ('["ASSET_MANAGEMENT_ASSOCIATION"]', 228),
    ('["PRESS_Q&A"]', 163),
    ('["FUTURES_ASSOCIATION"]', 110),
    ('["OTHER_ASSOCIATION"]', 109),
    ('["POLITICAL_ORGANIZATIONAL_DOC"]', 55),
    ('["LOCAL_REGULATIONS"]', 50),
    ('["INDUSTRY_LAWS"]', 12),
    ('["SECURITIES_ASSOCIATION","FUTURES_ASSOCIATION","ASSET_MANAGEMENT_ASSOCIATION"]', 8),
    ('["REPORT_AND_GUIDELINE"]', 6),
    ("", 4),
    ('["SECURITIES_ASSOCIATION","FUTURES_ASSOCIATION"]', 3),
    ('["SECURITIES_ASSOCIATION","ASSET_MANAGEMENT_ASSOCIATION","FUTURES_ASSOCIATION"]', 3),
    ('["SECURITIES_ASSOCIATION","ASSET_MANAGEMENT_ASSOCIATION"]', 1),
    ('["ASSET_MANAGEMENT_ASSOCIATION","SECURITIES_ASSOCIATION"]', 1),
    ('["DEPARTMENTAL_RULES_NORMATIVE_DOC"]', 1),
]

#: A3 DEL_FLAG:A 为主。D 应被 SQL 过滤掉,U 视为在册(export.LIVE_DEL_FLAGS)。
DEL_FLAG_DIST = [("A", 970), ("U", 25), ("D", 5)]

ISSUE_AUTH = [
    "中国证券监督管理委员会", "上海证券交易所", "深圳证券交易所", "中国证券业协会",
    "中国期货业协会", "中国证券投资基金业协会", "全国人民代表大会常务委员会",
    "国务院", "最高人民法院", "中国人民银行",
]

DOC_TYPES = ["行政处罚决定书", "市场禁入决定书", "纪律处分决定书", "监管措施决定书"]

#: 条款正文模板(内容不求逼真,但长度分布要像:短则数十字,长则数百字)
CLAUSE_TEMPLATES = [
    "为了规范{obj}的{act}行为,保护投资者合法权益,维护证券市场秩序,根据《中华人民共和国证券法》"
    "等法律法规,制定本{kind}。",
    "{obj}应当建立健全{act}的内部控制制度,明确岗位职责和操作流程,并报{auth}备案。",
    "{obj}从事{act}业务,应当遵循公开、公平、公正的原则,不得损害投资者合法权益。",
    "{obj}违反本{kind}规定的,由{auth}责令改正,给予警告,并处以三万元以上十万元以下罚款;"
    "情节严重的,暂停或者撤销其相关业务资格。",
    "{obj}应当于每个会计年度结束之日起四个月内,向{auth}报送年度{act}报告,并予以公告。",
]

CHAPTER_NAMES = [
    "总则", "基本规定", "业务规则", "信息披露", "内部控制", "风险管理",
    "监督管理", "法律责任", "附则",
]

SUBJECTS = ["证券公司", "基金管理人", "期货公司", "上市公司", "会计师事务所"]
ACTIVITIES = ["信息披露", "资产管理", "内部控制", "关联交易", "风险监测"]

CN_NUM = "一二三四五六七八九十"


def cn_number(n: int) -> str:
    """1..99 → 中文数字(第X条 用)。"""
    if n <= 10:
        return CN_NUM[n - 1]
    if n < 20:
        return "十" + (CN_NUM[n - 11] if n > 10 else "")
    tens, ones = divmod(n, 10)
    return CN_NUM[tens - 1] + "十" + (CN_NUM[ones - 1] if ones else "")


def weighted(rng: random.Random, dist: list[tuple[str, int]]) -> str:
    """按真分布权重抽样。"""
    values, weights = zip(*dist, strict=True)
    return rng.choices(values, weights=weights, k=1)[0]


def make_code(prefix: str, seed: str) -> str:
    """源库 CODE 形态:``ELA7`` + 32 位大写 hex = 36 字符(真数据 E2 样例证实)。"""
    return prefix + hashlib.md5(seed.encode()).hexdigest().upper()  # noqa: S324


def snowflake(rng: random.Random) -> str:
    """Snowflake 风格 19 位数字 ID(源库 ID 列)。"""
    return str(rng.randint(10**18, 10**19 - 1))


# ── 造数 ──────────────────────────────────────────────────────────────────


def gen_laws(rng: random.Random, n: int) -> list[dict]:
    """法规主表行。SCOPE 恒 0(真库 15,305 部全外规);TAG/版本链字段恒空串(非 NULL)。"""
    rows = []
    for i in range(n):
        code = make_code("ELA7", f"law-{i}")
        status = weighted(rng, STATUS_DIST)
        issue = date(2015, 1, 1) + timedelta(days=rng.randint(0, 3800))
        # pending/draft = 未生效 → 生效日在未来;abolish → 有失效日
        effect = issue + timedelta(days=rng.randint(0, 90))
        if status in ("pending", "draft"):
            effect = date.today() + timedelta(days=rng.randint(30, 365))
        invalid = (
            effect + timedelta(days=rng.randint(365, 2000)) if status == "abolish" else None
        )
        rows.append({
            "etl_src_date": int(issue.strftime("%Y%m%d")),
            "etl_src_code": "DCET",
            "id": snowflake(rng),
            "old_id": "",
            "scope": 0,                       # A1:真库全 0(全外规)
            "code": code,
            "name": f"{rng.choice(ISSUE_AUTH)}关于{rng.choice(CHAPTER_NAMES)}管理的规定"
                    f"({2015 + i % 11}年修订)",
            "doc_no": f"证监会公告〔{2015 + i % 11}〕{i % 99 + 1}号",
            "issue_auth_cn": rng.choice(ISSUE_AUTH),
            "issue_auth_code": f"AUTH{i % 50:04d}",
            "suit_obj_code": weighted(rng, SUIT_OBJ_DIST),
            "issue_date": issue,
            "invalid_date": invalid,
            "effect_date": effect,
            "status_code": status,
            "modify_info": "",
            "forbidden_msg": "",
            "source_law_id": "",              # E1/H2:100% 非空但全是空串
            "levels": weighted(rng, LEVELS_DIST),
            "tag": "",                        # F3:全库空串
            "create_time": datetime.combine(issue, datetime.min.time()),
            "update_time": datetime.combine(issue, datetime.min.time()),
            "del_flag": weighted(rng, DEL_FLAG_DIST),
            "has_content": 1,
            "data_version": "1",
            "owner": 1,
            "source": 1,
            "new_code": "",                   # H3:join 命中他法 0 条
            "creator_id": f"znsj_user{i % 7 + 1}",
            "updator_id": f"znsj_user{i % 7 + 1}",
            "ext_01": None, "ext_02": None, "ext_03": None,
            "abolish_code": "",               # H3:同上
        })
    return rows


def _emit_node(rows: list[dict], rng: random.Random, law: dict, *, code_seq: int, idx: int,
               is_catalog: int, title: str, content: str, path: str) -> None:
    """一个逻辑节点 → **2 个物理行**(同 CODE、异 snowflake ID、内容一致)。

    复刻真库 G1/G2:全表 `COUNT(DISTINCT CODE)` 恒 = 1,即两行只差 ID。
    """
    node_code = f"{law['code']}{code_seq:03d}"
    for _ in range(2):
        rows.append({
            "etl_src_date": law["etl_src_date"], "etl_src_code": "DCET",
            "id": snowflake(rng), "old_id": "",
            "code": node_code, "law_id": law["id"], "law_code": law["code"],
            "path_code": path, "is_catalog": is_catalog, "title": title,
            "index_no": idx, "content": content,
            "create_time": law["create_time"], "creator_name": "系统",
            "creator_id": law["creator_id"], "update_time": law["update_time"],
            "updator_id": law["updator_id"], "del_flag": "A", "data_version": "1",
            "new_content_code": "", "new_path_code": "", "new_law_code": "",
        })


def gen_contents(rng: random.Random, laws: list[dict]) -> list[dict]:
    """生成条款树行的中间形态；正文随后移动到详情表。

    **每个逻辑节点写 2 个物理行**(同 CODE、异 ID)，复刻真库 G1/G2；调用
    :func:`gen_content_details` 后，所有 ``LAW_CONTENT.CONTENT`` 都会变为空串，只有
    ``LAW_CONTENT_DETAIL.CONTENT`` 承载正文。

    结构仿真数据 B1 样例:
      INDEX_NO=0  根节点(TITLE/CONTENT 空,IS_CATALOG=0)  PATH_CODE = 自身 CODE
      INDEX_NO=2  "第一编 总则"      IS_CATALOG=1          PATH_CODE = 自身 CODE
      INDEX_NO=4  "第一章 基本规定"  IS_CATALOG=1          PATH_CODE = 自身 CODE
      INDEX_NO=5+ "第一条"…          IS_CATALOG=0          PATH_CODE = 章 CODE + "." + 自身
    """
    rows: list[dict] = []
    for law in laws:
        law_code = law["code"]
        n_clauses = rng.randint(8, 45)
        n_chapters = max(1, n_clauses // 8)
        seq = 0          # CODE 的 3 位序号
        index_no = 0     # 法规内全局序(0-based;真数据有空洞,此处顺延)
        clause_no = 0

        # 根节点:无标题无正文 —— export.py 应跳过它(text 为空)
        _emit_node(rows, rng, law, code_seq=seq, idx=index_no, is_catalog=0,
                   title="", content="", path=f"{law_code}{seq:03d}")
        seq += 1
        index_no += 2

        for ch in range(n_chapters):
            chapter_path = f"{law_code}{seq:03d}"
            _emit_node(rows, rng, law, code_seq=seq, idx=index_no, is_catalog=1,
                       title=f"第{cn_number(ch + 1)}章 {rng.choice(CHAPTER_NAMES)}",
                       content="", path=chapter_path)
            seq += 1
            index_no += 2

            for _ in range(max(1, n_clauses // n_chapters)):
                clause_no += 1
                text = rng.choice(CLAUSE_TEMPLATES).format(
                    obj=rng.choice(SUBJECTS), act=rng.choice(ACTIVITIES),
                    auth=rng.choice(ISSUE_AUTH), kind=rng.choice(["办法", "规定", "细则"]),
                )
                _emit_node(rows, rng, law, code_seq=seq, idx=index_no, is_catalog=0,
                           title=f"第{cn_number(clause_no)}条", content=text,
                           path=f"{chapter_path}.{law_code}{seq:03d}")
                seq += 1
                index_no += 1
    return rows


def gen_content_details(rng: random.Random, contents: list[dict]) -> list[dict]:
    """把逻辑条款正文同步到详情表，并置空主表正文。

    真达梦有效主表正文几乎全空；详情以 ``LAW_CONTENT_CODE`` 关联逻辑条款 CODE。主表的
    两个重复物理行只生成一条文本详情，保持真实库的“主表重复不等于正文重复”形态。
    """
    details: list[dict] = []
    seen_codes: set[str] = set()
    for row in contents:
        content = row["content"]
        row["content"] = ""
        code = row["code"]
        if not content or code in seen_codes:
            continue
        seen_codes.add(code)
        details.append({
            "etl_src_date": row["etl_src_date"], "etl_src_code": row["etl_src_code"],
            "id": snowflake(rng), "law_code": row["law_code"], "law_content_code": code,
            "content_order": 0, "content_type": 0, "content": content,
            "create_time": row["create_time"], "creator_id": row["creator_id"],
            "update_time": row["update_time"], "updator_id": row["updator_id"],
            "del_flag": row["del_flag"], "data_version": row["data_version"],
        })
    return details


#: 定向边缘样本(--edge-cases)。随机分布下罕见分支在小规模里抽不到:真库 15,305 部里
#: 超宽 LEVELS 只有 17 部(0.1%),300 部的样本期望 0.3 部 —— 联调因此可能"全绿"却没碰过
#: 拒收路径。每条 (key, 覆盖的分支, 字段覆写) 定向撞一个已知分支。
EDGE_LAWS = [
    ("levels-overflow", "LEVELS 三值组合 79 字符 > sub_type 列宽 32 → 整件拒收",
     {"levels": '["SECURITIES_ASSOCIATION","FUTURES_ASSOCIATION","ASSET_MANAGEMENT_ASSOCIATION"]'}),
    ("scope-null", "SCOPE 空 → classify_scope fail-closed 拒收(绝不默认 public)",
     {"scope": None}),
    ("scope-unknown", "SCOPE=9 未知值 → 同上 fail-closed", {"scope": 9}),
    ("scope-internal", "SCOPE=1 内规 → (P-INT, internal)", {"scope": 1}),
    ("scope-criterion", "SCOPE=2 标准 → (P-EXT, internal)", {"scope": 2}),
    ("status-testrun", "STATUS_CODE=test_run → SKIP_STATUS 整件跳过",
     {"status_code": "test_run"}),
    ("status-unknown", "STATUS_CODE 未知值 → 透传 status_map → meta_confirm",
     {"status_code": "archived"}),
    ("status-draft", "STATUS_CODE=draft 征求意见稿 → upcoming(有正文)",
     {"status_code": "draft"}),
    ("suitobj-pipe", "SUIT_OBJ_CODE 竖线分隔(真库仅 1 条)→ 拆多值",
     {"suit_obj_code": "证券|基金"}),
    # ⚠ 选 ISSUE_AUTH_CN 而非 NAME:源 NAME 是 VARCHAR(400) < 目标 title 列宽 512,
    #   那条 _bound 分支在真 schema 下**不可达**(造不出越界数据)。ISSUE_AUTH_CN 源宽 4000
    #   → 目标 issuer 128,才是真正会踩的越界面。详见 README「列宽边界的可达性」。
    ("issuer-overflow", "ISSUE_AUTH_CN 超 issuer 列宽 128 → 整件拒收",
     {"issue_auth_cn": "某某监督管理委员会" * 20}),
    ("delflag-u", "DEL_FLAG=U 修改态 → 仍视为在册(LIVE_DEL_FLAGS)", {"del_flag": "U"}),
    # 下面两条的边缘在 LAW_CONTENT 侧,由 apply_content_edges 后处理制造
    ("no-content", "在册法规但一条正文都没有 → 无正文跳过", {"has_content": 0}),
    ("content-conflict", "同 CODE 两物理行内容不一致 → 无法判权威版,拒收整部法规", {}),
]


def apply_content_edges(
    contents: list[dict], content_details: list[dict], edge_codes: dict[str, str],
) -> tuple[list[dict], list[dict]]:
    """制造两个正文侧边缘:①整部无正文;②同条款详情文本冲突。

    正文已在详情表，不能再通过主表 ``CONTENT`` 制造冲突；同一条款同一顺序却不同正文同样
    无法判权威，导出必须拒收。
    """
    no_content = edge_codes.get("no-content")
    conflict = edge_codes.get("content-conflict")
    out = [r for r in contents if r["law_code"] != no_content]
    out_details = [r for r in content_details if r["law_code"] != no_content]
    if conflict:
        target_code = None
        for row in out:
            if row["law_code"] != conflict or row["is_catalog"]:
                continue
            target_code = row["code"]
            break
        for detail in out_details:
            if detail["law_content_code"] != target_code or detail["content_type"] != 0:
                continue
            out_details.append({
                **detail,
                "id": f"{detail['id']}C",
                "content": detail["content"] + "(同顺序冲突样本)",
            })
            break
    return out, out_details


def gen_edge_laws(rng: random.Random, base_index: int) -> list[dict]:
    """定向边缘法规:在标准行上覆写单个字段,一条只撞一个分支(失败时好归因)。"""
    rows = []
    for i, (key, _why, overrides) in enumerate(EDGE_LAWS):
        (row,) = gen_laws(rng, 1)
        row["code"] = make_code("ELA7", f"edge-{key}")
        row["id"] = snowflake(rng)
        row["del_flag"] = "A"          # 除非 overrides 另说,边缘件必须在册才走到被测分支
        row["status_code"] = "inuse"
        row["name"] = f"[边缘样本 {base_index + i}] {key}"
        row.update(overrides)
        rows.append(row)
    return rows


def gen_cases(rng: random.Random, n: int) -> list[dict]:
    """案件主表行。"""
    rows = []
    for i in range(n):
        pub = date(2018, 1, 1) + timedelta(days=rng.randint(0, 2800))
        event = pub - timedelta(days=rng.randint(30, 900))
        org = rng.choice(ISSUE_AUTH)
        rows.append({
            "etl_src_date": int(pub.strftime("%Y%m%d")), "etl_src_code": "DCET",
            "id": snowflake(rng),
            "code": make_code("ECA7", f"case-{i}"),
            "name": f"关于对{rng.choice(['某某证券', '某某基金', '某某期货', '某某科技股份'])}"
                    f"有限公司及相关责任人员的行政处罚决定",
            "doc_no": f"〔{pub.year}〕{i % 99 + 1}号",
            "pub_auth_cn": org, "pub_auth_code": f"AUTH{i % 50:04d}",
            "pub_date": pub, "doc_type": rng.choice(DOC_TYPES), "event_date": event,
            "case_desc": "经查,当事人存在以下违法事实:未按规定履行信息披露义务,"
                         "相关定期报告存在虚假记载,涉及金额较大,情节严重。",
            "summary": "未按规定披露关联交易;定期报告存在虚假记载。",
            "url": f"http://www.example.gov.cn/case/{i}",
            "punish_basis": "《中华人民共和国证券法》第一百九十七条",
            "tag": "",
            "create_time": datetime.combine(pub, datetime.min.time()),
            "update_time": datetime.combine(pub, datetime.min.time()),
            "del_flag": weighted(rng, DEL_FLAG_DIST), "data_version": "1", "new_code": "",
        })
    return rows


def gen_punishes(rng: random.Random, cases: list[dict], contents: list[dict],
                 content_details: list[dict], laws: list[dict]) -> list[dict]:
    """处罚依据行 —— 桥接表。锚命中比例复刻真库 C1/I2。

    真库 55,299 行中 3,737 未命中,其中 **3,736 是空串锚**(不是 NULL)、1 条指向不存在的
    content。即:约 93.2% 行带可用锚,6.7% 空串锚,极少数悬空。
    """
    # 可作锚的正文条款(IS_CATALOG=0 且在详情表有文本),按法规归组。
    detail_text_by_code = {
        detail["law_content_code"]: detail["content"]
        for detail in content_details
        if detail["content_type"] == 0 and detail["content"] and detail["del_flag"] != "D"
    }
    by_law: dict[str, list[dict]] = {}
    for c in contents:
        if c["is_catalog"] == 0 and c["code"] in detail_text_by_code:
            by_law.setdefault(c["law_code"], []).append(c)
    law_by_code = {law["code"]: law for law in laws}
    anchorable = [code for code, items in by_law.items() if items]

    rows: list[dict] = []
    for case in cases:
        for idx in range(rng.randint(3, 20)):
            roll = rng.random()
            law_code = rng.choice(anchorable) if anchorable else ""
            law = law_by_code.get(law_code, {})
            node = rng.choice(by_law[law_code]) if law_code else None
            if roll < 0.932:                    # 真锚(可 join)
                anchor = node["code"] if node else ""
                clause_title = node["title"] if node else ""
            elif roll < 0.9997:                 # 空串锚(**不是 NULL**)—— 真库主要缺口形态
                anchor = ""
                clause_title = node["title"] if node else ""
            else:                               # 悬空锚:指向不存在的 content
                anchor = make_code("ELA7", f"missing-{idx}")
                clause_title = "第一条"
            rows.append({
                "etl_src_date": case["etl_src_date"], "etl_src_code": "DCET",
                "id": snowflake(rng),
                "case_code": case["code"],
                "law_code": law_code,
                "law_content_code": anchor,
                "punish_index": idx + 1,
                # ⚠ 字段语义以真数据 C2 为准(与 dcetl 文档描述相反):
                "punish_law": law.get("name", ""),        # 法规名
                "punish_law_title": clause_title,         # 条款标识
                "content": detail_text_by_code.get(node["code"], "") if node else "",
                "create_time": case["create_time"], "update_time": case["update_time"],
                "del_flag": "A", "data_version": "1",
                "new_content_code": "", "new_law_code": "", "new_case_code": "",
            })
    return rows


def gen_parties(rng: random.Random, cases: list[dict]) -> list[dict]:
    """涉案主体 —— **真库 0 行**,仅 --with-parties 时造(测 persons[] 通道用)。"""
    rows = []
    for case in cases:
        for pi in range(rng.randint(1, 4)):
            is_org = rng.random() < 0.6
            rows.append({
                "etl_src_date": case["etl_src_date"], "etl_src_code": "DCET",
                "id": snowflake(rng), "party_index": pi + 1, "case_code": case["code"],
                "name": "某某证券股份有限公司" if is_org else f"张{cn_number(pi + 1)}",
                "type_cn": "机构" if is_org else "个人",
                "identity_cn": "发行人" if is_org else "董事长",
                "viol_type_cn": "信息披露违法违规",
                "fine_amt": str(rng.randint(10, 500) * 10000),
                "confiscate_amt": "0", "crim_fine_amt": "0", "punish_cur_cn": "人民币",
                "affiliation": "", "sec_code": f"{rng.randint(600000, 603999)}",
                "sec_sname": "某某股份", "ind_cn": "制造业", "district_cn": "上海市",
                "sector_cn": "主板", "handler": "", "status": "已结案",
                "fbd_year": "", "fbd_date": "", "prison_term": "", "defend_status": "",
                "create_time": case["create_time"], "update_time": case["update_time"],
                "del_flag": "A", "data_version": "1", "new_case_code": "",
            })
    return rows


def gen_etl_log(counts: dict[str, int]) -> tuple[list[dict], list[dict]]:
    """Informatica 推送日志两表 —— 一次 workflow run 加载全部业务表。

    ⚠ 这是**日志形态的复刻**,不是甲方真实调度的还原:真实 SOURCE_SYSTEM 取值、加载频率、
    workflow 依赖关系均无资料(见 README「复刻不了的部分」)。
    """
    run_id = 20260714001
    busi_date = "20260714"
    start = datetime(2026, 7, 14, 2, 0, 0)
    push = [{
        "busi_date": busi_date, "source_system": "DCETL", "etl_date": busi_date,
        "start_datetime": start, "end_datetime": start + timedelta(minutes=37),
        "etl_status": 1, "flag": "1", "workflow_run_id": run_id,
        "get_time": start + timedelta(minutes=37),
    }]
    info = []
    offset = 0
    for table, rows in counts.items():
        info.append({
            "busi_date": busi_date, "schema_name": "DCETL", "table_name": table.upper(),
            "etl_date": busi_date, "etl_status": 1,
            "start_time": start + timedelta(minutes=offset),
            "end_time": start + timedelta(minutes=offset + 4),
            "successful_rows": rows, "failed_rows": 0, "trans_errors": 0,
            "workflow_run_id": run_id,
        })
        offset += 5
    return push, info


# ── 落库 ──────────────────────────────────────────────────────────────────


def insert(conn, table: str, rows: list[dict]) -> None:
    """批量插入(分块,避免超长参数列表)。"""
    from sqlalchemy import text

    if not rows:
        return
    cols = list(rows[0])
    stmt = text(
        f"INSERT INTO {table} ({', '.join(cols)}) "  # noqa: S608 — 列名来自本模块字面量
        f"VALUES ({', '.join(':' + c for c in cols)})"
    )
    for i in range(0, len(rows), 2000):
        conn.execute(stmt, rows[i:i + 2000])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="seed.py", description="按达梦真数据的值域/分布生成源库仿真数据。"
    )
    ap.add_argument("--laws", type=int, default=300,
                    help="法规数(默认 300;真库 15,305)。案例数按真比例 0.307 换算")
    ap.add_argument("--cases", type=int, default=None, help="案例数(默认按法规数比例换算)")
    ap.add_argument("--with-parties", action="store_true",
                    help="造 CASE_PARTY 数据(真库该表 0 行;仅为测 persons[] 通道)")
    ap.add_argument("--edge-cases", action="store_true",
                    help="追加定向边缘样本(拒收/跳过/fail-closed 分支;随机分布下抽不到)")
    ap.add_argument("--seed", type=int, default=20260714, help="随机种子(确定性复现)")
    ap.add_argument("--dsn", default=os.environ.get(
        "MOCK_SOURCE_DSN", "postgresql+psycopg://dcetl:dcetl@127.0.0.1:5434/dcetl"))
    args = ap.parse_args(argv)

    from sqlalchemy import create_engine

    rng = random.Random(args.seed)
    n_cases = args.cases if args.cases is not None else max(1, round(args.laws * 0.307))

    print(f"生成中(seed={args.seed}):laws={args.laws} cases={n_cases}")
    laws = gen_laws(rng, args.laws)
    edge_codes: dict[str, str] = {}
    if args.edge_cases:
        edges = gen_edge_laws(rng, args.laws)
        edge_codes = {key: row["code"] for (key, _, _), row in zip(EDGE_LAWS, edges, strict=True)}
        laws.extend(edges)
    contents = gen_contents(rng, laws)
    content_details = gen_content_details(rng, contents)
    if args.edge_cases:
        contents, content_details = apply_content_edges(contents, content_details, edge_codes)
    cases = gen_cases(rng, n_cases)
    punishes = gen_punishes(rng, cases, contents, content_details, laws)
    parties = gen_parties(rng, cases) if args.with_parties else []

    engine = create_engine(args.dsn)
    schema_sql = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")
    with engine.begin() as conn:
        # exec_driver_sql 而非 text():schema.sql 是多语句 DDL,SQLAlchemy 的 text() 只收单条,
        # 且会把 SQL 里的 ":" 当参数占位符。
        conn.exec_driver_sql(schema_sql)
        insert(conn, "znfg_iam_law_basic", laws)
        insert(conn, "znfg_iam_law_content", contents)
        insert(conn, "znfg_iam_law_content_detail", content_details)
        insert(conn, "znfg_iam_law_case_basic", cases)
        insert(conn, "znfg_iam_law_case_party", parties)
        insert(conn, "znfg_iam_law_case_punish", punishes)
        push, info = gen_etl_log({
            "znfg_iam_law_basic": len(laws), "znfg_iam_law_content": len(contents),
            "znfg_iam_law_content_detail": len(content_details),
            "znfg_iam_law_case_basic": len(cases), "znfg_iam_law_case_party": len(parties),
            "znfg_iam_law_case_punish": len(punishes),
        })
        insert(conn, "tb_infa_push", push)
        insert(conn, "tb_infa_push_info", info)

    anchored = sum(1 for p in punishes if p["law_content_code"])
    party_note = "(真库为 0 行)" if not parties else "(真库无此数据)"
    print(
        f"✓ 落库完成\n"
        f"  LAW_BASIC   {len(laws):>7,}\n"
        f"  LAW_CONTENT {len(contents):>7,}  (正文为空,每逻辑节点 2 物理行)\n"
        f"  CONTENT_DETAIL {len(content_details):>5,}  (正文文本)\n"
        f"  CASE_BASIC  {len(cases):>7,}\n"
        f"  CASE_PARTY  {len(parties):>7,}  {party_note}\n"
        f"  CASE_PUNISH {len(punishes):>7,}  (带锚 {anchored:,} = "
        f"{anchored / max(1, len(punishes)):.1%},真库 93.2%)\n"
        f"\n下一步:\n"
        f"  PRESEG_SOURCE_DSN='{args.dsn}' \\\n"
        f"      python -m pipeline.preseg_export <out_dir>"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
