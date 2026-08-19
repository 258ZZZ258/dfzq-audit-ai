"""仅导出两部外网测试 OCR 外规为 P-PRESEG 批次。

避免联调时把 mock source 的整库法规重新导出、重灌到目标检索库。输入源仍复用
``DmSource`` 和 ``build_batch``，所以产物与内网迁移接缝完全一致。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from pipeline.preseg.export import build_batch
from pipeline.preseg_export import DmSource
from tools.mock_source.seed_external_ocr_test_regulations import SPECS


class SelectedLawsSource:
    """将读取范围约束在本测试脚本拥有的两个固定法规 CODE。"""

    def __init__(self, source: DmSource) -> None:
        self._source = source
        self._codes = {str(spec["code"]) for spec in SPECS}

    def iter_laws(self):
        return [law for law in self._source.iter_laws() if str(law.get("CODE") or "") in self._codes]

    def contents_for(self, law_code: str):
        return self._source.contents_for(law_code)

    def content_details_for(self, law_code: str):
        return self._source.content_details_for(law_code)

    def iter_cases(self):
        return []

    def parties_for(self, case_code: str):
        return []

    def punishes_for(self, case_code: str):
        return []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="导出两部外网测试 OCR 外规")
    parser.add_argument("out_dir", type=Path)
    args = parser.parse_args(argv)
    dsn = os.environ.get("PRESEG_SOURCE_DSN")
    if not dsn:
        print("✗ 未设置 PRESEG_SOURCE_DSN")
        return 2

    from sqlalchemy import create_engine

    engine = create_engine(dsn)
    conn = engine.connect().execution_options(isolation_level="REPEATABLE READ")
    try:
        with conn.begin():
            stats = build_batch(SelectedLawsSource(DmSource(conn)), args.out_dir)
    finally:
        conn.close()
    print(f"✓ 外网测试 OCR 批次已导出 → {stats['out_dir']}: laws={stats['laws']}")
    for warning in stats.get("warnings", []):
        print(f"  ⚠ {warning}")
    for skipped in stats.get("skipped", []):
        print(f"  ⨯ {skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
