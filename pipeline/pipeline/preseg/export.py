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
from datetime import date, datetime
from pathlib import Path
from typing import Protocol

from pipeline.preseg.reader import PRESEG_REQUIRED_COLUMNS, blocks_content_hash, parse_blocks


class PresegExportError(ValueError):
    """转换期不可安全导出(如源键超列宽、分类不可判)——拒收该件,不带病产出。"""


# ── 源语义映射(依据真实 schema;仍待样例的以 seam 标注)──────────────────────

#: DEL_FLAG 在册标记:排除 D(删除);A 有效、U 修改视为在册。⚠ U 处置待甲方确认。
LIVE_DEL_FLAGS = frozenset({"A", "U"})

#: PG 描述列宽(截断上限;键列不在此表——键超宽必须拒收而非截断)
_COL_WIDTHS = {
    "title": 512, "issuer": 128, "doc_number": 128, "sub_type": 32,
    "issuer_level_src": 64, "file_no": 128, "case_name": 512,
}
#: 源键列最大安全宽(迁移 0016 拓宽后):超过即拒收(截断键会破坏幂等/桥接)
_KEY_MAXLEN = 256


def classify_scope(scope: object) -> tuple[str, str] | None:
    """SCOPE(ZNFG_IAM_LAW_BASIC.SCOPE)→ (corpus_type, perm_tag)。**权威分类,fail-closed**。

    0=external→(P-EXT, public);1=internal→(P-INT, internal);2=criterion 标准→(P-EXT, internal)
    (外规分区但密级保守,待甲方确认可否 public);**未知/空 → None(调用方拒收,绝不默认 public)**。
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
    """PATH_CODE(VARCHAR(1020) 层级路径)→ clause_path_norm。⚠ seam:**内部分段格式未知 → 返 None**,
    由 adapter 从 TITLE 派生(is_catalog 权威判目录块仍生效)。真格式确认后在此产出权威 norm。"""
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


def _fit(value: object, field: str, warnings: list[str]) -> str | None:
    """描述列按 PG 列宽截断 + 记审计告警(不 DataError 中断批次)。键列不走此路。"""
    s = _s(value)
    if s is None:
        return None
    maxlen = _COL_WIDTHS[field]
    if len(s) > maxlen:
        warnings.append(f"{field} 超列宽 {maxlen}(源长 {len(s)})→ 截断:{s[:20]}…")
        return s[:maxlen]
    return s


def _key(value: object, field: str) -> str | None:
    """源键列:超安全宽 → 拒收(截断键会破坏幂等/桥接);正常返原值。"""
    s = _s(value)
    if s is not None and len(s) > _KEY_MAXLEN:
        raise PresegExportError(f"{field} 长度 {len(s)} 超键列上限 {_KEY_MAXLEN}:{s[:40]}…")
    return s


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
    ordered = sorted(
        _live(contents), key=lambda r: (_int(r.get("INDEX_NO")), str(r.get("CODE") or ""))
    )
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
        if sc and len(sc) > _KEY_MAXLEN:  # 锚超宽 → 弃锚(回落 fuzzy),不崩
            warnings.append(f"LAW_CONTENT.CODE 超 {_KEY_MAXLEN} 弃锚:{sc[:40]}…")
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
    """一条 CASE_PUNISH → violated_regulations 条目(**精确桥接锚**)。"""
    title = _s(punish.get("PUNISH_LAW_TITLE")) or _s(punish.get("PUNISH_LAW")) or "(未标注)"
    v: dict = {"title": title}
    if content := _s(punish.get("CONTENT")) or _s(punish.get("PUNISH_LAW")):
        v["content"] = content
    if lc := _s(punish.get("LAW_CODE")):
        v["law_code"] = lc
    if lcc := _s(punish.get("LAW_CONTENT_CODE")):
        v["law_content_code"] = lcc  # → 直连 chunks.source_code(精确桥接)
    return v


def case_record(case: dict, parties: list[dict], punishes: list[dict], warnings: list[str]) -> dict:
    """CASE_BASIC + PARTY + PUNISH → cases.jsonl 记录(reader.PresegCase 契约)。"""
    persons = [_person(p) for p in sorted(_live(parties), key=lambda r: _int(r.get("PARTY_INDEX")))]
    vregs = [
        _violated(p) for p in sorted(_live(punishes), key=lambda r: _int(r.get("PUNISH_INDEX")))
    ]
    rec: dict = {"case_name": _fit(case.get("NAME"), "case_name", warnings) or "(未命名案件)",
                 "persons": persons, "violated_regulations": vregs}
    if code := _s(case.get("CODE")):
        rec["source_case_id"] = code
    for src, dst in (("DOC_NO", "doc_number"), ("PUB_AUTH_CN", "issuing_org"),
                     ("DOC_TYPE", "case_type"), ("SUMMARY", "problem_summary"),
                     ("CASE_DESC", "description"), ("URL", "source_url")):
        if val := _s(case.get(src)):
            rec[dst] = val
    if d := _date(case.get("PUB_DATE")):
        rec["issue_date"] = d
    if d := _date(case.get("EVENT_DATE")):
        rec["occurred_at"] = d
    if tag := _s(case.get("TAG")):
        rec["tags"] = [t for t in tag.split(";") if t.strip()]
    return rec


def manifest_row(law: dict, blocks: list[dict], filename: str, warnings: list[str]) -> dict:
    """LAW_BASIC + 其 blocks → manifest 行(21 列全集)。分类走 SCOPE(fail-closed);列宽保真。

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
        title=_fit(law.get("NAME"), "title", warnings),
        doc_number=_fit(law.get("DOC_NO"), "doc_number", warnings),
        issuer=_fit(law.get("ISSUE_AUTH_CN"), "issuer", warnings),
        perm_tag=perm,
        corpus_type=corpus,
        sub_type=_fit(law.get("LEVELS"), "sub_type", warnings),
        issue_date=_date(law.get("ISSUE_DATE")),
        effective_date=_date(law.get("EFFECT_DATE")),
        invalid_date=_date(law.get("INVALID_DATE")),
        source_doc_id=_key(law.get("CODE"), "CODE"),  # 逻辑键(跨版本稳)
        content_hash=blocks_content_hash(parsed),
        effective_status=effective_status_of(law.get("STATUS_CODE")),
        issuer_level_src=_fit(law.get("LEVELS"), "issuer_level_src", warnings),
        tags=_s(law.get("TAG")),
        file_no=_fit(law.get("DOC_NO"), "file_no", warnings),
        source_law_id=_key(law.get("SOURCE_LAW_ID"), "SOURCE_LAW_ID"),
    )
    # biz_domain=SUIT_OBJ_CODE(适用对象,真列已见)/ entity_types / source_created_by=CREATOR_ID:
    # 值域/编码表待甲方对接会锁 → 本轮留空(seam),不阻塞
    return row


