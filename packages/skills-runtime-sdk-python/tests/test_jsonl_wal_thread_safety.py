import threading

from skills_runtime.core.contracts import AgentEvent
from skills_runtime.state.jsonl_wal import JsonlWal


def test_concurrent_append_no_data_loss(tmp_path):
    wal = JsonlWal(path=tmp_path / "events.jsonl")
    n_threads, n_per_thread = 4, 50

    def writer(tid):
        for i in range(n_per_thread):
            wal.append(
                AgentEvent(
                    type="test",
                    timestamp=f"t{tid}-{i}",
                    run_id="r",
                    payload={},
                )
            )

    threads = [threading.Thread(target=writer, args=(t,)) for t in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    events = list(wal.iter_events())
    assert len(events) == n_threads * n_per_thread


def test_concurrent_append_monotonic_index(tmp_path):
    wal = JsonlWal(path=tmp_path / "events.jsonl")
    indices = []
    lock = threading.Lock()

    def writer():
        for _ in range(20):
            idx = wal.append(AgentEvent(type="t", timestamp="t", run_id="r", payload={}))
            with lock:
                indices.append(idx)

    threads = [threading.Thread(target=writer) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(set(indices)) == 80
