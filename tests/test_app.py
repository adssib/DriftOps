import threading
import time

import pytest
from fastapi.testclient import TestClient
from test_bundle_registry import tiny_model  # noqa: F401  (module-scoped fixture)

from driftops.config import Settings
from driftops.serving.app import create_app
from driftops.serving.logger import PredictionLogger

GOOD = {
    "carrier": "AA",
    "origin": "ORD",
    "dest": "LGA",
    "flight_date": "2020-03-27",
    "dep_time": "18:05",
}


class FakeLookup:
    def route(self, o, d):
        return (733.0, 130.0)

    def origin_load(self, day, a, h):
        return 20.0

    def dest_load(self, day, a, h):
        return 10.0


@pytest.fixture
def make_client(tiny_model):  # noqa: F811
    model, _ = tiny_model
    made = []

    def make(*, gate=None, cors=(), sink=None, sim_token="s3cret"):
        records = []

        def load():
            if gate:
                gate.wait(5)
            return model

        logger = PredictionLogger(sink or records.extend, batch_size=1, flush_interval=0.01)
        s = Settings(model_version=1, sim_token=sim_token, cors_origins=list(cors))
        app = create_app(s, load_model=load, lookup=FakeLookup(), logger=logger)
        client = TestClient(app)
        client.__enter__()
        made.append(client)
        return client, records

    yield make
    for c in made:
        c.__exit__(None, None, None)


def ready(client, timeout=3):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if client.get("/readyz").status_code == 200:
            return True
        time.sleep(0.02)
    return False


def test_liveness_up_while_model_loads(make_client):
    gate = threading.Event()
    client, _ = make_client(gate=gate)
    assert client.get("/healthz").status_code == 200
    r = client.get("/readyz")
    assert r.status_code == 503 and r.json()["status"] == "loading"
    assert client.post("/predict", json=GOOD).status_code == 503
    gate.set()
    assert ready(client)


def test_predict_contract(make_client):
    client, records = make_client()
    assert ready(client)
    r = client.post("/predict", json=GOOD, headers={"X-Request-ID": "abc-123"})
    assert r.status_code == 200
    body = r.json()
    assert 0 <= body["p_disrupted"] <= 1
    assert body["risk"] in {"low", "elevated", "high"}
    assert body["model_version"] == 1
    assert len(body["top_reasons"]) == 3
    assert body["request_id"] == "abc-123" and r.headers["X-Request-ID"] == "abc-123"
    end = time.monotonic() + 2
    while not records and time.monotonic() < end:
        time.sleep(0.01)
    assert records[0]["source"] == "user" and records[0]["request_id"] == "abc-123"


def test_rejects_bad_input_and_big_body(make_client):
    client, _ = make_client()
    assert ready(client)
    assert client.post("/predict", json={**GOOD, "carrier": "aa"}).status_code == 422
    assert client.post("/predict", json={**GOOD, "flight_date": "2022-01-01"}).status_code == 422
    big = {**GOOD, "padding": "x" * 5000}
    assert client.post("/predict", json=big).status_code == 413


def test_source_cannot_be_spoofed(make_client):
    client, records = make_client()
    assert ready(client)
    assert client.post("/predict", json={**GOOD, "source": "sim"}).status_code == 422
    client.post("/predict", json=GOOD, headers={"Authorization": "Bearer wrong"})
    client.post("/predict", json=GOOD, headers={"Authorization": "Bearer s3cret"})
    end = time.monotonic() + 2
    while len(records) < 2 and time.monotonic() < end:
        time.sleep(0.01)
    assert [r["source"] for r in records] == ["user", "sim"]


def test_logging_failure_never_fails_a_prediction(make_client):
    def broken(batch):
        raise ConnectionError("postgres down")

    client, _ = make_client(sink=broken)
    assert ready(client)
    for _ in range(5):
        assert client.post("/predict", json=GOOD).status_code == 200
    time.sleep(0.2)
    assert "driftops_predictions_dropped_total" in client.get("/metrics").text


def test_cors_only_for_allowed_origin(make_client):
    client, _ = make_client(cors=["https://adssib.github.io"])
    ok = client.options(
        "/predict",
        headers={"Origin": "https://adssib.github.io", "Access-Control-Request-Method": "POST"},
    )
    bad = client.options(
        "/predict",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"},
    )
    assert ok.headers.get("access-control-allow-origin") == "https://adssib.github.io"
    assert "access-control-allow-origin" not in bad.headers


def test_metrics_by_model_version(make_client):
    client, _ = make_client()
    assert ready(client)
    client.post("/predict", json=GOOD)
    text = client.get("/metrics").text
    assert 'driftops_requests_total{endpoint="predict",model_version="1",status="200"}' in text
    assert 'driftops_model_info{version="1"} 1.0' in text


def test_explanations_only_on_request(make_client):
    client, _ = make_client()
    assert ready(client)
    assert len(client.post("/predict", json=GOOD).json()["top_reasons"]) == 3
    assert client.post("/predict?explain=false", json=GOOD).json()["top_reasons"] == []


def test_healthz_answers_while_predictions_run(make_client):
    """Scoring runs in the thread pool: the event loop stays free for the liveness probe."""
    import concurrent.futures as cf

    client, _ = make_client()
    assert ready(client)
    with cf.ThreadPoolExecutor(16) as pool:
        futures = [pool.submit(client.post, "/predict", json=GOOD) for _ in range(64)]
        t0 = time.perf_counter()
        assert client.get("/healthz").status_code == 200
        health_s = time.perf_counter() - t0
        assert all(f.result().status_code == 200 for f in futures)
    assert health_s < 1.0
