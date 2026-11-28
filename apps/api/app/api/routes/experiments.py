"""A/B experiments on flags.

FlagPilot never changes the split in Flagsmith (read-only by design): you set up the
multivariate flag there, FlagPilot tracks exposures/conversions your app reports and
computes the statistics with the lifecycle engine.
"""

import hashlib
import hmac
import re
import secrets

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.auth import current_actor
from app.core.database import get_db
from app.models.base import utc_now
from app.models.experiment import Experiment, ExperimentSubject
from app.models.flag import Flag
from app.schemas.experiment import (
    ConcludeRequest,
    ExperimentCreate,
    ExperimentEvents,
    ExperimentEventsResult,
    ExperimentResponse,
    ExperimentUpdate,
    ExperimentVariant,
    ExperimentWithKey,
)
from app.services import audit
from app.services.engine import EngineUnavailable, experiment_results

router = APIRouter(tags=["Experiments"])
# Called by your application with the experiment's key; no user sign-in.
events_router = APIRouter(tags=["Experiments"])

TRANSITIONS = {"start": ({"draft", "stopped"}, "running"), "stop": ({"running"}, "stopped")}


# --- helpers -------------------------------------------------------------------------------

def _hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def _new_key() -> str:
    return "fpx_" + secrets.token_urlsafe(32)


def _subject_hash(experiment_id: str, subject_id: str) -> str:
    return hashlib.sha256(f"{experiment_id}:{subject_id}".encode()).hexdigest()


def _slug(value) -> str:
    s = re.sub(r"[^A-Za-z0-9_.\-]+", "-", str(value)).strip("-")[:100]
    return s or "variant"


def variants_from_flagsmith(flag: Flag) -> list[dict]:
    """Control = the flag's own value; one variant per Flagsmith multivariate option."""
    options = flag.flagsmith_variants or []
    if not options:
        raise HTTPException(400, f"'{flag.name}' has no multivariate options in Flagsmith (run a Flagsmith sync, "
                                 "or add variants in Flagsmith); otherwise pass `variants` explicitly")
    taken = {"control"}
    out = []
    for o in options:
        key = _slug(o.get("value")) if o.get("value") not in (None, "") else f"option-{o.get('option_id')}"
        base, n = key, 2
        while key in taken:
            key, n = f"{base}-{n}", n + 1
        taken.add(key)
        out.append({"key": key, "name": str(o.get("value")), "is_control": False,
                    "expected_share": float(o.get("default_allocation") or 0), "flagsmith_option_id": o.get("option_id")})
    control_share = max(0.0, 100.0 - sum(v["expected_share"] for v in out))
    return [{"key": "control", "name": "Control (the flag's own value)", "is_control": True,
             "expected_share": control_share, "flagsmith_option_id": None}, *out]


def _validate_variants(variants: list[dict]) -> list[dict]:
    keys = [v["key"] for v in variants]
    if len(variants) < 2:
        raise HTTPException(422, "An experiment needs at least two variants")
    if sum(1 for v in variants if v.get("is_control")) != 1:
        raise HTTPException(422, "Exactly one variant must be the control (is_control: true)")
    if len(set(keys)) != len(keys):
        raise HTTPException(422, "Variant keys must be unique")
    shares = [v.get("expected_share") for v in variants]
    if all(s is not None for s in shares) and abs(sum(shares) - 100) > 0.5:
        raise HTTPException(422, f"Planned traffic shares must add up to 100% (they add up to {sum(shares):g}%)")
    return variants


def _results(db: Session, exp: Experiment) -> tuple[dict | None, str | None]:
    rows = db.execute(select(ExperimentSubject.variant, func.count(ExperimentSubject.id), func.count(ExperimentSubject.converted_at))
                      .where(ExperimentSubject.experiment_id == exp.id).group_by(ExperimentSubject.variant)).all()
    counts = {variant: (int(n), int(c)) for variant, n, c in rows}
    try:
        return experiment_results(exp.variants, counts, minimum_detectable_effect=exp.minimum_detectable_effect,
                                  alpha=exp.alpha), None
    except EngineUnavailable as exc:
        return None, str(exc)


def _response(db: Session, exp: Experiment, with_results: bool = True) -> dict:
    flag = db.get(Flag, exp.flag_id)
    data = ExperimentResponse.model_validate(exp).model_dump()
    data["flag_name"] = flag.name if flag else None
    if with_results:
        data["results"], data["results_error"] = _results(db, exp)
    return data


def _get(db: Session, experiment_id: str) -> Experiment:
    exp = db.get(Experiment, experiment_id)
    if exp is None:
        raise HTTPException(404, "Experiment not found")
    return exp


