"""A/B experiments: design, lifecycle, event ingestion and results."""

import importlib.util
from contextlib import contextmanager

import pytest

from app.core.auth import get_current_user
from app.main import app

from app.models.experiment import ExperimentSubject
from app.models.flag import Flag
from app.services.flagsmith_sync import _variants
from tests.conftest import TestingSession

needs_engine = pytest.mark.skipif(importlib.util.find_spec("lifecycle_engine") is None,
                                  reason="lifecycle engine not installed (pip install -r requirements-engine.txt)")

VARIANTS = [{"key": "control", "is_control": True, "expected_share": 50},
            {"key": "one-page", "expected_share": 50}]


@pytest.fixture()
def flag(client):
    return client.post("/api/flags", json={"name": "checkout_layout", "environment": "Production", "enabled": True}).json()


def create(client, flag, **kw):
    body = {"name": "One-page checkout", "primary_metric": "checkout_completed", "variants": VARIANTS, **kw}
    r = client.post(f"/api/flags/{flag['id']}/experiments", json=body)
    assert r.status_code == 201, r.text
    return r.json()


@contextmanager
def signed_out():
    """Like a customer's server calling the event endpoint: no user token."""
    saved = app.dependency_overrides.pop(get_current_user, None)
    try:
        yield
    finally:
        if saved is not None:
            app.dependency_overrides[get_current_user] = saved


def send(client, exp, events, key=None):
    with signed_out():
        return client.post(f"/api/experiments/{exp['id']}/events", json={"events": events},
                           headers={"X-Experiment-Key": key or exp["ingest_key"]})


def test_create_returns_the_key_once(client, flag):
    exp = create(client, flag, hypothesis="Fewer steps -> more completed checkouts")
    assert exp["status"] == "draft" and exp["ingest_key"].startswith("fpx_")
    assert f"/api/experiments/{exp['id']}/events" in exp["ingest_example"]
    assert exp["flag_name"] == "checkout_layout" and exp["environment_name"] == "Production"
    fetched = client.get(f"/api/experiments/{exp['id']}").json()
    assert "ingest_key" not in fetched
    from app.models.experiment import Experiment
    with TestingSession() as db:
        assert exp["ingest_key"] not in db.get(Experiment, exp["id"]).ingest_key_hash


def test_variants_prefilled_from_flagsmith(client, flag):
    with TestingSession() as db:
        db.get(Flag, flag["id"]).flagsmith_variants = [
            {"option_id": 1, "value": "One page", "default_allocation": 30},
            {"option_id": 2, "value": "Two pages", "default_allocation": 20}]
        db.commit()
    exp = create(client, flag, variants=None)
    assert [(v["key"], v["is_control"], v["expected_share"]) for v in exp["variants"]] == [
        ("control", True, 50.0), ("One-page", False, 30.0), ("Two-pages", False, 20.0)]
    assert exp["variants"][1]["flagsmith_option_id"] == 1


def test_flag_without_variants_needs_explicit_ones(client, flag):
    r = client.post(f"/api/flags/{flag['id']}/experiments", json={"name": "x", "primary_metric": "m"})
    assert r.status_code == 400 and "multivariate options" in r.json()["detail"]


@pytest.mark.parametrize("variants,needle", [
    ([{"key": "a", "is_control": True, "expected_share": 50}, {"key": "b", "expected_share": 40}], "add up to 100%"),
    ([{"key": "a", "expected_share": 50}, {"key": "b", "expected_share": 50}], "Exactly one variant"),
    ([{"key": "a", "is_control": True}, {"key": "a"}], "unique"),
])
def test_variant_validation(client, flag, variants, needle):
    r = client.post(f"/api/flags/{flag['id']}/experiments", json={"name": "x", "primary_metric": "m", "variants": variants})
    assert r.status_code == 422 and needle in r.json()["detail"]


