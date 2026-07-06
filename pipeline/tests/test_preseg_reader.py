"""T3 接收契约 reader:扩展 manifest 列集精确匹配 + blocks/cases JSONL 行级校验,
坏输入整文件拒收(行级错误汇总),合法 fixtures 全解析。(SPEC-PRESEG §3)"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from common.manifest import REQUIRED_COLUMNS
from pipeline.preseg.reader import (
    PRESEG_REQUIRED_COLUMNS,
    PresegFormatError,
    read_blocks,
    read_cases,
    validate_manifest_header,
    validate_manifest_rows,
)

FIXTURES = Path(__file__).parent / "fixtures" / "preseg_batch"


# ── manifest 列集 ──────────────────────────────────────────────


class TestManifestContract:
    def test_extends_base_eleven(self):
        assert PRESEG_REQUIRED_COLUMNS[: len(REQUIRED_COLUMNS)] == REQUIRED_COLUMNS
        assert set(PRESEG_REQUIRED_COLUMNS) >= {
            "source_doc_id", "content_hash", "effective_status",
            "issuer_level_src", "tags", "file_no", "source_created_by",
        }

    def test_exact_header_passes(self):
        validate_manifest_header(list(PRESEG_REQUIRED_COLUMNS))  # 不 raise

    def test_missing_column_rejected(self):
        header = [c for c in PRESEG_REQUIRED_COLUMNS if c != "source_doc_id"]
        with pytest.raises(PresegFormatError, match="source_doc_id"):
            validate_manifest_header(header)

    def test_extra_column_rejected(self):
        with pytest.raises(PresegFormatError, match="page_no"):
            validate_manifest_header([*PRESEG_REQUIRED_COLUMNS, "page_no"])

    def test_rows_require_idempotency_keys(self):
        row = dict.fromkeys(PRESEG_REQUIRED_COLUMNS, "x")
        row.update(corpus_type="P-INT", source_doc_id="", content_hash="h1")
        with pytest.raises(PresegFormatError, match="source_doc_id"):
            validate_manifest_rows([row])

    def test_rows_corpus_type_must_be_partition_value(self):
        # 检索分区归属:corpus_type 仍填 P-INT/P-EXT/P-CASE(preseg 通道性由扩展列标识,
        # 落 P-PRESEG 会脱出主检索分区——B6 红线,SPEC 精化,见 preseg_devlog)
        row = dict.fromkeys(PRESEG_REQUIRED_COLUMNS, "x")
        row.update(corpus_type="P-PRESEG", source_doc_id="s1", content_hash="h1")
        with pytest.raises(PresegFormatError, match="corpus_type"):
            validate_manifest_rows([row])

    def test_rows_valid_pass(self):
        row = dict.fromkeys(PRESEG_REQUIRED_COLUMNS, "x")
        row.update(corpus_type="P-EXT", source_doc_id="s1", content_hash="h1")
        validate_manifest_rows([row])  # 不 raise


# ── blocks JSONL ──────────────────────────────────────────────


class TestBlocks:
    def test_fixture_blocks_parse_sorted(self):
        blocks = read_blocks(FIXTURES / "blocks" / "ext-001.jsonl")
        assert len(blocks) >= 3
        assert [b.block_seq for b in blocks] == sorted(b.block_seq for b in blocks)
        assert all(b.text for b in blocks)
        assert any(b.clause_label for b in blocks)

    def test_missing_text_rejected_with_line_no(self, tmp_path):
        p = tmp_path / "bad.jsonl"
        p.write_text(
            '{"block_seq": 0, "text": "ok"}\n{"block_seq": 1}\n', encoding="utf-8"
        )
        with pytest.raises(PresegFormatError, match="line 2"):
            read_blocks(p)

    def test_duplicate_seq_rejected(self, tmp_path):
        p = tmp_path / "dup.jsonl"
        p.write_text(
            '{"block_seq": 0, "text": "a"}\n{"block_seq": 0, "text": "b"}\n',
            encoding="utf-8",
        )
        with pytest.raises(PresegFormatError, match="block_seq"):
            read_blocks(p)

    def test_empty_file_rejected(self, tmp_path):
        p = tmp_path / "empty.jsonl"
        p.write_text("", encoding="utf-8")
        with pytest.raises(PresegFormatError, match="空"):
            read_blocks(p)

    def test_bad_json_line_aggregated(self, tmp_path):
        p = tmp_path / "badjson.jsonl"
        p.write_text('{"block_seq": 0, "text": "a"}\nnot-json\n', encoding="utf-8")
        with pytest.raises(PresegFormatError, match="line 2"):
            read_blocks(p)


# ── cases JSONL ──────────────────────────────────────────────


class TestCases:
    def test_fixture_cases_parse(self):
        cases = read_cases(FIXTURES / "cases.jsonl")
        assert len(cases) >= 2
        c = cases[0]
        assert c.case_name and c.doc_number
        assert c.violated_regulations and c.violated_regulations[0].title
        assert isinstance(c.persons, list) and c.persons  # 多涉案人员样例
        # 边界样例:第二案含款级引用(kuan 由 derive 处理,reader 原样透传)
        labels = [
            v.clause_label for case in cases for v in case.violated_regulations
        ]
        assert any(lbl and "款" in lbl for lbl in labels)

    def test_case_name_required(self, tmp_path):
        p = tmp_path / "c.jsonl"
        p.write_text(json.dumps({"doc_number": "x"}) + "\n", encoding="utf-8")
        with pytest.raises(PresegFormatError, match="case_name"):
            read_cases(p)

    def test_violated_reg_title_required(self, tmp_path):
        rec = {"case_name": "A", "doc_number": "x",
               "violated_regulations": [{"clause_label": "第一条"}]}
        p = tmp_path / "c.jsonl"
        p.write_text(json.dumps(rec) + "\n", encoding="utf-8")
        with pytest.raises(PresegFormatError, match="title"):
            read_cases(p)

    def test_persons_must_be_list(self, tmp_path):
        rec = {"case_name": "A", "doc_number": "x", "persons": {"name": "张三"}}
        p = tmp_path / "c.jsonl"
        p.write_text(json.dumps(rec) + "\n", encoding="utf-8")
        with pytest.raises(PresegFormatError, match="persons"):
            read_cases(p)
