"""P-PRESEG 块流 → ChunkSpec 适配器(CP-010 T7,SPEC-PRESEG §4-S3)。

**搬运保真**:源块文本原样入 chunk(不加面包屑前缀、不改写正文——源即权威切块);
本模块只做四件事:norm 推导接线(章节上下文继承)、seq 分配(同 norm 多块递增)、
超预算二次切分(复用 chunker._split_oversize 纯文本链)、文档级适用对象继承(D7)。

- 章/节标题块:更新上下文,**不成块**(与条款树"章节点不出 chunk"一致);
- 推导失败块:伪路径 ``preseg/{block_seq}`` + ``chunk_type="preseg_raw"``(仿 qa/case 先例),
  可检索、不参与引用末段对齐,批次占比是推导器健康信号(告警阈值 ⚠ 待样例标定);
- 零页码(D2):page_start/page_end 恒 None,引用三级;
- chunk_id 走既有 ``compute_chunk_id(dvid, norm, seq)``,幂等契约不变(B1)。
"""

from __future__ import annotations

from collections import Counter

from common.chunk_id import compute_chunk_id
from pipeline.chunking.chunker import ChunkSpec, _split_oversize, count_tokens
from pipeline.config import ChunkConfig
from pipeline.preseg.derive import derive_norm
from pipeline.preseg.reader import PresegBlock


def build_preseg_specs(
    dvid: str,
    blocks: list[PresegBlock],
    cfg: ChunkConfig,
    entity_types: list | None = None,
) -> list[ChunkSpec]:
    """预切块块流 → 同构 ChunkSpec 列表(下游 s3 落库/s5 索引零感知)。"""
    specs: list[ChunkSpec] = []
    seq_counter: Counter[str] = Counter()
    chapter: str | None = None
    section: str | None = None
    chapter_raw: str | None = None
    section_raw: str | None = None

    for b in sorted(blocks, key=lambda x: x.block_seq):
        d = derive_norm(b.clause_label)
        if d.kind == "chapter":  # 结构标题:更新上下文,不成块(换章清空节)
            chapter, chapter_raw = d.chapter, (b.clause_label or "").strip()
            section, section_raw = None, None
            continue
        if d.kind == "section":
            chapter = d.chapter or chapter
            section, section_raw = d.section, (b.clause_label or "").strip()
            continue

        if d.kind == "article":
            parts = [d.chapter or chapter, d.section or section, d.norm]
            norm = "/".join(p for p in parts if p)
            chunk_type = "table" if b.is_table else "clause"
        else:  # 推导失败 → 伪路径,不阻塞入库(降级留痕)
            norm = f"preseg/{b.block_seq}"
            chunk_type = "table" if b.is_table else "preseg_raw"

        label = (b.clause_label or "").strip()
        breadcrumb = " > ".join(p for p in (chapter_raw, section_raw, label) if p)
        pieces = (
            _split_oversize(b.text, cfg.target_token_max)
            if count_tokens(b.text) > cfg.target_token_max
            else [(b.text, False)]
        )
        for piece, hard_cut in pieces:
            seq = seq_counter[norm]
            seq_counter[norm] += 1
            specs.append(
                ChunkSpec(
                    chunk_id=compute_chunk_id(dvid, norm, seq),
                    doc_version_id=dvid,
                    clause_path=label,
                    clause_path_norm=norm,
                    seq=seq,
                    text=piece,
                    breadcrumb=breadcrumb,
                    page_start=None,  # D2 零页码,引用三级
                    page_end=None,
                    token_count=count_tokens(piece),
                    is_table=b.is_table,
                    oversize=hard_cut,
                    chunk_type=chunk_type,
                    entity_type=list(entity_types) if entity_types else None,  # D7 文档级继承
                )
            )
    return specs
