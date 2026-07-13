"""达梦源库 → 预切块批次目录导出入口(``python -m pipeline.preseg_export <out_dir>``)。

甲方内网法规制度平台(达梦 DM8,8 表)→ 批次目录(SPEC-PRESEG §3 接收契约),之后由
``python -m pipeline.preseg_ingest <out_dir>`` 灌库。两步分离:导出可离线核对产物,灌库需 PG/模型栈。

**连接**:达梦 DSN 走环境变量 ``PRESEG_SOURCE_DSN``(如 ``dm+dmPython://user:pwd@host:5236``;
驱动 ``dmPython`` + SQLAlchemy ``dm`` 方言,信创内网离线装)。不硬编码凭证/方言,适配部署实际。

**为何 ``python -m``**:同 preseg_ingest——生产构建剔除 console_scripts,``python -m`` 不受影响,
给运维稳定入口。转换纯逻辑在 ``pipeline.preseg.export``(FakeSource 单测);本模块只做连接 + 编排。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from pipeline.preseg.export import Source, build_batch

# ── 达梦 8 表读取(部署期真库联调;列集对齐知识库结构.md)────────────────────────

_LAW_COLS = (
    "CODE, NAME, DOC_NO, ISSUE_AUTH_CN, ISSUE_DATE, EFFECT_DATE, INVALID_DATE, "
    "STATUS_CODE, SOURCE_LAW_ID, LEVELS, TAG, DEL_FLAG"
)
_CONTENT_COLS = "CODE, LAW_CODE, PATH_CODE, IS_CATALOG, TITLE, INDEX_NO, CONTENT, DEL_FLAG"
_CASE_COLS = (
    "CODE, NAME, DOC_NO, PUB_AUTH_CN, PUB_DATE, DOC_TYPE, EVENT_DATE, "
    "CASE_DESC, SUMMARY, URL, TAG, DEL_FLAG"
)
_PARTY_COLS = (
    "PARTY_INDEX, CASE_CODE, NAME, TYPE_CN, IDENTITY_CN, VIOL_TYPE_CN, FINE_AMT, "
    "CONFISCATE_AMT, CRIM_FINE_AMT, PUNISH_CUR_CN, AFFILIATION, SEC_CODE, SEC_SNAME, "
    "IND_CN, DISTRICT_CN, SECTOR_CN, HANDLER, STATUS, DEL_FLAG"
)
_PUNISH_COLS = (
    "CASE_CODE, LAW_CODE, LAW_CONTENT_CODE, PUNISH_INDEX, PUNISH_LAW, "
    "PUNISH_LAW_TITLE, CONTENT, DEL_FLAG"
)


class DmSource(Source):
    """达梦源实现:SQLAlchemy 只读查 8 表,行 → dict(大写列名键)。表间为应用层 CODE 关联
    (源库无物理外键,知识库结构.md §3),故子表按父级 CODE 显式过滤。"""

    def __init__(self, engine) -> None:
        self._engine = engine

    def _rows(self, sql: str, **params: object) -> list[dict]:
        from sqlalchemy import text

        with self._engine.connect() as c:
            return [dict(r._mapping) for r in c.execute(text(sql), params)]

    def iter_laws(self) -> list[dict]:
        return self._rows(f"SELECT {_LAW_COLS} FROM ZNFG_IAM_LAW_BASIC WHERE DEL_FLAG <> 'D'")

    def contents_for(self, law_code: str) -> list[dict]:
        return self._rows(
            f"SELECT {_CONTENT_COLS} FROM ZNFG_IAM_LAW_CONTENT "
            "WHERE LAW_CODE = :c AND DEL_FLAG <> 'D'",
            c=law_code,
        )

    def iter_cases(self) -> list[dict]:
        return self._rows(f"SELECT {_CASE_COLS} FROM ZNFG_IAM_LAW_CASE_BASIC WHERE DEL_FLAG <> 'D'")

    def parties_for(self, case_code: str) -> list[dict]:
        return self._rows(
            f"SELECT {_PARTY_COLS} FROM ZNFG_IAM_LAW_CASE_PARTY "
            "WHERE CASE_CODE = :c AND DEL_FLAG <> 'D'",
            c=case_code,
        )

    def punishes_for(self, case_code: str) -> list[dict]:
        return self._rows(
            f"SELECT {_PUNISH_COLS} FROM ZNFG_IAM_LAW_CASE_PUNISH "
            "WHERE CASE_CODE = :c AND DEL_FLAG <> 'D'",
            c=case_code,
        )


def run(out_dir: Path, dsn: str | None = None) -> int:
    dsn = dsn or os.environ.get("PRESEG_SOURCE_DSN")
    if not dsn:
        print("✗ 未提供达梦 DSN(--dsn 或 env PRESEG_SOURCE_DSN)")
        return 2
    from sqlalchemy import create_engine

    engine = create_engine(dsn)
    stats = build_batch(DmSource(engine), out_dir)
    print(f"✓ 导出批次 → {stats['out_dir']}:laws={stats['laws']} cases={stats['cases']}")
    print(f"  下一步:python -m pipeline.preseg_ingest {stats['out_dir']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m pipeline.preseg_export",
        description="达梦源库 8 表 → 预切块批次目录(SPEC-PRESEG §3 接收契约)。",
    )
    ap.add_argument(
        "out_dir", type=Path, help="批次输出目录(生成 manifest.xlsx + blocks/ + cases.jsonl)"
    )
    ap.add_argument("--dsn", default=None, help="达梦 DSN(默认取 env PRESEG_SOURCE_DSN)")
    args = ap.parse_args(argv)
    return run(args.out_dir, args.dsn)


if __name__ == "__main__":
    sys.exit(main())
