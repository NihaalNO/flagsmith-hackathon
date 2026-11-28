"""AI-proposed experiments: propose from a repo, then launch (creates multivariate flags)."""

import json

import httpx
import pytest

from app.main import app
from app.models.repository import Repository
from app.services.experiment_planner import clean_proposals, even_split, repo_digest
from app.services.flagsmith import FlagsmithClient, get_flagsmith_client_factory
from app.core.security import decrypt_secret
from tests.conftest import WS_ID, TestingSession
from tests.test_analysis import FakeGitHub, use_github
from tests.test_flagsmith import GOOD_KEY, FakeFlagsmith
from tests.test_github import make_tarball

REPO_FILES = {
    "README.md": "# Shop\nA tiny store.",
    "package.json": '{"name": "shop"}',
    "src/pages/signup.tsx": 'export default () => <button>Create account</button>;\n',
    "src/pages/pricing.tsx": "export const plans = ['monthly', 'yearly'];\n",
    "src/utils/math.test.ts": "test('x', () => {});\n",
    "node_modules/x/index.js": "ignored",
}

LLM_OUTPUT = {"proposals": [
    {"flag_name": "signup_button_copy", "title": "Signup button copy", "hypothesis": "Clearer copy, more signups",
     "primary_metric": "signup_completed",
     "control": {"key": "control", "value": "Create account", "description": "today"},
     "variants": [{"key": "start_free", "value": "Start free", "description": "free framing"}],
     "files": ["src/pages/signup.tsx", "src/pages/invented.tsx"], "rationale": "Main conversion step"},
    {"flag_name": "Pricing Default Plan", "title": "Default to yearly", "hypothesis": "",
     "primary_metric": "Checkout Completed",
     "control": {"key": "control", "value": "monthly", "description": ""},
     "variants": [{"key": "yearly", "value": "yearly", "description": ""},
                  {"key": "dup", "value": "monthly", "description": "same as control: dropped"}],
     "files": ["src/pages/pricing.tsx"], "rationale": ""},
    {"flag_name": "no_variants", "title": "Broken", "hypothesis": "", "primary_metric": "x",
     "control": {"key": "control", "value": "a", "description": ""}, "variants": [], "files": [], "rationale": ""},
]}


class FakeLLM:
    name, model = "mock", "test-model"

    def __init__(self, output=LLM_OUTPUT, fail=False):
        self.output, self.fail, self.prompts = output, fail, []

    def complete_json(self, *, system, prompt, schema, context):
        self.prompts.append(prompt)
        if self.fail:
            raise RuntimeError("rate limited")
        return self.output


class MVFlagsmith(FakeFlagsmith):
    """Returns ids for created multivariate options, like Flagsmith does."""

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/projects/6/features/" and request.method == "POST":
            body = json.loads(request.content)
            if any(f["name"] == body["name"] for f in self.repo_features):
                return httpx.Response(400, json={"name": ["exists"]})
            fid = 600 + len(self.repo_features)
            opts = [{**o, "id": fid * 10 + i} for i, o in enumerate(body.get("multivariate_options", []))]
            feature = {"id": fid, **body, "multivariate_options": opts}
            self.repo_features.append(feature)
            return httpx.Response(201, json=feature)
        return super().handler(request)


@pytest.fixture()
def setup(client, monkeypatch):
    fs = MVFlagsmith()
    app.dependency_overrides[get_flagsmith_client_factory] = lambda: (
        lambda integ: FlagsmithClient(decrypt_secret(integ.encrypted_secret), transport=httpx.MockTransport(fs.handler)))
    integ = client.post("/api/integrations", json={"provider": "flagsmith", "name": "Flagsmith",
                                                   "api_token": GOOD_KEY}).json()
    with TestingSession() as db:
        repo = Repository(workspace_id=WS_ID, full_name="acme/web", name="web", default_branch="main",
                          installation_id="42", flagsmith_project_id=6, flagsmith_integration_id=integ["id"])
        db.add(repo)
        db.commit()
        repo_id = repo.id
    use_github(FakeGitHub(files=REPO_FILES))
    llm = FakeLLM()
    monkeypatch.setattr("app.api.routes.experiments.llm_provider", lambda: llm)
    return client, fs, llm, repo_id


def test_digest_prefers_manifests_and_ui_and_skips_noise():
    digest, paths = repo_digest(make_tarball(REPO_FILES))
    assert "node_modules/x/index.js" not in paths
    assert digest.index("--- README.md ---") < digest.index("--- src/pages/signup.tsx ---")
    assert "--- src/utils/math.test.ts ---" not in digest  # tests aren't sent as content