def _example(exp: Experiment, key: str) -> str:
    variant = next((v["key"] for v in exp.variants if not v.get("is_control")), "control")
    return (f'POST /api/experiments/{exp.id}/events\nX-Experiment-Key: {key}\nContent-Type: application/json\n\n'
            f'{{"events": [{{"type": "exposure", "subject_id": "user-123", "variant": "{variant}"}},\n'
            f'            {{"type": "conversion", "subject_id": "user-123"}}]}}')


# --- signed-in routes ------------------------------------------------------------------------

@router.post("/flags/{flag_id}/experiments", response_model=ExperimentWithKey, status_code=201,
             summary="Create an A/B experiment on a flag (draft)")
def create_experiment(flag_id: str, payload: ExperimentCreate, db: Session = Depends(get_db)):
    flag = db.get(Flag, flag_id)
    if flag is None:
        raise HTTPException(404, "Flag not found")
    variants = ([v.model_dump() for v in payload.variants] if payload.variants else variants_from_flagsmith(flag))
    key = _new_key()
    exp = Experiment(flag_id=flag.id, name=payload.name, hypothesis=payload.hypothesis, primary_metric=payload.primary_metric,
                     environment_name=payload.environment_name or flag.environment, variants=_validate_variants(variants),
                     minimum_detectable_effect=payload.minimum_detectable_effect, alpha=payload.alpha, status="draft",
                     ingest_key_hash=_hash_key(key), created_by=current_actor.get())
    db.add(exp)
    db.flush()
    audit.record(db, "experiment.created", "experiment", exp.id, {"flag": flag.name, "name": exp.name,
                                                                   "variants": [v["key"] for v in exp.variants]})
    db.commit()
    db.refresh(exp)
    return {**_response(db, exp), "ingest_key": key, "ingest_example": _example(exp, key)}


@router.get("/experiments", response_model=list[ExperimentResponse], summary="All experiments, newest first")
def list_experiments(db: Session = Depends(get_db)):
    return [_response(db, e) for e in db.scalars(select(Experiment).order_by(Experiment.created_at.desc())).all()]


@router.get("/flags/{flag_id}/experiments", response_model=list[ExperimentResponse], summary="Experiments on a flag")
def flag_experiments(flag_id: str, db: Session = Depends(get_db)):
    if db.get(Flag, flag_id) is None:
        raise HTTPException(404, "Flag not found")
    return [_response(db, e) for e in db.scalars(select(Experiment).where(Experiment.flag_id == flag_id)
                                                 .order_by(Experiment.created_at.desc())).all()]


@router.get("/experiments/{experiment_id}", response_model=ExperimentResponse, summary="An experiment with live results")
def get_experiment(experiment_id: str, db: Session = Depends(get_db)):
    return _response(db, _get(db, experiment_id))


@router.patch("/experiments/{experiment_id}", response_model=ExperimentResponse)
def update_experiment(experiment_id: str, payload: ExperimentUpdate, db: Session = Depends(get_db)):
    exp = _get(db, experiment_id)
    changes = payload.model_dump(exclude_unset=True)
    design = {"primary_metric", "environment_name", "variants", "minimum_detectable_effect", "alpha"} & set(changes)
    if design and exp.status != "draft":
        raise HTTPException(409, f"{', '.join(sorted(design))} can only change while the experiment is a draft")
    if "variants" in changes:
        changes["variants"] = _validate_variants([v.model_dump() for v in payload.variants])
    for k, v in changes.items():
        setattr(exp, k, v)
    if changes:
        audit.record(db, "experiment.updated", "experiment", exp.id, {"fields": sorted(changes)})
    db.commit()
    db.refresh(exp)
    return _response(db, exp)


def _transition(db: Session, experiment_id: str, action: str) -> dict:
    exp = _get(db, experiment_id)
    allowed, target = TRANSITIONS[action]
    if exp.status not in allowed:
        raise HTTPException(409, f"Can't {action} an experiment that is {exp.status}")
    if action == "start":
        _validate_variants(exp.variants)
        exp.started_at = exp.started_at or utc_now()
        exp.stopped_at = None
    else:
        exp.stopped_at = utc_now()
    exp.status = target
    audit.record(db, f"experiment.{'started' if action == 'start' else 'stopped'}", "experiment", exp.id, {})
    db.commit()
    db.refresh(exp)
    return _response(db, exp)


@router.post("/experiments/{experiment_id}/start", response_model=ExperimentResponse,
             summary="Start (or resume) collecting events. Doesn't change Flagsmith.")
def start_experiment(experiment_id: str, db: Session = Depends(get_db)):
    return _transition(db, experiment_id, "start")


@router.post("/experiments/{experiment_id}/stop", response_model=ExperimentResponse, summary="Stop collecting events")
def stop_experiment(experiment_id: str, db: Session = Depends(get_db)):
    return _transition(db, experiment_id, "stop")