def test_lifecycle_rules(client, flag):
    exp = create(client, flag)
    eid = exp["id"]
    assert client.post(f"/api/experiments/{eid}/stop").status_code == 409
    assert client.post(f"/api/experiments/{eid}/conclude", json={}).status_code == 409
    assert client.patch(f"/api/experiments/{eid}", json={"alpha": 0.01}).json()["alpha"] == 0.01  # draft: OK
    assert client.post(f"/api/experiments/{eid}/start").json()["status"] == "running"
    r = client.patch(f"/api/experiments/{eid}", json={"variants": VARIANTS})
    assert r.status_code == 409 and "draft" in r.json()["detail"]
    assert client.patch(f"/api/experiments/{eid}", json={"name": "Renamed"}).json()["name"] == "Renamed"
    assert client.delete(f"/api/experiments/{eid}").status_code == 409
    assert client.post(f"/api/experiments/{eid}/stop").json()["status"] == "stopped"
    assert client.post(f"/api/experiments/{eid}/start").json()["status"] == "running"     # resume
    client.post(f"/api/experiments/{eid}/stop")
    assert client.post(f"/api/experiments/{eid}/conclude", json={"winner": "nope"}).status_code == 422
    done = client.post(f"/api/experiments/{eid}/conclude", json={"winner": "one-page", "note": "ship it"}).json()
    assert done["status"] == "concluded" and done["winner"] == "one-page"
    assert client.post(f"/api/experiments/{eid}/start").status_code == 409
    actions = [l["action"] for l in client.get("/api/audit-logs?resource_type=experiment").json()]
    assert {"experiment.created", "experiment.started", "experiment.stopped", "experiment.concluded"} <= set(actions)


def test_events_need_the_key_and_a_running_experiment(client, flag):
    exp = create(client, flag)
    ev = [{"type": "exposure", "subject_id": "u1", "variant": "control"}]
    assert send(client, exp, ev).status_code == 409                            # draft
    client.post(f"/api/experiments/{exp['id']}/start")
    assert send(client, exp, ev, key="fpx_wrong").status_code == 401
    with signed_out():
        assert client.post("/api/experiments/nope/events", json={"events": ev},
                           headers={"X-Experiment-Key": exp["ingest_key"]}).status_code == 401
        assert client.post(f"/api/experiments/{exp['id']}/events", json={"events": ev}).status_code == 401
        assert client.get("/api/experiments").status_code == 401              # everything else needs sign-in
    r = send(client, exp, ev)                                                  # no user sign-in needed
    assert r.status_code == 200 and r.json()["accepted_exposures"] == 1


def test_event_counting(client, flag):
    exp = create(client, flag)
    client.post(f"/api/experiments/{exp['id']}/start")
    r = send(client, exp, [
        {"type": "exposure", "subject_id": "u1", "variant": "control"},
        {"type": "exposure", "subject_id": "u1", "variant": "control"},     # duplicate
        {"type": "exposure", "subject_id": "u1", "variant": "one-page"},    # conflict, first variant kept
        {"type": "exposure", "subject_id": "u2", "variant": "one-page"},
        {"type": "exposure", "subject_id": "u3", "variant": "ghost"},       # unknown variant
        {"type": "conversion", "subject_id": "u2"},
        {"type": "conversion", "subject_id": "u2"},                         # duplicate
        {"type": "conversion", "subject_id": "u9"},                         # never exposed
    ]).json()
    assert r == {"accepted_exposures": 2, "accepted_conversions": 1, "duplicates": 2, "not_exposed": 1,
                 "variant_conflicts": 1, "unknown_variants": 1}
    with TestingSession() as db:
        subjects = db.query(ExperimentSubject).all()
        assert len(subjects) == 2 and all(len(s.subject_hash) == 64 for s in subjects)
        assert not any(s.subject_hash in ("u1", "u2") for s in subjects)          # only hashes stored
        assert {s.workspace_id for s in subjects} == {"ws-acme"}