def _safe_filename(law_code: str) -> str:
    """CODE → blocks 文件名:清洗基名 + CODE 稳定哈希后缀(**单射**,防不同 CODE 清洗后同名覆盖;
    承 s0 ``fn==Path(fn).name`` 防穿越)。"""
    base = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in law_code)[:40] or "law"
    return f"{base}-{hashlib.sha1(law_code.encode('utf-8')).hexdigest()[:8]}"  # noqa: S324


# ── 编排 + 落盘 ──────────────────────────────────────────────────────────────


def _clean_out_dir(out_dir: Path) -> None:
    """复用目录:清陈旧产物(blocks/*.jsonl + cases.jsonl + manifest.xlsx),避免旧案例/旧法规
    被再次摄取(Codex:空 cases 时旧 cases.jsonl 会残留)。"""
    (out_dir / "blocks").mkdir(parents=True, exist_ok=True)
    for p in (out_dir / "blocks").glob("*.jsonl"):
        p.unlink()
    for name in ("cases.jsonl", "manifest.xlsx"):
        f = out_dir / name
        if f.exists():
            f.unlink()


def build_batch(source: Source, out_dir: Path) -> dict:
    """8 表 → 批次目录。返回统计(含 skipped/warnings)。**只写目录,不入库**。"""
    out_dir = Path(out_dir)
    _clean_out_dir(out_dir)

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
            row = manifest_row(law, blocks, fn, warnings)
        except PresegExportError as e:  # fail-closed(SCOPE 不可判 / 键超宽)→ 拒收该件 + 审计
            skipped.append(str(e))
            continue
        seen_keys.add(code)
        seen_files.add(fn)
        (out_dir / "blocks" / f"{fn}.jsonl").write_text(
            "\n".join(json.dumps(b, ensure_ascii=False) for b in blocks) + "\n", encoding="utf-8"
        )
        rows.append(row)

    cases = []
    for c in _live(source.iter_cases()):
        code = _s(c.get("CODE"))
        if not code:
            continue
        cases.append(case_record(c, source.parties_for(code), source.punishes_for(code), warnings))
    if cases:  # 无案例则不写(_clean_out_dir 已删旧件,避免旧案例再摄取)
        (out_dir / "cases.jsonl").write_text(
            "\n".join(json.dumps(c, ensure_ascii=False) for c in cases) + "\n", encoding="utf-8"
        )
    _write_manifest(out_dir / "manifest.xlsx", rows)
    return {"laws": len(rows), "cases": len(cases), "skipped": skipped,
            "warnings": warnings, "out_dir": str(out_dir)}


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
