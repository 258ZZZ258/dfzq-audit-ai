"""P-PRESEG 接收契约 reader(CP-010 T3,SPEC-PRESEG §3)。

批次目录 = 扩展 manifest.xlsx + blocks/<filename>.jsonl(每文档一件)+ cases.jsonl(案例批次)。
源系统实际导出形态由**批次目录之外的薄转换脚本**吸收(接缝);本模块只认本契约,坏输入
**整文件拒收**(行级错误汇总报出),绝不带病入库。

口径钉子(SPEC 精化,详见 preseg_devlog):manifest 的 ``corpus_type`` 列仍填
``P-INT/P-EXT/P-CASE``——它是 Milvus 分区/检索归属(B6),preseg 落自立分区会脱出主检索;
preseg **通道性**由扩展列(source_doc_id/content_hash)与 dv 级 ``source_format="preseg"``
标识,QC 档案键才用 ``P-PRESEG``(profiles.yaml)。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from common.manifest import REQUIRED_COLUMNS

#: 扩展列(SPEC §3.2):幂等键两分量 + 源元数据。列集精确匹配,缺/多整批拒收(承 s0 语义)。
PRESEG_EXTRA_COLUMNS = [
    "source_doc_id", "content_hash", "effective_status",
    "issuer_level_src", "tags", "file_no", "source_created_by",
]
PRESEG_REQUIRED_COLUMNS = [*REQUIRED_COLUMNS, *PRESEG_EXTRA_COLUMNS]

#: corpus_type 允许值 = 检索分区归属(见模块 docstring 口径钉子)
_ALLOWED_CORPUS = {"P-INT", "P-EXT", "P-CASE"}


class PresegFormatError(ValueError):
    """接收契约违约(列集/行级校验失败)→ 整文件/整批拒收。"""


@dataclass(frozen=True)
class PresegBlock:
    block_seq: int
    text: str
    clause_label: str | None = None
    is_table: bool = False


@dataclass(frozen=True)
class ViolatedReg:
    title: str
    clause_label: str | None = None
    content: str | None = None


@dataclass(frozen=True)
class PresegCase:
    case_name: str
    source_case_id: str | None = None  # 源系统案例主键(幂等键;缺失时退 record_hash)
    record_hash: str = ""  # reader 计算的记录规范化哈希(幂等键第二分量,源侧无需提供)
    doc_number: str | None = None  # 发文文号
    issuing_org: str | None = None  # 发文单位
    issue_date: str | None = None  # 发文日期(ISO 串,类型待样例)
    occurred_at: str | None = None  # 发生时间
    case_type: str | None = None
    problem_summary: str | None = None  # → case_summary chunk 文本源
    description: str | None = None  # → case_section chunk 文本源
    source_url: str | None = None
    tags: list = field(default_factory=list)
    violated_regulations: list[ViolatedReg] = field(default_factory=list)
    persons: list[dict] = field(default_factory=list)  # 原样照存 cases.persons JSONB(D6)


def validate_manifest_header(header: list) -> None:
    """列集**精确匹配**(与 s0 制度类 manifest 同语义):缺列/多列整批拒收。"""
    got = [str(h) for h in header if h is not None]
    missing = [c for c in PRESEG_REQUIRED_COLUMNS if c not in got]
    extra = [c for c in got if c not in PRESEG_REQUIRED_COLUMNS]
    if missing or extra:
        raise PresegFormatError(
            f"P-PRESEG manifest 列集不匹配:缺失 {missing or '无'};多余 {extra or '无'}"
        )


def validate_manifest_rows(rows: list[dict]) -> None:
    """行级契约:幂等键两分量非空 + corpus_type 必须是检索分区值。"""
    errors: list[str] = []
    for i, row in enumerate(rows, start=2):  # xlsx 数据行从第 2 行起
        for key in ("source_doc_id", "content_hash"):
            if not str(row.get(key) or "").strip():
                errors.append(f"row {i}: 幂等键 {key} 为空")
        ct = str(row.get("corpus_type") or "").strip()
        if ct not in _ALLOWED_CORPUS:
            errors.append(
                f"row {i}: corpus_type={ct!r} 非法——preseg 批次仍须填检索分区值 "
                f"{sorted(_ALLOWED_CORPUS)}(通道性由扩展列标识,勿填 P-PRESEG)"
            )
    if errors:
        raise PresegFormatError("manifest 行级校验失败:\n" + "\n".join(errors))


def read_blocks(path: Path) -> list[PresegBlock]:
    """blocks JSONL → 按 block_seq 升序的块列表;任何行级违约汇总后整文件拒收。"""
    records = _read_jsonl(path)
    if not records:
        raise PresegFormatError(f"{path.name}: 空 blocks 文件")
    errors: list[str] = []
    blocks: list[PresegBlock] = []
    seen_seq: set[int] = set()
    for lineno, rec in records:
        seq = rec.get("block_seq")
        text = rec.get("text")
        if not isinstance(seq, int):
            errors.append(f"line {lineno}: block_seq 缺失或非整数")
            continue
        if seq in seen_seq:
            errors.append(f"line {lineno}: block_seq={seq} 重复")
            continue
        if not isinstance(text, str) or not text.strip():
            errors.append(f"line {lineno}: text 缺失或为空")
            continue
        seen_seq.add(seq)
        blocks.append(
            PresegBlock(
                block_seq=seq,
                text=text,
                clause_label=rec.get("clause_label") or None,
                is_table=bool(rec.get("is_table", False)),
            )
        )
    if errors:
        raise PresegFormatError(f"{path.name} 行级校验失败:\n" + "\n".join(errors))
    return sorted(blocks, key=lambda b: b.block_seq)


def read_cases(path: Path) -> list[PresegCase]:
    """cases JSONL → PresegCase 列表;case_name 必填、violated_regulations[].title 必填、
    persons 须为 list(内容原样照存,D6 不做字段级裁剪)。"""
    records = _read_jsonl(path)
    if not records:
        raise PresegFormatError(f"{path.name}: 空 cases 文件")
    errors: list[str] = []
    cases: list[PresegCase] = []
    for lineno, rec in records:
        if not str(rec.get("case_name") or "").strip():
            errors.append(f"line {lineno}: case_name 必填")
            continue
        persons = rec.get("persons", [])
        if not isinstance(persons, list):
            errors.append(f"line {lineno}: persons 必须是 list")
            continue
        vregs: list[ViolatedReg] = []
        bad = False
        for j, v in enumerate(rec.get("violated_regulations", [])):
            if not str((v or {}).get("title") or "").strip():
                errors.append(f"line {lineno}: violated_regulations[{j}].title 必填")
                bad = True
                break
            vregs.append(
                ViolatedReg(
                    title=v["title"],
                    clause_label=v.get("clause_label") or None,
                    content=v.get("content") or None,
                )
            )
        if bad:
            continue
        cases.append(
            PresegCase(
                case_name=rec["case_name"],
                source_case_id=str(rec.get("source_case_id") or "") or None,
                record_hash=_record_hash(rec),
                doc_number=rec.get("doc_number") or None,
                issuing_org=rec.get("issuing_org") or None,
                issue_date=rec.get("issue_date") or None,
                occurred_at=rec.get("occurred_at") or None,
                case_type=rec.get("case_type") or None,
                problem_summary=rec.get("problem_summary") or None,
                description=rec.get("description") or None,
                source_url=rec.get("source_url") or None,
                tags=rec.get("tags") or [],
                violated_regulations=vregs,
                persons=persons,
            )
        )
    if errors:
        raise PresegFormatError(f"{path.name} 行级校验失败:\n" + "\n".join(errors))
    return cases


def _record_hash(rec: dict) -> str:
    """记录规范化哈希(sort_keys JSON → sha256):案例幂等键第二分量,源侧无需提供。"""
    canon = json.dumps(rec, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def _read_jsonl(path: Path) -> list[tuple[int, dict]]:
    """JSONL → [(行号, 记录)];坏 JSON 行汇总拒收。"""
    errors: list[str] = []
    out: list[tuple[int, dict]] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError as e:
            errors.append(f"line {lineno}: 非法 JSON({e.msg}")
            continue
        if not isinstance(rec, dict):
            errors.append(f"line {lineno}: 记录须为 JSON object")
            continue
        out.append((lineno, rec))
    if errors:
        raise PresegFormatError(f"{path.name} JSON 解析失败:\n" + "\n".join(errors))
    return out
