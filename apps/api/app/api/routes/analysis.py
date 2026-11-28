"""Lifecycle-engine analysis of a flag (services/lifecycle-engine).

POST runs the engine on the flag's repositories (downloaded from GitHub at their default
branch head) and stores the result; GET returns stored results. Everything the engine
returns is a recommendation: nothing here changes the flag, its lifecycle status,
Flagsmith or any repository.
"""

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.auth import current_actor
from app.core.database import get_db
from app.models.experiment import Experiment
from app.models.flag import Flag
from app.models.flag_analysis import FlagAnalysis
from app.models.flag_environment_state import FlagEnvironmentState
from app.models.repository import Repository
from app.schemas.analysis import (
    AnalyzeRequest,
    FlagAnalysisResponse,
    FlagAnalysisSummary,
    FlagEnvironmentStateResponse,
    FlagVerdictSummary,
)
from app.services import audit
from app.services.engine import EngineUnavailable, RepoSnapshot, analyze_flag, engine_version
from app.services.github import GitHubClientFactory, GitHubError, get_github_client_factory

router = APIRouter(prefix="/flags", tags=["Lifecycle analysis"])

MAX_REPOS_PER_ANALYSIS = 10


def _flag_or_404(db: Session, flag_id: str) -> Flag:
    flag = db.get(Flag, flag_id)
    if not flag:
        raise HTTPException(404, "Flag not found")
    return flag


def _repositories_for(db: Session, flag: Flag) -> tuple[list[Repository], bool]:
    """The repos whose code can evaluate this flag: those linked to the flag's Flagsmith
    project, or every GitHub-connected repo when none is linked. Returns (repos, linked)."""
    stmt = select(Repository).where(Repository.installation_id.is_not(None)).order_by(Repository.full_name)
    if flag.flagsmith_project_id is not None:
        linked = db.scalars(stmt.where(Repository.flagsmith_project_id == flag.flagsmith_project_id)).all()
        if linked:
            return list(linked), True
    return list(db.scalars(stmt).all()), False


def _states(db: Session, flag_id: str) -> list[FlagEnvironmentState]:
    return list(db.scalars(select(FlagEnvironmentState).where(FlagEnvironmentState.flag_id == flag_id)
                           .order_by(FlagEnvironmentState.environment_name)).all())


def _response(row: FlagAnalysis) -> dict:
    return {**FlagAnalysisSummary.model_validate(row).model_dump(),
            "repositories": row.result.get("repositories", []), "result": row.result}


@router.post("/{flag_id}/analysis", response_model=FlagAnalysisResponse, status_code=201,
             summary="Run the lifecycle engine for a flag")
