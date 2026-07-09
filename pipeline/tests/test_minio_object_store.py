"""MinioObjectStore(S3 后端)单元:注入 fake client,零真 MinIO、零 minio SDK 依赖。

put/get artifact 往返 + key 布局 · IR 往返 · exists · write_once 不覆盖 · from_config 后端分发。
真 MinIO 端到端留合并前集成(需 minio SDK + 真栈)。
"""

from __future__ import annotations

import pytest

from common.ir import Block, BlockType, IRDocument, SourceFormat
from pipeline.config import load_config
from pipeline.index.object_store import MinioObjectStore, ObjectStore


class _FakeResp:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data

    def close(self) -> None:
        pass

    def release_conn(self) -> None:
        pass


class _FakeMinio:
    """内存版 minio client(仅实现被 MinioObjectStore 调用的 3 方法)。"""

    def __init__(self) -> None:
        self.objs: dict[tuple[str, str], bytes] = {}

    def put_object(self, bucket, key, data, length, **kw):
        self.objs[(bucket, key)] = data.read()

    def get_object(self, bucket, key):
        if (bucket, key) not in self.objs:
            raise RuntimeError("NoSuchKey")
        return _FakeResp(self.objs[(bucket, key)])

    def stat_object(self, bucket, key):
        if (bucket, key) not in self.objs:
            raise RuntimeError("NoSuchKey")
        return object()


def test_put_get_artifact_roundtrip():
    c = _FakeMinio()
    s = MinioObjectStore(c, "bkt")
    s.put_artifact("u1", b'{"x":1}')
    assert ("bkt", "artifact/u1.json") in c.objs  # 继承的 key 布局
    assert s.get_artifact("u1") == b'{"x":1}'


def test_ir_roundtrip():
    c = _FakeMinio()
    s = MinioObjectStore(c, "bkt")
    ir = IRDocument(
        doc_version_id="dv1", source_format=SourceFormat("pdf"),
        blocks=[Block(index=0, type=BlockType.PARAGRAPH, text="正文", page=1)],
    )
    s.put_ir(ir)
    assert ("bkt", "ir/dv1.json") in c.objs
    assert s.load_ir("dv1").doc_version_id == "dv1"


def test_exists():
    c = _FakeMinio()
    s = MinioObjectStore(c, "bkt")
    assert not s.exists("artifact/u1.json")
    s.put_artifact("u1", b"x")
    assert s.exists("artifact/u1.json")


def test_write_once_no_overwrite():
    c = _FakeMinio()
    s = MinioObjectStore(c, "bkt")
    s.put_raw("P-INT", "b1", "dv1", "pdf", b"first")
    s.put_raw("P-INT", "b1", "dv1", "pdf", b"second")  # write_once → 不覆盖
    assert s.get(ObjectStore.raw_key("P-INT", "b1", "dv1", "pdf")) == b"first"


def test_from_config_local_default():
    st = ObjectStore.from_config(load_config())  # settings.toml 默认 backend=local
    assert type(st) is ObjectStore


def test_from_config_minio_dispatch(monkeypatch):
    settings = load_config().model_copy(deep=True)
    settings.object_store.backend = "minio"
    sentinel = object()
    monkeypatch.setattr(MinioObjectStore, "from_env", classmethod(lambda cls, cfg: sentinel))
    assert ObjectStore.from_config(settings) is sentinel


def test_from_env_requires_endpoint_bucket(monkeypatch):
    # 缺 endpoint/bucket → 构造即抛(不留到写才崩);且不触发真 minio 连接
    settings = load_config()
    monkeypatch.delenv("MINIO_ENDPOINT", raising=False)
    monkeypatch.delenv("MINIO_BUCKET", raising=False)
    with pytest.raises(ValueError, match="minio_endpoint"):
        MinioObjectStore.from_env(settings.object_store)
