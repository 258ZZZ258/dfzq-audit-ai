"""per-run 已检索白名单的判别性测试。

这不是一个缓存,是**权限边界**:T4/T5 只允许取本 run 检索过的 id。
按 run_id 分桶而不是靠进程隔离 —— S3 会按 specId 池化 MCP 进程(dfzq-pi 规格 §3.3),
到那时一个进程服务多个并发 run,全局桶会让它们互取对方的条款详情。
"""

from query.mcp.session import RunRegistry


def test_records_and_returns_per_run():
    reg = RunRegistry()
    reg.record("r-1", ["a", "b"])
    assert reg.allowed("r-1") == {"a", "b"}


def test_runs_are_isolated_from_each_other():
    # 池化后的越权向量:两个并发 run 各自检索,不得看见对方的 id。
    reg = RunRegistry()
    reg.record("r-1", ["a"])
    reg.record("r-2", ["b"])
    assert reg.allowed("r-1") == {"a"}
    assert reg.allowed("r-2") == {"b"}
    assert "b" not in reg.allowed("r-1")


def test_unknown_run_has_an_empty_allowlist_not_a_permissive_one():
    # fail-closed:没见过的 run 拿到空集,不是「随便取」。
    reg = RunRegistry()
    assert reg.allowed("never-seen") == set()


def test_record_accumulates_across_calls():
    # 一个 run 会检索多次,后一次不得冲掉前一次(否则模型引用早先命中的 id 会被拒)。
    reg = RunRegistry()
    reg.record("r-1", ["a"])
    reg.record("r-1", ["b"])
    assert reg.allowed("r-1") == {"a", "b"}


def test_forget_drops_only_that_run():
    reg = RunRegistry()
    reg.record("r-1", ["a"])
    reg.record("r-2", ["b"])
    reg.forget("r-1")
    assert reg.allowed("r-1") == set()
    assert reg.allowed("r-2") == {"b"}


def test_allowed_returns_a_copy_not_the_live_set():
    # 调用方改返回值不得改到内部状态 —— 那会让白名单被静默放宽。
    reg = RunRegistry()
    reg.record("r-1", ["a"])
    got = reg.allowed("r-1")
    got.add("INJECTED")
    assert reg.allowed("r-1") == {"a"}


def test_recording_an_empty_list_is_harmless():
    reg = RunRegistry()
    reg.record("r-1", [])
    assert reg.allowed("r-1") == set()