@router.post("/experiments/{experiment_id}/conclude", response_model=ExperimentResponse,
             summary="Record the decision. Rolling the winner out in Flagsmith is up to you.")
def conclude_experiment(experiment_id: str, payload: ConcludeRequest, db: Session = Depends(get_db)):
    exp = _get(db, experiment_id)
    if exp.status != "stopped":
        raise HTTPException(409, "Stop the experiment before concluding it")
    if payload.winner is not None and payload.winner not in {v["key"] for v in exp.variants}:
        raise HTTPException(422, f"'{payload.winner}' is not a variant of this experiment")
    exp.status, exp.winner, exp.conclusion_note, exp.concluded_at = "concluded", payload.winner, payload.note, utc_now()
    results, _ = _results(db, exp)
    audit.record(db, "experiment.concluded", "experiment", exp.id, {
        "winner": payload.winner, "engine_decision": (results or {}).get("decision"),
        "engine_winner": (results or {}).get("winner")})
    db.commit()
    db.refresh(exp)
    return _response(db, exp)


@router.post("/experiments/{experiment_id}/rotate-key", response_model=ExperimentWithKey,
             summary="Replace the event key (the old one stops working)")
def rotate_key(experiment_id: str, db: Session = Depends(get_db)):
    exp = _get(db, experiment_id)
    key = _new_key()
    exp.ingest_key_hash = _hash_key(key)
    audit.record(db, "experiment.key_rotated", "experiment", exp.id, {})
    db.commit()
    db.refresh(exp)
    return {**_response(db, exp), "ingest_key": key, "ingest_example": _example(exp, key)}


@router.delete("/experiments/{experiment_id}", status_code=204)
def delete_experiment(experiment_id: str, db: Session = Depends(get_db)):
    exp = _get(db, experiment_id)
    if exp.status == "running":
        raise HTTPException(409, "Stop the experiment before deleting it")
    audit.record(db, "experiment.deleted", "experiment", exp.id, {"name": exp.name})
    db.delete(exp)
    db.commit()


# --- event ingestion (your app, authenticated with the experiment key) ------------------------

@events_router.post("/experiments/{experiment_id}/events", response_model=ExperimentEventsResult,
                    summary="Report exposures and conversions (send from your server; up to 1000 per call)")
def ingest_events(experiment_id: str, payload: ExperimentEvents, db: Session = Depends(get_db),
                  x_experiment_key: str | None = Header(default=None)):
    exp = db.get(Experiment, experiment_id)
    # Same answer for unknown experiments and wrong keys: don't reveal which ids exist.
    if exp is None or not x_experiment_key or not hmac.compare_digest(_hash_key(x_experiment_key), exp.ingest_key_hash):
        raise HTTPException(401, "Unknown experiment or wrong X-Experiment-Key")
    if exp.status != "running":
        raise HTTPException(409, f"Experiment is {exp.status}; events are accepted only while it is running")
    for attempt in range(2):
        try:
            result = _apply_events(db, exp, payload)
            db.commit()
            return result
        except IntegrityError:  # a concurrent request inserted the same subject; retry once
            db.rollback()
            if attempt:
                raise HTTPException(409, "Concurrent events for the same subjects; retry")


def _apply_events(db: Session, exp: Experiment, payload: ExperimentEvents) -> dict:
    variants = {v["key"] for v in exp.variants}
    hashes = {e.subject_id: _subject_hash(exp.id, e.subject_id) for e in payload.events}
    existing = {s.subject_hash: s for s in db.scalars(select(ExperimentSubject).where(
        ExperimentSubject.experiment_id == exp.id, ExperimentSubject.subject_hash.in_(set(hashes.values())))).all()}
    stats = {"accepted_exposures": 0, "accepted_conversions": 0, "duplicates": 0, "not_exposed": 0,
             "variant_conflicts": 0, "unknown_variants": 0}
    now = utc_now()
    for event in payload.events:
        h = hashes[event.subject_id]
        subject = existing.get(h)
        if event.type == "exposure":
            if event.variant is None or event.variant not in variants:
                stats["unknown_variants"] += 1
            elif subject is None:
                subject = ExperimentSubject(experiment_id=exp.id, subject_hash=h, variant=event.variant,
                                            exposed_at=now, workspace_id=exp.workspace_id)
                db.add(subject)
                existing[h] = subject
                stats["accepted_exposures"] += 1
            elif subject.variant != event.variant:
                stats["variant_conflicts"] += 1
            else:
                stats["duplicates"] += 1
        else:
            if subject is None:
                stats["not_exposed"] += 1
            elif event.variant is not None and event.variant != subject.variant:
                stats["variant_conflicts"] += 1
            elif subject.converted_at is not None:
                stats["duplicates"] += 1
            else:
                subject.converted_at = now
                stats["accepted_conversions"] += 1
    return stats
