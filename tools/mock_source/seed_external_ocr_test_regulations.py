"""将人工截图 OCR 的两部外规写入 *外网测试* 仿真源库。

这不是正式法规入库工具：原始截图没有可靠的完整页码、条号和发布元数据，故导入件
明确标记为 ``外网测试OCR``。每一行 OCR 文本在源库中对应一个 ``OCR片段NNN``，以免把
推测出的条号误标为法律原文条号。它的用途仅是联调“外规 → 内规”的覆盖度比对。

输入来自本任务生成的 ``*-候选入库块.jsonl``；写入后仍走标准
``python -m pipeline.preseg_export`` / ``python -m pipeline.preseg_ingest``，不旁路生产
的源库转换契约。

示例::

    .venv/bin/python tools/mock_source/seed_external_ocr_test_regulations.py \
      --input-dir /tmp/audit-ai-regulation-parse-20260813 --dry-run
    .venv/bin/python tools/mock_source/seed_external_ocr_test_regulations.py \
      --input-dir /tmp/audit-ai-regulation-parse-20260813

重复执行是幂等的：仅删除并重建本文件声明的两个 ``ELAT...`` 测试 CODE，绝不会改动
其他仿真法规、案例或正式目标库数据。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

TEST_BATCH_DATE = date(2026, 8, 13)
TEST_CREATOR = "external_ocr_test"

# 固定 CODE 使批次重跑、导出和查询都可追溯；它们不是内网的真实 CODE。
SPECS = (
    {
        "title": "证券公司股权管理规定",
        "code": "ELAT6CF3E2AFD4EF08FDDAA9B0D0C0112",
        "filename": "证券公司股权管理规定-候选入库块.jsonl",
    },
    {
        "title": "证券公司治理准则",
        "code": "ELATCBEFB568218C02D925B9F95F14528",
        "filename": "证券公司治理准则-候选入库块.jsonl",
    },
)


def _id(seed: str) -> str:
    """源表 ID 形态使用稳定的 19 位数字，方便仿真源库对拍。"""
    return str(10**18 + int(hashlib.sha256(seed.encode("utf-8")).hexdigest()[:15], 16) % (9 * 10**18))


def _clean_text(value: object) -> str | None:
    text = " ".join(str(value or "").replace("|", " ").split())
    if not text:
        return None
    # 截图中的设备日期/纯页码不是法规正文，不能作为检索片段。
    if text in {"2026/8/13", "2026/8", "13"}:
        return None
    return text


def _load_blocks(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_no} 不是合法 JSONL") from exc
        text = _clean_text(raw.get("text"))
        images = raw.get("source_images") or []
        if text is None or not isinstance(images, list) or not all(isinstance(v, str) for v in images):
            continue
        rows.append({"text": text, "source_image": images[0] if images else "unknown"})
    if not rows:
        raise ValueError(f"{path} 没有可入库的 OCR 文本片段")
    return rows


def _build_rows(input_dir: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    laws: list[dict[str, Any]] = []
    contents: list[dict[str, Any]] = []
    details: list[dict[str, Any]] = []
    now = datetime.combine(TEST_BATCH_DATE, datetime.min.time())

    for spec in SPECS:
        code = spec["code"]
        law_id = _id(f"{code}:law")
        blocks = _load_blocks(input_dir / spec["filename"])
        laws.append({
            "etl_src_date": int(TEST_BATCH_DATE.strftime("%Y%m%d")),
            "etl_src_code": "TEST",
            "id": law_id,
            "old_id": "",
            "scope": 0,
            "code": code,
            "name": f"{spec['title']}（外网测试OCR）",
            "doc_no": "外网测试OCR-20260813",
            "issue_auth_cn": "测试数据（OCR截图来源待复核）",
            "issue_auth_code": "TEST_OCR",
            "suit_obj_code": "证券",
            # 这是*测试入库时间*，不是法规实际发布/生效日期；TAG 和 modify_info 同样声明。
            "issue_date": TEST_BATCH_DATE,
            "invalid_date": None,
            "effect_date": TEST_BATCH_DATE,
            "status_code": "inuse",
            "modify_info": "仅用于外网覆盖度联调；正式法规日期、文号、条号须人工核验后重导。",
            "forbidden_msg": "",
            "source_law_id": "",
            "levels": '["EXTERNAL_TEST_OCR"]',
            "tag": "external-test;ocr-pending-review;20260813",
            "create_time": now,
            "update_time": now,
            "del_flag": "A",
            "has_content": 1,
            "data_version": "external-test-ocr-v1",
            "owner": 0,
            "source": 0,
            "new_code": "",
            "creator_id": TEST_CREATOR,
            "updator_id": TEST_CREATOR,
            "ext_01": "external-test",
            "ext_02": "ocr-pending-review",
            "ext_03": "source-images-20260813",
            "abolish_code": "",
        })

        # 根节点和按截图页分组的目录节点，只承担树形结构；正文均位于详情表。
        root_code = f"{code}000"
        contents.extend(_content_rows(code, law_id, root_code, root_code, 0, 0, "", now))
        by_image: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for block in blocks:
            by_image[block["source_image"]].append(block)

        seq = 1
        index_no = 1
        for page_no, page_blocks in enumerate(by_image.values(), start=1):
            catalog_code = f"{code}{seq:03d}"
            contents.extend(_content_rows(
                code, law_id, catalog_code, catalog_code, 1, index_no,
                f"截图页 {page_no}（OCR测试分段，非正式章节）", now,
            ))
            seq += 1
            index_no += 1
            for block in page_blocks:
                clause_code = f"{code}{seq:03d}"
                contents.extend(_content_rows(
                    code, law_id, clause_code, f"{catalog_code}.{clause_code}", 0, index_no,
                    f"OCR片段{index_no:03d}", now,
                ))
                details.append({
                    "etl_src_date": int(TEST_BATCH_DATE.strftime("%Y%m%d")),
                    "etl_src_code": "TEST",
                    "id": _id(f"{clause_code}:detail"),
                    "law_code": code,
                    "law_content_code": clause_code,
                    "content_order": 0,
                    "content_type": 0,
                    "content": block["text"],
                    "create_time": now,
                    "creator_id": TEST_CREATOR,
                    "update_time": now,
                    "updator_id": TEST_CREATOR,
                    "del_flag": "A",
                    "data_version": "external-test-ocr-v1",
                })
                seq += 1
                index_no += 1
    return laws, contents, details


def _content_rows(
    law_code: str, law_id: str, node_code: str, path_code: str, is_catalog: int,
    index_no: int, title: str, now: datetime,
) -> list[dict[str, Any]]:
    """源库真值的同 CODE 双物理行特征，导出器会按 CODE 安全去重。"""
    base = {
        "etl_src_date": int(TEST_BATCH_DATE.strftime("%Y%m%d")),
        "etl_src_code": "TEST",
        "old_id": "",
        "code": node_code,
        "law_id": law_id,
        "law_code": law_code,
        "path_code": path_code,
        "is_catalog": is_catalog,
        "title": title,
        "index_no": index_no,
        "content": "",
        "create_time": now,
        "creator_name": "外网测试OCR",
        "creator_id": TEST_CREATOR,
        "update_time": now,
        "updator_id": TEST_CREATOR,
        "del_flag": "A",
        "data_version": "external-test-ocr-v1",
        "new_content_code": "",
        "new_path_code": "",
        "new_law_code": "",
    }
    return [{**base, "id": _id(f"{node_code}:physical:{i}")} for i in (1, 2)]


def _insert(conn, table: str, rows: list[dict[str, Any]]) -> None:
    from sqlalchemy import text

    if not rows:
        return
    columns = list(rows[0])
    statement = text(
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join(':' + c for c in columns)})"
    )
    conn.execute(statement, rows)


def run(input_dir: Path, dsn: str, dry_run: bool = False) -> None:
    laws, contents, details = _build_rows(input_dir)
    summary = f"laws={len(laws)}, logical_nodes={len(contents) // 2}, text_segments={len(details)}"
    if dry_run:
        print(f"✓ dry-run: {summary}")
        return

    from sqlalchemy import create_engine, text

    codes = [spec["code"] for spec in SPECS]
    engine = create_engine(dsn)
    with engine.begin() as conn:
        # 仅清理本脚本拥有的、固定 CODE 的测试记录；先子表后父表。
        for table in ("znfg_iam_law_content_detail", "znfg_iam_law_content", "znfg_iam_law_basic"):
            conn.execute(text(f"DELETE FROM {table} WHERE law_code = ANY(:codes)" if table != "znfg_iam_law_basic" else "DELETE FROM znfg_iam_law_basic WHERE code = ANY(:codes)"), {"codes": codes})
        _insert(conn, "znfg_iam_law_basic", laws)
        _insert(conn, "znfg_iam_law_content", contents)
        _insert(conn, "znfg_iam_law_content_detail", details)
    print(f"✓ 外网测试 OCR 法规已写入 mock source: {summary}")
    for spec in SPECS:
        print(f"  - {spec['title']}（外网测试OCR）: {spec['code']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="写入两部外网测试 OCR 外规到 mock source")
    parser.add_argument("--input-dir", type=Path, required=True, help="候选 OCR JSONL 所在目录")
    parser.add_argument(
        "--dsn", default=os.environ.get("MOCK_SOURCE_DSN", "postgresql+psycopg://dcetl:dcetl@127.0.0.1:5434/dcetl"),
        help="仅限本机 mock source；不要传内网生产凭据",
    )
    parser.add_argument("--dry-run", action="store_true", help="仅校验 OCR 输入并显示统计，不写库")
    args = parser.parse_args()
    run(args.input_dir, args.dsn, args.dry_run)


if __name__ == "__main__":
    main()
