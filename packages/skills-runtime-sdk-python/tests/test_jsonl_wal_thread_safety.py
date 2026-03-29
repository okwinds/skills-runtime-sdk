import multiprocessing as mp
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


def _append_from_process(
    wal_path: str,
    start_event,
    ready_queue,
    result_queue,
    n_per_process: int,
    process_name: str,
) -> None:
    wal = JsonlWal(path=wal_path)
    ready_queue.put(process_name)
    start_event.wait()
    indices = []
    for i in range(n_per_process):
        idx = wal.append(AgentEvent(type="test", timestamp=f"{process_name}-{i}", run_id="r", payload={}))
        indices.append(idx)
    result_queue.put(indices)


def test_multiprocess_append_keeps_indices_unique(tmp_path):
    ctx = mp.get_context("spawn")
    wal_path = str(tmp_path / "events.jsonl")
    start_event = ctx.Event()
    ready_queue = ctx.Queue()
    result_queue = ctx.Queue()
    n_per_process = 20
    processes = [
        ctx.Process(
            target=_append_from_process,
            args=(wal_path, start_event, ready_queue, result_queue, n_per_process, f"p{i}"),
        )
        for i in range(2)
    ]
    for proc in processes:
        proc.start()
    for _ in processes:
        assert ready_queue.get(timeout=10) in {"p0", "p1"}
    start_event.set()

    indices = []
    for _ in processes:
        indices.extend(result_queue.get(timeout=10))

    for proc in processes:
        proc.join(timeout=10)
        assert proc.exitcode == 0

    assert len(indices) == len(processes) * n_per_process
    assert len(set(indices)) == len(indices)
    assert len(list(JsonlWal(path=wal_path).iter_events())) == len(indices)
