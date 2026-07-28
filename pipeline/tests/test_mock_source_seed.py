"""仿真达梦数据的正文落点必须与真实库一致。"""

from __future__ import annotations

import random

from tools.mock_source import seed


def test_seed_moves_clause_text_from_main_content_to_detail_rows():
    laws = seed.gen_laws(random.Random(7), 1)
    contents = seed.gen_contents(random.Random(8), laws)
    original_text_by_code = {
        row["code"]: row["content"]
        for row in contents
        if row["content"]
    }

    details = seed.gen_content_details(random.Random(9), contents)

    assert original_text_by_code
    assert all(row["content"] == "" for row in contents)
    detail_text_by_code = {
        row["law_content_code"]: row["content"]
        for row in details
        if row["content_type"] == 0
    }
    assert detail_text_by_code == original_text_by_code