def test_other_workspaces_cannot_see_experiments(client, flag):
    exp = create(client, flag)
    from app.models.experiment import Experiment
    with TestingSession() as db:
        db.get(Experiment, exp["id"]).workspace_id = None  # belongs to no workspace of this user
        db.commit()
    assert client.get(f"/api/experiments/{exp['id']}").status_code == 404
    assert client.get("/api/experiments").json() == []


@needs_engine
def test_results_reach_a_winner(client, flag):
    exp = create(client, flag, minimum_detectable_effect=1.0)  # detect a doubling: needs ~200 per variant
    client.post(f"/api/experiments/{exp['id']}/start")
    events = []
    for i in range(600):
        events.append({"type": "exposure", "subject_id": f"c{i}", "variant": "control"})
        events.append({"type": "exposure", "subject_id": f"t{i}", "variant": "one-page"})
        if i % 10 == 0:
            events.append({"type": "conversion", "subject_id": f"c{i}"})   # 10%
        if i % 5 == 0:
            events.append({"type": "conversion", "subject_id": f"t{i}"})   # 20%
    for chunk in range(0, len(events), 1000):
        assert send(client, exp, events[chunk:chunk + 1000]).status_code == 200
    res = client.get(f"/api/experiments/{exp['id']}").json()["results"]
    assert res["decision"] == "winner" and res["winner"] == "one-page"
    control, treatment = res["variants"]
    assert (control["exposures"], control["conversions"]) == (600, 60)
    assert (treatment["exposures"], treatment["conversions"]) == (600, 120)
    assert treatment["p_value_adjusted"] < 0.05 and treatment["probability_to_beat_control"] > 0.99
    assert not res["sample_ratio"]["mismatch"]


@needs_engine
def test_empty_experiment_keeps_running(client, flag):
    res = client.get(f"/api/experiments/{create(client, flag)['id']}").json()["results"]
    assert res["decision"] == "keep_running" and res["total_exposures"] == 0


def test_sync_reads_multivariate_options():
    assert _variants({"multivariate_options": [
        {"id": 1, "type": "unicode", "string_value": "Big button", "default_percentage_allocation": 25},
        {"id": 2, "type": "int", "integer_value": 3, "default_percentage_allocation": 25}]}) == [
        {"option_id": 1, "value": "Big button", "default_allocation": 25.0},
        {"option_id": 2, "value": 3, "default_allocation": 25.0}]
    assert _variants({}) is None and _variants({"multivariate_options": []}) == []


# --- verdicts --------------------------------------------------------------------------------------

@needs_engine
def test_analysis_stores_a_verdict_and_experiments_count(client, flag):
    from tests.test_analysis import FakeGitHub, add_repo, use_github
    add_repo()
    use_github(FakeGitHub())
    checkout = client.post("/api/flags", json={"name": "new_checkout", "environment": "Production", "enabled": True}).json()
    body = client.post(f"/api/flags/{checkout['id']}/analysis", json={"use_llm": False}).json()
    v = body["result"]["verdict"]
    assert body["activity"] == v["activity"] == "active"
    assert body["usefulness"] == v["usefulness"] and v["explanation"] and v["recommendation"]
    verdicts = {x["flag_id"]: x for x in client.get("/api/flag-verdicts").json()}
    assert verdicts[checkout["id"]]["analysis_id"] == body["id"]

    exp = create(client, checkout)
    client.post(f"/api/experiments/{exp['id']}/start")
    again = client.post(f"/api/flags/{checkout['id']}/analysis", json={"use_llm": False}).json()
    assert again["usefulness"] == "useful"
    assert any("experiment" in r["text"] for r in again["result"]["verdict"]["usefulness_reasons"])
    assert {x["flag_id"]: x for x in client.get("/api/flag-verdicts").json()}[checkout["id"]]["analysis_id"] == again["id"]
