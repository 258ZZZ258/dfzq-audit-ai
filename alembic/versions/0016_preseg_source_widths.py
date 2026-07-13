"""preseg source-key widths (Codex review:真实达梦列宽 > PG 列宽会 DataError)

真实源 schema(图片 IMG_1112/1113/1120)列宽远大于初版猜测:
- LAW_CONTENT.CODE = VARCHAR(256) → chunks.source_code 原 64 会截断/DataError
- LAW_BASIC.CODE = VARCHAR(180) → doc_versions.source_doc_id 原 64 太窄(幂等键,截断致灾)
- LAW_BASIC.SOURCE_LAW_ID = VARCHAR(256) → doc_versions.source_law_id 原 64 太窄

三列均为**源标识键**(截断会破坏幂等/桥接),拓宽到 256。VARCHAR 拓宽 add-only 安全(不丢数据、
Postgres 秒级)。描述性列(title/issuer/doc_number/sub_type/issuer_level_src)在转换脚本边界按列宽
截断 + 审计(见 export._fit),不在此拓宽(共享列、非键)。

Revision ID: 0016_preseg_source_widths
Revises: 0015_preseg_source_align
Create Date: 2026-07-14 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0016_preseg_source_widths"
down_revision: str | None = "0015_preseg_source_align"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("chunks", "source_code",
                    existing_type=sa.String(length=64), type_=sa.String(length=256))
    op.alter_column("doc_versions", "source_doc_id",
                    existing_type=sa.String(length=64), type_=sa.String(length=256))
    op.alter_column("doc_versions", "source_law_id",
                    existing_type=sa.String(length=64), type_=sa.String(length=256))


def downgrade() -> None:
    op.alter_column("doc_versions", "source_law_id",
                    existing_type=sa.String(length=256), type_=sa.String(length=64))
    op.alter_column("doc_versions", "source_doc_id",
                    existing_type=sa.String(length=256), type_=sa.String(length=64))
    op.alter_column("chunks", "source_code",
                    existing_type=sa.String(length=256), type_=sa.String(length=64))
