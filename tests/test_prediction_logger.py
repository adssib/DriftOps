import threading
import time

from driftops.serving.logger import PredictionLogger


def wait_until(cond, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


def test_full_queue_drops_and_counts_without_blocking():
    gate = threading.Event()
    log = PredictionLogger(lambda b: gate.wait(), maxsize=10, batch_size=1, flush_interval=0.01)
    t0 = time.perf_counter()
    results = [log.log({"i": i}) for i in range(100)]  # sink never started: queue fills at 10
    assert time.perf_counter() - t0 < 0.1  # never blocked
    assert results.count(True) == 10
    assert log.dropped == 90


def test_failing_sink_drops_batch_and_keeps_running():
    calls = {"n": 0}

    def sink(batch):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("postgres is down")

    dropped = []
    log = PredictionLogger(
        sink, batch_size=5, flush_interval=0.05, on_dropped=lambda n, r: dropped.append((n, r))
    )
    log.start()
    for i in range(5):
        log.log({"i": i})
    assert wait_until(lambda: log.dropped == 5)
    for i in range(5):
        log.log({"i": i})
    assert wait_until(lambda: log.logged == 5)
    log.stop()
    assert dropped == [(5, "sink_error")]


def test_flushes_by_size_and_by_time():
    batches = []
    log = PredictionLogger(batches.append, batch_size=3, flush_interval=0.2).start()
    for i in range(7):
        log.log({"i": i})
    assert wait_until(lambda: sum(len(b) for b in batches) == 7)
    log.stop()
    assert [len(b) for b in batches][:2] == [3, 3]  # two full batches, then the remainder on time


def test_stop_drains_the_queue():
    batches = []
    log = PredictionLogger(batches.append, batch_size=100, flush_interval=10).start()
    for i in range(42):
        log.log({"i": i})
    log.stop()
    assert sum(len(b) for b in batches) == 42
