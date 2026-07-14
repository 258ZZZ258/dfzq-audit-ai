"""达梦(DM8)源库 8 表 → 扩展 intake 批次目录(CP-010 Phase 5,SPEC-PRESEG §3 接收契约)。

**接缝**:本模块把甲方内网法规制度平台的源表(``ZNFG_IAM_LAW_*``)转换为管线认的批次目录
(manifest.xlsx + blocks/<law>.jsonl + cases.jsonl),之后由 ``python -m pipeline.preseg_ingest``
摄取。**管线核心不动**(SPEC §3 承诺)。

分层:``Source`` 协议 + ``DmSource``(达梦,见 preseg_export)| 纯转换(FakeSource 可单测)|
``build_batch``(编排 + 落盘)。

**真实源 schema 依据**:``东方/东方知识库/图片/`` 内的完整字段表(SCOPE / 列宽 / 日期型等;
用户提供的 ``知识库结构.md`` 是有损精简版,漏了 SCOPE、SUIT_OBJ_CODE、列宽、ABOLISH_CODE 等)。

**安全钉子(Codex review)**:
- **分类走 SCOPE(权威),fail-closed**:SCOPE 0=外规/1=内规/2=标准;未知/空 → **拒收不导出**,
  绝不默认 public(否则内规被标 public 越权披露)。
- **列宽保真**:源键列(CODE 180 / LAW_CONTENT.CODE 256 / SOURCE_LAW_ID 256)拓宽承接(迁移 0016);
  描述列按 PG 列宽 ``_fit`` 截断 + 审计(不 DataError 中断批次,不静默破坏键)。
- **日期规范化**:达梦返回 date/datetime,统一 ISO 日期(否则含时分秒串使 S0 fromisoformat 落 None)。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from datetime import date, datetime
from pathlib import Path
from typing import Protocol

from pipeline.preseg.reader import PRESEG_REQUIRED_COLUMNS, blocks_content_hash, parse_blocks


class PresegExportError(ValueError):
    """转换期不可安全导出(如源键超列宽、分类不可判)——拒收该件,不带病产出。"""


# ── 源语义映射(依据真实 schema + 达梦真数据探查,2026-07-14)──────────────────

#: DEL_FLAG 在册标记:排除 D(删除);A 有效、U 修改视为在册。⚠ U 处置待甲方确认。
LIVE_DEL_FLAGS = frozenset({"A", "U"})

#: 跳过的 STATUS_CODE:test_run=测试数据(真数据仅 3 条),不入生产库。
SKIP_STATUS = frozenset({"test_run"})

#: 适用对象多值分隔符(真数据 SUIT_OBJ_CODE 见 顿号、竖线 混用,统一拆)
_SUIT_OBJ_DELIM = re.compile(r"[、,，|/;;]+")

#: 所有落 PG 定长列的宽度(法规 + 案例)。**超界 → 可审计拒收该件,绝不截断法律元数据**
#: (文号/机关/效力层级/条款锚都是法律身份;截断会污染,DataError 会中断批次;Codex 二轮 F2/F3)。
#: 真实值远小于列宽(code ~39、文号/机关 ~十几字),拒收几乎不触发;触发即整件拦下人工核。
_COL_WIDTHS = {
    # 法规(manifest → doc_versions/chunks)
    "title": 512, "issuer": 128, "doc_number": 128, "sub_type": 32,
    "issuer_level_src": 64, "file_no": 128, "source_doc_id": 64,
    "source_code": 256, "source_law_id": 256,
    # 案例(cases.jsonl → doc_versions/cases)
    "case_name": 512, "source_case_id": 64, "case_doc_number": 128,
    "penalty_org": 256, "case_type": 64, "source_url": 512,
}


def entity_types_of(suit_obj_code: object) -> str | None:
    """SUIT_OBJ_CODE(适用对象,中文多值)→ ``;``-join 串(供 manifest;s0 再按 ``;`` 拆)。

    真数据(探查 A4):中文名直存(通用/证券/基金/期货/其他金融机构…),多值分隔符不统一
    (顿号、竖线 混用)→ 统一拆再以 ``;`` 规范化。'通用'=不限对象,原样保留。空→None。
    ⚠ 查询侧 dict_entity_types 词表须换成这套真值(证券/基金/期货…),D7 过滤才对得上——query 侧跟进。
    """
    s = _s(suit_obj_code)
    if not s:
        return None
    parts = [p.strip() for p in _SUIT_OBJ_DELIM.split(s) if p.strip()]
    return ";".join(parts) or None


def classify_scope(scope: object) -> tuple[str, str] | None:
    """SCOPE(ZNFG_IAM_LAW_BASIC.SCOPE)→ (corpus_type, perm_tag)。**权威分类,fail-closed**。

    0=external→(P-EXT, public);1=internal→(P-INT, internal);2=criterion 标准→(P-EXT, internal)
    (外规分区但密级保守,待甲方确认可否 public);**未知/空 → None(调用方拒收,绝不默认 public)**。
    真数据(探查 A1):当前全库 SCOPE=0(15,305 部全外规);fail-closed 仍作跨环境健壮兜底。
    """
    try:
        s = int(scope)
    except (TypeError, ValueError):
        return None
    return {0: ("P-EXT", "public"), 1: ("P-INT", "internal"), 2: ("P-EXT", "internal")}.get(s)


def effective_status_of(status_code: object) -> str:
    """STATUS_CODE → effective_status(manifest 原值)。⚠ seam:**透传不猜**——值域未知,
    交 status_map;命中直落,未知 → meta_confirm 人工定。"""
    return str(status_code or "").strip()


def path_code_to_norm(path_code: object, title: object) -> str | None:
    """PATH_CODE → clause_path_norm。**返回 None(设计如此,非缺陷)**。

    真数据(探查 B1):PATH_CODE 是**点分的 opaque CODE 路径**(每段是 ``ELA7AF908…`` 这类哈希 ID,
    仅编码父子祖先关系),**不含可读编号**;人类编号("第X条/章/节")在 **TITLE** 里。故 norm 从 TITLE
    派生(adapter/derive.py 已做,含内嵌章节),PATH_CODE 的价值仅在祖先关系——本流程不需要,恒 None。"""
    return None


# ── 值规范化辅助 ──────────────────────────────────────────────────────────────


def _s(v: object) -> str | None:
    s = str(v).strip() if v is not None else ""
    return s or None


def _date(v: object) -> str | None:
    """达梦 DATE/TIMESTAMP → ISO 日期串(YYYY-MM-DD)。datetime 取日期部分(丢时分秒,
    否则 S0 date.fromisoformat 静默落 None);str 取前 10 位(若形如日期);其余 None。"""
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    s = _s(v)
    if not s:
        return None
    head = s[:10]
    return head if len(head) == 10 and head[4] == "-" and head[7] == "-" else s


def _int(v: object, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _bound(value: object, field: str) -> str | None:
    """落 PG 定长列的字段边界:超列宽 → **拒收(PresegExportError,可审计,调用方 skip 该件)**,
    不截断(法律元数据)、不 DataError(中断批次)。None/空 → None。"""
    s = _s(value)
    if s is not None and len(s) > _COL_WIDTHS[field]:
        raise PresegExportError(f"{field} 长度 {len(s)} 超 PG 列宽 {_COL_WIDTHS[field]}:{s[:30]}…")
    return s


def _content_sig(row: dict) -> tuple:
    """LAW_CONTENT 行内容指纹(去重冲突比对用):TITLE/CONTENT/IS_CATALOG/INDEX_NO。"""
    return (_s(row.get("TITLE")), _s(row.get("CONTENT")),
            _int(row.get("IS_CATALOG")), _int(row.get("INDEX_NO")))


def _live(rows: list[dict]) -> list[dict]:
    """DEL_FLAG 在册过滤(排除删除件;NULL 视为 A 在册)。"""
    return [r for r in rows if str(r.get("DEL_FLAG") or "A").strip() in LIVE_DEL_FLAGS]


# ── 纯转换(FakeSource 可单测)────────────────────────────────────────────────


def blocks_from_contents(contents: list[dict], warnings: list[str]) -> list[dict]:
    """一部法规的 LAW_CONTENT 行 → blocks JSONL records。

    - block_seq = 稳定运行序(enumerate 保唯一);排序按 **INDEX_NO(源 0-based 全局序)** + CODE,
      不按 PATH_CODE 字符串(未补零会让 1.10 排在 1.2 前);
    - is_catalog = IS_CATALOG==1;source_code = CODE(精确桥接锚,超 256 → 弃锚+告警不崩);
    - 正文取 CONTENT(图片/视频详情本轮跳过);空正文目录节点用 TITLE 兜底成块。
    """
    # 去重:真数据(探查 G1/G2)每节点有 2 物理行(**同 CODE 异 snowflake ID,内容一致**,
    # 全表 COUNT(DISTINCT CODE)恒=1)→ 按 CODE 保留首见。但**不假设内容必然一致**(Codex 二轮 F8):
    # 同 CODE 若 TITLE/CONTENT/IS_CATALOG/INDEX_NO 冲突(未来/异常数据),告警留痕再保留首见(确定性
    # 靠 DmSource 的 ORDER BY CODE,ID),不静默丢失冲突。无 CODE 行(罕见)不去重全保留。
    deduped: list[dict] = []
    by_code: dict[str, dict] = {}
    for c in _live(contents):
        code = _s(c.get("CODE"))
        if not code:
            deduped.append(c)
            continue
        if code in by_code:
            if _content_sig(c) != _content_sig(by_code[code]):
                warnings.append(f"同 CODE={code} 重复行内容冲突,保留首见")
            continue
        by_code[code] = c
        deduped.append(c)
    ordered = sorted(deduped, key=lambda r: (_int(r.get("INDEX_NO")), str(r.get("CODE") or "")))
    out: list[dict] = []
    for seq, c in enumerate(ordered):
        title = _s(c.get("TITLE"))
        is_catalog = _int(c.get("IS_CATALOG")) == 1
        text = _s(c.get("CONTENT")) or (title if is_catalog else None)
        if not text:  # 正文块无内容 → 跳过(reader 要求 text 非空)
            continue
        rec: dict = {"block_seq": seq, "text": text, "is_catalog": is_catalog}
        if title:
            rec["clause_label"] = title
        sc = _s(c.get("CODE"))
        if sc and len(sc) > _COL_WIDTHS["source_code"]:  # 锚超列宽 → 弃锚(回落 fuzzy),不崩
            warnings.append(f"LAW_CONTENT.CODE 超 {_COL_WIDTHS['source_code']} 弃锚:{sc[:40]}…")
            sc = None
        if sc:
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
    """一条 CASE_PARTY → persons 条目(全字段照带,D6;None 略去)。"""
    return {dst: party.get(src) for src, dst in _PARTY_FIELDS.items() if party.get(src) is not None}


def _violated(punish: dict) -> dict:
    """一条 CASE_PUNISH → violated_regulations 条目(**精确桥接锚**;字段语义按真数据校正)。

    真数据(2026-07-14 探查 C2):``PUNISH_LAW``=**法规名**(如"上市公司信息披露管理办法(2025年修订)")、
    ``PUNISH_LAW_TITLE``=**条款标识**(如"第二十二条")——与知识库文档描述相反,**以数据为准**。
    精确桥接靠 ``LAW_CONTENT_CODE``(真数据 96.7% 命中);title/clause 仅无锚时 fuzzy 回落用,故须摆对。
    """
    v: dict = {"title": _s(punish.get("PUNISH_LAW")) or "(未标注)"}  # 法规名
    if clause := _s(punish.get("PUNISH_LAW_TITLE")):  # 条款标识 第X条(fuzzy 回落)
        v["clause_label"] = clause
    if content := _s(punish.get("CONTENT")):
        v["content"] = content
    if lc := _s(punish.get("LAW_CODE")):
        v["law_code"] = lc
    if lcc := _s(punish.get("LAW_CONTENT_CODE")):
        v["law_content_code"] = lcc  # → 直连 chunks.source_code(精确桥接)
    return v


def case_record(case: dict, parties: list[dict], punishes: list[dict]) -> dict:
    """CASE_BASIC + PARTY + PUNISH → cases.jsonl 记录(reader.PresegCase 契约)。

    落 PG 定长列的字段(source_case_id/doc_number/issuing_org/case_type/source_url)经 ``_bound``
    边界校验:超列宽 → 拒收该案(Codex 二轮 F3);problem_summary/description 是 chunk 文本,不限长。
    """
    persons = [_person(p) for p in sorted(_live(parties), key=lambda r: _int(r.get("PARTY_INDEX")))]
    vregs = [
        _violated(p) for p in sorted(_live(punishes), key=lambda r: _int(r.get("PUNISH_INDEX")))
    ]
    rec: dict = {"case_name": _bound(case.get("NAME"), "case_name") or "(未命名案件)",
                 "persons": persons, "violated_regulations": vregs}
    # 落 PG 定长列 → _bound 拒收超宽;(源列, cases.jsonl 键, _COL_WIDTHS 键)
    for src, dst, wkey in (
        ("CODE", "source_case_id", "source_case_id"),
        ("DOC_NO", "doc_number", "case_doc_number"),
        ("PUB_AUTH_CN", "issuing_org", "penalty_org"),
        ("DOC_TYPE", "case_type", "case_type"),
        ("URL", "source_url", "source_url"),
    ):
        if val := _bound(case.get(src), wkey):
            rec[dst] = val
    for src, dst in (("SUMMARY", "problem_summary"), ("CASE_DESC", "description")):  # chunk 文本
        if val := _s(case.get(src)):
            rec[dst] = val
    if d := _date(case.get("PUB_DATE")):
        rec["issue_date"] = d
    if d := _date(case.get("EVENT_DATE")):
        rec["occurred_at"] = d
    if tag := _s(case.get("TAG")):
        rec["tags"] = [t for t in tag.split(";") if t.strip()]
    return rec


def manifest_row(law: dict, blocks: list[dict], filename: str) -> dict:
    """LAW_BASIC + 其 blocks → manifest 行(21 列全集)。分类走 SCOPE(fail-closed);
    落 PG 定长列经 ``_bound`` 超宽即拒收(不截断法律元数据;Codex 二轮 F2)。

    content_hash = 本次 blocks 语义规范化指纹(声明==实际;S0 交叉核验恒真,源内容变即指纹变)。
    """
    classified = classify_scope(law.get("SCOPE"))
    if classified is None:  # fail-closed:SCOPE 未知/空 → 拒收该件,绝不默认 public
        raise PresegExportError(
            f"SCOPE={law.get('SCOPE')!r} 不可判(须 0外/1内/2标准)→ 拒收 CODE={law.get('CODE')!r}"
        )
    corpus, perm = classified
    row = dict.fromkeys(PRESEG_REQUIRED_COLUMNS, None)
    parsed = parse_blocks("\n".join(json.dumps(b, ensure_ascii=False) for b in blocks), filename)
    row.update(
        filename=filename,
        title=_bound(law.get("NAME"), "title"),
        doc_number=_bound(law.get("DOC_NO"), "doc_number"),
        issuer=_bound(law.get("ISSUE_AUTH_CN"), "issuer"),
        perm_tag=perm,
        corpus_type=corpus,
        sub_type=_bound(law.get("LEVELS"), "sub_type"),
        issue_date=_date(law.get("ISSUE_DATE")),
        effective_date=_date(law.get("EFFECT_DATE")),
        invalid_date=_date(law.get("INVALID_DATE")),
        source_doc_id=_bound(law.get("CODE"), "source_doc_id"),  # 逻辑键(跨版本稳)
        content_hash=blocks_content_hash(parsed),
        effective_status=effective_status_of(law.get("STATUS_CODE")),
        issuer_level_src=_bound(law.get("LEVELS"), "issuer_level_src"),
        tags=_s(law.get("TAG")),
        file_no=_bound(law.get("DOC_NO"), "file_no"),
        source_law_id=_bound(law.get("SOURCE_LAW_ID"), "source_law_id"),
        entity_types=entity_types_of(law.get("SUIT_OBJ_CODE")),  # 适用对象(D7,写投影)
        source_created_by=_s(law.get("CREATOR_ID")),  # 源创建人(provenance)
    )
    # biz_domain:LAW_BASIC 无干净专列(仍 seam);supersedes 由 SOURCE_LAW_ID/ABOLISH_CODE 生成待定
    return row


def _safe_filename(law_code: str) -> str:
    """CODE → blocks 文件名:清洗基名 + CODE 稳定哈希后缀(**单射**,防不同 CODE 清洗后同名覆盖;
    承 s0 ``fn==Path(fn).name`` 防穿越)。"""
    base = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in law_code)[:40] or "law"
    return f"{base}-{hashlib.sha1(law_code.encode('utf-8')).hexdigest()[:8]}"  # noqa: S324


# ── 编排 + 落盘 ──────────────────────────────────────────────────────────────


def build_batch(source: Source, out_dir: Path) -> dict:
    """8 表 → 批次目录。返回统计(含 skipped/warnings)。**只写目录,不入库**。

    **原子替换**(Codex 二轮 F4):先在同父目录的 staging 临时目录完整构建(源读取/落盘都在此),
    成功后才 rmtree 旧目录 + ``os.replace`` 原子换入——**源读取中途异常绝不销毁已有有效批次**,
    也不留半批产物(异常时清 staging,out_dir 原样)。"""
    out_dir = Path(out_dir)
    out_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(dir=out_dir.parent, prefix=f".{out_dir.name}.staging-"))
    try:
        stats = _build_into(source, staging)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    if out_dir.exists():  # 仅在 staging 构建成功后才动 out_dir
        shutil.rmtree(out_dir)
    os.replace(staging, out_dir)  # 同文件系统原子 rename
    stats["out_dir"] = str(out_dir)
    return stats


def _build_into(source: Source, work: Path) -> dict:
    """把 8 表转换产物写入(空的)work 目录。仅由 build_batch 在 staging 上调用。"""
    (work / "blocks").mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    warnings: list[str] = []
    skipped: list[str] = []
    seen_keys: set[str] = set()
    seen_files: set[str] = set()
    for law in _live(source.iter_laws()):
        code = _s(law.get("CODE"))
        if not code:
            skipped.append("(law 无 CODE)")
            continue
        if str(law.get("STATUS_CODE") or "").strip() in SKIP_STATUS:  # test_run 测试数据不入库
            skipped.append(f"test_run CODE={code}")
            continue
        if code in seen_keys:  # 重复 CODE → 跳过(防同键覆盖/幂等歧义)
            skipped.append(f"重复 CODE={code}")
            continue
        try:
            blocks = blocks_from_contents(source.contents_for(code), warnings)
            if not blocks:
                skipped.append(f"无正文 CODE={code}")
                continue
            fn = _safe_filename(code)
            if fn in seen_files:  # 哈希后缀后仍撞(极罕见)→ 拒收该件不覆盖
                skipped.append(f"文件名碰撞 CODE={code}")
                continue
            row = manifest_row(law, blocks, fn)
        except PresegExportError as e:  # fail-closed(SCOPE 不可判 / 字段超列宽)→ 拒收该件 + 审计
            skipped.append(str(e))
            continue
        seen_keys.add(code)
        seen_files.add(fn)
        (work / "blocks" / f"{fn}.jsonl").write_text(
            "\n".join(json.dumps(b, ensure_ascii=False) for b in blocks) + "\n", encoding="utf-8"
        )
        rows.append(row)

    cases = []
    for c in _live(source.iter_cases()):
        if not _s(c.get("CODE")):
            continue
        code = _s(c.get("CODE"))
        try:
            cases.append(case_record(c, source.parties_for(code), source.punishes_for(code)))
        except PresegExportError as e:  # 案例字段超列宽 → 拒收该案 + 审计(F3)
            skipped.append(str(e))
    if cases:  # 无案例则不写(staging 全新,不会残留旧 cases.jsonl)
        (work / "cases.jsonl").write_text(
            "\n".join(json.dumps(c, ensure_ascii=False) for c in cases) + "\n", encoding="utf-8"
        )
    _write_manifest(work / "manifest.xlsx", rows)
    return {"laws": len(rows), "cases": len(cases), "skipped": skipped,
            "warnings": warnings, "out_dir": str(work)}


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


class Source(Protocol):
    """8 表读取接口。行统一为 ``dict``(键=大写列名);DEL_FLAG 过滤由本模块施加。"""

    def iter_laws(self) -> list[dict]: ...
    def contents_for(self, law_code: str) -> list[dict]: ...
    def iter_cases(self) -> list[dict]: ...
    def parties_for(self, case_code: str) -> list[dict]: ...
    def punishes_for(self, case_code: str) -> list[dict]: ...
