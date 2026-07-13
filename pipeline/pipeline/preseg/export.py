"""达梦(DM8)源库 8 表 → 扩展 intake 批次目录(CP-010 Phase 5,SPEC-PRESEG §3 接收契约)。

**接缝**:本模块把甲方内网法规制度平台的 8 张表(``ZNFG_IAM_LAW_*`` + ``TB_INFA_*``)转换为
管线认的批次目录(manifest.xlsx + blocks/<law>.jsonl + cases.jsonl),之后由
``python -m pipeline.preseg_ingest <batch_dir>`` 摄取。**管线核心不动**(SPEC §3 承诺)。

分层:
- ``Source`` 协议 + ``DmSource``(达梦实现,SQLAlchemy) —— 源耦合层,部署期真库联调。
- 纯转换函数(``blocks_from_contents`` / ``case_record`` / ``manifest_row``) —— FakeSource 可单测。
- ``build_batch(source, out_dir)`` —— 编排 + 落盘。

**源语义未知项(seam,待达梦真样例/甲方对接会锁定,已就地标注)**:
- ``PATH_CODE`` → clause_path_norm 的格式(暂省略 → adapter 从 TITLE 派生,is_catalog 权威仍生效);
- ``STATUS_CODE`` 值域(暂透传 → status_map 未知值走 meta_confirm,不猜);
- 内/外规分型(``LEVELS``→P-INT/P-EXT)、密级、biz_domain/entity_type 源头(schema 无专列)。

**高置信已做实**(最高价值,不依赖未知项):``LAW_CONTENT.CODE`` → chunks.source_code 与
``CASE_PUNISH.LAW_CONTENT_CODE`` → 精确桥接锚,端到端点亮案例反查。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from pipeline.preseg.reader import PRESEG_REQUIRED_COLUMNS, blocks_content_hash, parse_blocks

# ── 源语义 seam(格式/值域待真样例;集中此处便于锁定)──────────────────────────

#: DEL_FLAG 在册标记:排除 D(删除);A 有效、U 修改均视为在册。⚠ U 处置待核。
LIVE_DEL_FLAGS = frozenset({"A", "U"})


def corpus_type_of(levels: str | None) -> str:
    """LEVELS(法规层级)→ 检索分区归属。⚠ seam:值域待样例。默认外规为主(P-EXT);
    含"制度/内部"字样判内规(P-INT)。"""
    s = str(levels or "")
    return "P-INT" if ("制度" in s or "内部" in s) else "P-EXT"


def perm_tag_of(corpus_type: str) -> str:
    """密级:schema 无密级列 → 外规公开、内规内部(工程默认)。⚠ seam:真密级来源待核。"""
    return "internal" if corpus_type == "P-INT" else "public"


def effective_status_of(status_code: str | None) -> str:
    """STATUS_CODE → effective_status(manifest 原值)。⚠ seam:**透传不猜**——值域未知,
    交 status_map;命中(如"现行有效")直落,未知(如数字码)→ meta_confirm 人工定。"""
    return str(status_code or "").strip()


def path_code_to_norm(path_code: str | None, title: str | None) -> str | None:
    """PATH_CODE(章节层级路径)→ clause_path_norm。⚠ seam:**格式未知 → 暂返 None**,
    由 adapter 从 TITLE 派生(is_catalog 权威判目录块仍生效)。真格式确认后在此产出权威 norm。"""
    return None


# ── 源协议(DmSource / FakeSource 共同实现)──────────────────────────────────


class Source(Protocol):
    """8 表读取接口。行统一为 ``dict``(键=大写列名);DEL_FLAG 过滤由本模块统一施加。"""

    def iter_laws(self) -> list[dict]: ...
    def contents_for(self, law_code: str) -> list[dict]: ...
    def iter_cases(self) -> list[dict]: ...
    def parties_for(self, case_code: str) -> list[dict]: ...
    def punishes_for(self, case_code: str) -> list[dict]: ...


# ── 纯转换(FakeSource 可单测)────────────────────────────────────────────────


def _s(v: object) -> str | None:
    s = str(v).strip() if v is not None else ""
    return s or None


def _int(v: object, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _live(rows: list[dict]) -> list[dict]:
    """DEL_FLAG 在册过滤(排除删除件)。"""
    return [r for r in rows if str(r.get("DEL_FLAG") or "A").strip() in LIVE_DEL_FLAGS]


def blocks_from_contents(contents: list[dict]) -> list[dict]:
    """一部法规的 ``ZNFG_IAM_LAW_CONTENT`` 行 → blocks JSONL records。

    - block_seq = **稳定运行序**(enumerate,保证唯一;INDEX_NO 仅供排序,可能非全局唯一);
    - is_catalog = ``IS_CATALOG==1``(权威:目录块不成 chunk);
    - source_code = ``CODE``(精确桥接锚);clause_path_norm 走 seam(暂 None → adapter 派生)。
    正文文本取 ``CONTENT``(图片/视频详情本轮跳过,§跳过决策)。空正文的目录节点照留(带 is_catalog)。
    """
    ordered = sorted(
        _live(contents), key=lambda r: (str(r.get("PATH_CODE") or ""), _int(r.get("INDEX_NO")))
    )
    out: list[dict] = []
    for seq, c in enumerate(ordered):
        title = _s(c.get("TITLE"))
        is_catalog = _int(c.get("IS_CATALOG")) == 1
        text = _s(c.get("CONTENT")) or (title if is_catalog else None)
        if not text:  # 正文块无内容 → 跳过(reader 要求 text 非空;目录块已用 title 兜底)
            continue
        rec: dict = {"block_seq": seq, "text": text, "is_catalog": is_catalog}
        if title:
            rec["clause_label"] = title
        if sc := _s(c.get("CODE")):
            rec["source_code"] = sc
        if norm := path_code_to_norm(c.get("PATH_CODE"), title):
            rec["clause_path_norm"] = norm
        out.append(rec)
    return out


#: CASE_PARTY 列 → persons dict 键(消费面需 name/type/identity/reason;富字段照带,D6)
_PARTY_FIELDS = {
    "PARTY_INDEX": "party_index", "NAME": "name", "TYPE_CN": "type",
    "IDENTITY_CN": "identity", "VIOL_TYPE_CN": "reason", "FINE_AMT": "fine_amt",
    "CONFISCATE_AMT": "confiscate_amt", "CRIM_FINE_AMT": "crim_fine_amt",
    "PUNISH_CUR_CN": "punish_currency", "AFFILIATION": "affiliation",
    "SEC_CODE": "sec_code", "SEC_SNAME": "sec_name", "IND_CN": "industry",
    "DISTRICT_CN": "district", "SECTOR_CN": "sector", "HANDLER": "handler", "STATUS": "status",
}


def _person(party: dict) -> dict:
    """一条 ``ZNFG_IAM_LAW_CASE_PARTY`` → persons 条目(全字段照带,D6;None 值略去)。"""
    return {dst: party.get(src) for src, dst in _PARTY_FIELDS.items() if party.get(src) is not None}


def _violated(punish: dict) -> dict:
    """一条 ``ZNFG_IAM_LAW_CASE_PUNISH`` → violated_regulations 条目(**精确桥接锚**)。"""
    title = _s(punish.get("PUNISH_LAW_TITLE")) or _s(punish.get("PUNISH_LAW")) or "(未标注)"
    v: dict = {"title": title}
    if content := _s(punish.get("CONTENT")) or _s(punish.get("PUNISH_LAW")):
        v["content"] = content
    if lc := _s(punish.get("LAW_CODE")):
        v["law_code"] = lc
    if lcc := _s(punish.get("LAW_CONTENT_CODE")):
        v["law_content_code"] = lcc  # → 直连 chunks.source_code(精确桥接)
    return v


def case_record(case: dict, parties: list[dict], punishes: list[dict]) -> dict:
    """``ZNFG_IAM_LAW_CASE_BASIC`` + PARTY + PUNISH → cases.jsonl 记录(reader.PresegCase 契约)。"""
    persons = [_person(p) for p in sorted(_live(parties), key=lambda r: _int(r.get("PARTY_INDEX")))]
    vregs = [
        _violated(p) for p in sorted(_live(punishes), key=lambda r: _int(r.get("PUNISH_INDEX")))
    ]
    rec: dict = {"case_name": _s(case.get("NAME")) or "(未命名案件)", "persons": persons,
                 "violated_regulations": vregs}
    for src, dst in (
        ("CODE", "source_case_id"), ("DOC_NO", "doc_number"), ("PUB_AUTH_CN", "issuing_org"),
        ("PUB_DATE", "issue_date"), ("EVENT_DATE", "occurred_at"), ("DOC_TYPE", "case_type"),
        ("SUMMARY", "problem_summary"), ("CASE_DESC", "description"), ("URL", "source_url"),
    ):
        if val := _s(case.get(src)):
            rec[dst] = val
    if tag := _s(case.get("TAG")):
        rec["tags"] = [t for t in tag.replace(";", ";").split(";") if t.strip()]
    return rec


def manifest_row(law: dict, blocks: list[dict], filename: str) -> dict:
    """``ZNFG_IAM_LAW_BASIC`` + 其 blocks → manifest 行(21 列全集,精确匹配契约)。

    content_hash = 本次生成 blocks 的**语义规范化指纹**(``blocks_content_hash``)——声明值==实际值,
    s0 交叉核验成恒真,源内容变即指纹变→revise_replace(幂等健壮,不误隔离)。
    """
    corpus = corpus_type_of(law.get("LEVELS"))
    row = dict.fromkeys(PRESEG_REQUIRED_COLUMNS, None)
    parsed = parse_blocks("\n".join(json.dumps(b, ensure_ascii=False) for b in blocks), filename)
    row.update(
        filename=filename,
        title=_s(law.get("NAME")),
        doc_number=_s(law.get("DOC_NO")),
        issuer=_s(law.get("ISSUE_AUTH_CN")),
        perm_tag=perm_tag_of(corpus),
        corpus_type=corpus,
        sub_type=_s(law.get("LEVELS")),  # 法规层级
        issue_date=_s(law.get("ISSUE_DATE")),
        effective_date=_s(law.get("EFFECT_DATE")),
        source_doc_id=_s(law.get("CODE")),  # 逻辑键(跨版本稳)
        content_hash=blocks_content_hash(parsed),
        effective_status=effective_status_of(law.get("STATUS_CODE")),
        issuer_level_src=_s(law.get("LEVELS")),
        tags=_s(law.get("TAG")),
        file_no=_s(law.get("DOC_NO")),
        source_law_id=_s(law.get("SOURCE_LAW_ID")),  # 版本链源
        invalid_date=_s(law.get("INVALID_DATE")),
    )
    # biz_domain / entity_types / source_created_by / supersedes:源无专列(§口径出入)→ 留空(seam)
    return row


def _safe_filename(law_code: str) -> str:
    """CODE → blocks 文件名(单一路径段,承 s0 ``fn==Path(fn).name`` 防穿越)。"""
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in law_code) or "law"


# ── 编排 + 落盘 ──────────────────────────────────────────────────────────────


def build_batch(source: Source, out_dir: Path) -> dict:
    """8 表 → 批次目录。返回统计。**只写目录,不入库**(摄取由 preseg_ingest 单独跑)。"""
    out_dir = Path(out_dir)
    (out_dir / "blocks").mkdir(parents=True, exist_ok=True)

    rows: list[dict] = []
    for law in _live(source.iter_laws()):
        code = _s(law.get("CODE"))
        if not code:
            continue
        blocks = blocks_from_contents(source.contents_for(code))
        if not blocks:  # 无正文块 → 跳过(空法规不成 intake 件)
            continue
        fn = _safe_filename(code)
        (out_dir / "blocks" / f"{fn}.jsonl").write_text(
            "\n".join(json.dumps(b, ensure_ascii=False) for b in blocks) + "\n", encoding="utf-8"
        )
        rows.append(manifest_row(law, blocks, fn))

    cases = [
        case_record(c, source.parties_for(code), source.punishes_for(code))
        for c in _live(source.iter_cases())
        if (code := _s(c.get("CODE")))
    ]
    if cases:
        (out_dir / "cases.jsonl").write_text(
            "\n".join(json.dumps(c, ensure_ascii=False) for c in cases) + "\n", encoding="utf-8"
        )
    _write_manifest(out_dir / "manifest.xlsx", rows)
    return {"laws": len(rows), "cases": len(cases), "out_dir": str(out_dir)}


def _write_manifest(path: Path, rows: list[dict]) -> None:
    """rows → manifest.xlsx(21 列精确匹配 PRESEG_REQUIRED_COLUMNS)。"""
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "manifest"
    ws.append(list(PRESEG_REQUIRED_COLUMNS))
    for r in rows:
        ws.append([r.get(c) for c in PRESEG_REQUIRED_COLUMNS])
    wb.save(str(path))