def run_analysis(flag_id: str, payload: AnalyzeRequest = Body(default_factory=AnalyzeRequest),
                 db: Session = Depends(get_db),
                 make_client: GitHubClientFactory = Depends(get_github_client_factory)):
    flag = _flag_or_404(db, flag_id)
    try:
        engine_version()
    except EngineUnavailable as exc:
        raise HTTPException(503, str(exc))
    repos, linked = _repositories_for(db, flag)
    if not repos:
        raise HTTPException(400, "No GitHub-connected repositories to analyze; install the GitHub App and run POST /api/github/sync")
    if len(repos) > MAX_REPOS_PER_ANALYSIS:
        raise HTTPException(400, f"Flag maps to {len(repos)} repositories; link it to a Flagsmith project "
                                 f"(at most {MAX_REPOS_PER_ANALYSIS} repositories per analysis)")
    # Each repository is fetched on its own: one empty, deleted or no-longer-shared repo
    # shouldn't sink the analysis of the others.
    snapshots: list[RepoSnapshot] = []
    skipped: list[str] = []
    try:
        with make_client() as gh:
            for repo in repos:
                try:
                    sha = gh.branch_head_sha(repo.installation_id, repo.full_name, repo.default_branch)
                    snapshots.append(RepoSnapshot(repo.full_name, sha,
                                                  gh.download_tarball(repo.installation_id, repo.full_name, sha)))
                except GitHubError as exc:
                    skipped.append(f"{repo.full_name} ({repo.default_branch}): {exc}")
    except GitHubError as exc:  # e.g. the GitHub App credentials themselves don't work
        raise HTTPException(502, f"GitHub: {exc}")
    if not snapshots:
        raise HTTPException(502, "GitHub: could not read any repository for this flag: " + "; ".join(skipped))

    try:
        experiments = [{"name": x.name, "status": x.status}
                       for x in db.scalars(select(Experiment).where(Experiment.flag_id == flag.id)).all()]
        result = analyze_flag(flag, snapshots, states=_states(db, flag.id), experiments=experiments,
                              use_llm=payload.use_llm)
    except EngineUnavailable as exc:
        raise HTTPException(503, str(exc))

    notes = result.setdefault("data_notes", [])
    if not linked:
        notes.insert(0, "no repository is linked to this flag's Flagsmith project, so every connected repository "
                        "was analyzed; link the right one on the Repositories page")
    notes.extend(f"repository skipped, couldn't be read from GitHub: {s}" for s in skipped)
    analysis, stale = result["analysis"], result["stale_finding"]
    refs = analysis["references"]
    row = FlagAnalysis(
        flag_id=flag.id,
        engine_version=result["engine_version"],
        stale_status=stale["status"],
        stale_confidence=stale["confidence"],
        cleanup_direction=stale["cleanup_direction"],
        suggested_state=result["lifecycle_evaluation"]["suggested_state"],
        recommended_action=result["cleanup_plan"]["recommended_action"],
        confirmed_references=sum(r["classification"] == "confirmed" for r in refs),
        potential_references=sum(r["classification"] == "potential" for r in refs),
        dynamic_references=len(analysis["unresolved_dynamic_references"]),
        llm_status=result["cleanup_plan"]["llm"]["status"],
        activity=result["verdict"]["activity"],
        usefulness=result["verdict"]["usefulness"],
        verdict_confidence=result["verdict"]["confidence"],
        result=result,
        created_by=current_actor.get(),
    )
    db.add(row)
    db.flush()
    audit.record(db, "flag.analyzed", "flag", flag.id, {
        "analysis_id": row.id, "stale_status": row.stale_status, "suggested_state": row.suggested_state,
        "repositories": result["repositories"]})
    db.commit()
    db.refresh(row)
    return _response(row)


# Own path (not /flags/verdicts): the flags router's GET /flags/{flag_id} would capture it.
verdict_router = APIRouter(tags=["Flags"])


@verdict_router.get("/flag-verdicts", response_model=list[FlagVerdictSummary],
                    summary="Latest active/useful verdict for every analyzed flag")
def flag_verdicts(db: Session = Depends(get_db)):
    latest = (select(FlagAnalysis.flag_id, func.max(FlagAnalysis.created_at).label("at"))
              .group_by(FlagAnalysis.flag_id).subquery())
    rows = db.scalars(select(FlagAnalysis).join(
        latest, (FlagAnalysis.flag_id == latest.c.flag_id) & (FlagAnalysis.created_at == latest.c.at))).all()
    return [{"flag_id": r.flag_id, "analysis_id": r.id, "activity": r.activity, "usefulness": r.usefulness,
             "confidence": r.verdict_confidence, "stale_status": r.stale_status, "analyzed_at": r.created_at}
            for r in rows]


@router.get("/{flag_id}/analysis", response_model=FlagAnalysisResponse | None,
            summary="Latest lifecycle analysis of a flag (null if it has never been analyzed)")
def latest_analysis(flag_id: str, db: Session = Depends(get_db)):
    _flag_or_404(db, flag_id)
    row = db.scalars(select(FlagAnalysis).where(FlagAnalysis.flag_id == flag_id)
                     .order_by(FlagAnalysis.created_at.desc()).limit(1)).first()
    return None if row is None else _response(row)


@router.get("/{flag_id}/analyses", response_model=list[FlagAnalysisSummary], summary="Analysis history of a flag")
def analysis_history(flag_id: str, db: Session = Depends(get_db)):
    _flag_or_404(db, flag_id)
    return db.scalars(select(FlagAnalysis).where(FlagAnalysis.flag_id == flag_id)
                      .order_by(FlagAnalysis.created_at.desc()).limit(50)).all()


@router.get("/{flag_id}/environments", response_model=list[FlagEnvironmentStateResponse], tags=["Flags"],
            summary="The flag's state in every Flagsmith environment (as of the last sync)")
def flag_environments(flag_id: str, db: Session = Depends(get_db)):
    _flag_or_404(db, flag_id)
    from app.core.config import settings
    prod = settings.production_environment_list
    return [{**FlagEnvironmentStateResponse.model_validate(st).model_dump(exclude={"is_production"}),
             "is_production": st.environment_name.lower() in prod} for st in _states(db, flag_id)]