def test_clean_proposals_normalises_and_drops_bad_ones():
    out = clean_proposals(LLM_OUTPUT, ["src/pages/signup.tsx", "src/pages/pricing.tsx"], {"existing_flag"})
    assert [p["flag_name"] for p in out] == ["signup_button_copy", "pricing_default_plan"]
    assert out[0]["files"] == ["src/pages/signup.tsx"]           # invented path removed
    assert out[1]["primary_metric"] == "checkout_completed"
    assert [v["key"] for v in out[1]["variants"]] == ["yearly"]  # duplicate of the control dropped
    assert clean_proposals(LLM_OUTPUT, [], {"signup_button_copy"})[0]["flag_name"] == "pricing_default_plan"


def test_even_split_adds_to_100():
    assert even_split(1) == (50.0, [50.0])
    control, shares = even_split(2)
    assert round(control + sum(shares), 2) == 100 and shares == [33.33, 33.33]


def test_propose_reads_the_repo_and_creates_nothing(setup):
    client, fs, llm, repo_id = setup
    r = client.post(f"/api/repositories/{repo_id}/experiment-proposals", json={"focus": "signup"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["commit_sha"] == "abc123" and body["llm"] == "mock:test-model"
    assert [p["flag_name"] for p in body["proposals"]] == ["signup_button_copy", "pricing_default_plan"]
    assert "signup" in llm.prompts[0] and "src/pages/signup.tsx" in llm.prompts[0]
    assert fs.repo_features == [] and client.get("/api/flags").json() == []


def test_launch_creates_multivariate_flags_and_running_experiments(setup):
    client, fs, llm, repo_id = setup
    proposals = client.post(f"/api/repositories/{repo_id}/experiment-proposals").json()["proposals"]
    r = client.post(f"/api/repositories/{repo_id}/experiments/launch", json={"proposals": proposals})
    assert r.status_code == 201, r.text
    launched = r.json()["launched"]
    assert len(launched) == 2 and r.json()["errors"] == []

    # Flagsmith got a multivariate feature with the control as its value and an even split.
    created = fs.repo_features[0]
    assert created["type"] == "MULTIVARIATE" and created["initial_value"] == "Create account"
    assert created["multivariate_options"][0]["string_value"] == "Start free"
    assert created["multivariate_options"][0]["default_percentage_allocation"] == 50.0

    exp = launched[0]
    assert exp["status"] == "running" and exp["ingest_key"].startswith("fpx_")
    assert [(v["key"], v["expected_share"], v["flagsmith_option_id"]) for v in exp["variants"]] == [
        ("control", 50.0, None), ("start_free", 50.0, 6000)]
    flag = next(f for f in client.get("/api/flags").json() if f["name"] == "signup_button_copy")
    assert flag["flagsmith_id"] == "600" and flag["flagsmith_project_id"] == 6

    # The app can report events straight away with the shown key.
    ev = client.post(f"/api/experiments/{exp['id']}/events", headers={"X-Experiment-Key": exp["ingest_key"]},
                     json={"events": [{"type": "exposure", "subject_id": "u1", "variant": "start_free"}]})
    assert ev.status_code == 200, ev.text
    assert ev.json()["accepted_exposures"] == 1

    # Launching the same proposal again fails for that flag only.
    again = client.post(f"/api/repositories/{repo_id}/experiments/launch", json={"proposals": proposals[:1]})
    assert again.status_code == 502 and "already exists" in again.json()["detail"]


def test_launch_as_draft(setup):
    client, fs, llm, repo_id = setup
    proposals = client.post(f"/api/repositories/{repo_id}/experiment-proposals").json()["proposals"]
    r = client.post(f"/api/repositories/{repo_id}/experiments/launch", json={"proposals": proposals[:1], "start": False})
    assert r.json()["launched"][0]["status"] == "draft"


def test_llm_failure_and_missing_llm(setup, monkeypatch):
    client, fs, llm, repo_id = setup
    llm.fail = True
    r = client.post(f"/api/repositories/{repo_id}/experiment-proposals")
    assert r.status_code == 502 and "rate limited" in r.json()["detail"]
    monkeypatch.setattr("app.api.routes.experiments.llm_provider", lambda: None)
    r = client.post(f"/api/repositories/{repo_id}/experiment-proposals")
    assert r.status_code == 503


def test_repo_without_flagsmith_project_explains_how_to_fix(setup):
    client, fs, llm, repo_id = setup
    with TestingSession() as db:
        repo = db.get(Repository, repo_id)
        repo.flagsmith_project_id, repo.flagsmith_integration_id = None, None
        db.commit()
    for integ in client.get("/api/integrations").json():
        client.delete(f"/api/integrations/{integ['id']}")
    proposals = [{"flag_name": "abc_test", "title": "t", "primary_metric": "m",
                  "control": {"key": "control", "value": "a"}, "variants": [{"key": "b", "value": "b"}]}]
    r = client.post(f"/api/repositories/{repo_id}/experiments/launch", json={"proposals": proposals})
    assert r.status_code == 400 and "Repositories page" in r.json()["detail"], r.text
