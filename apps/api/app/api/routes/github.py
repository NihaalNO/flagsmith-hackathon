from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.models.base import utc_now
from app.models.flag import Flag
from app.models.flag_reference import FlagReference
from app.models.integration import Integration
from app.models.repository import Repository
from app.schemas.github import GitHubSyncResult, InstallUrl, ScanResponse
from app.services import audit
from app.services.code_scan import scan_tarball
from app.services.github import GitHubClientFactory, GitHubError, get_github_client_factory

router = APIRouter(tags=["GitHub"])


@router.get("/github/install-url", response_model=InstallUrl)
def install_url():
    """Where to send a user to install the FlagPilot GitHub App on their repos."""
    if not settings.github_app_slug:
        raise HTTPException(503, "GitHub App is not configured")
    return {"url": f"https://github.com/apps/{settings.github_app_slug}/installations/new"}


@router.post("/github/sync", response_model=GitHubSyncResult)
def sync_github(db: Session = Depends(get_db), make_client: GitHubClientFactory = Depends(get_github_client_factory)):
    """Import every repo the GitHub App is installed on (run after installing / changing repo access)."""
    added = updated = 0
    names: list[str] = []
    try:
        with make_client() as gh:
            installations = gh.list_installations()
            for inst in installations:
                inst_id = str(inst["id"])
                account = inst["account"]["login"]
                integ = db.scalar(select(Integration).where(
                    Integration.provider == "github", Integration.config["installation_id"].as_string() == inst_id))
                if integ is None:
                    integ = Integration(provider="github", name=f"GitHub: {account}",
                                        config={"installation_id": inst_id, "account": account})
                    db.add(integ)
                    db.flush()
                    audit.record(db, "integration.created", "integration", integ.id,
                                 {"provider": "github", "account": account})
                integ.status, integ.status_detail, integ.last_synced_at = "connected", None, utc_now()

                for r in gh.list_installation_repos(inst_id):
                    repo = db.scalar(select(Repository).where(Repository.github_repo_id == str(r["id"])))
                    if repo is None:
                        repo = db.scalar(select(Repository).where(Repository.full_name == r["full_name"]))
                    if repo is None:
                        repo = Repository(github_repo_id=str(r["id"]), full_name=r["full_name"])
                        db.add(repo)
                        added += 1
                    else:
                        updated += 1
                    repo.github_repo_id = str(r["id"])
                    repo.name = r["name"]
                    repo.full_name = r["full_name"]
                    repo.default_branch = r.get("default_branch") or "main"
                    repo.installation_id = inst_id
                    names.append(r["full_name"])
    except GitHubError as exc:
        db.rollback()
        raise HTTPException(502, f"GitHub: {exc}")
    audit.record(db, "github.synced", "integration", None,
                 {"installations": len(installations), "added": added, "updated": updated})
    db.commit()
    return {"installations": len(installations), "repositories_added": added,
            "repositories_updated": updated, "repositories": sorted(names)}


@router.post("/repositories/{repo_id}/scan", response_model=ScanResponse)
def scan_repository(repo_id: str, db: Session = Depends(get_db),
                    make_client: GitHubClientFactory = Depends(get_github_client_factory)):
    """Download the default branch and record every place each flag's name appears."""
    repo = db.get(Repository, repo_id)
    if not repo:
        raise HTTPException(404, "Repository not found")
    if not repo.installation_id:
        raise HTTPException(400, "Repository isn't connected through the GitHub App; run POST /api/github/sync")
    flags = db.scalars(select(Flag)).all()
    by_name = {f.name: f for f in flags}
    try:
        with make_client() as gh:
            sha = gh.branch_head_sha(repo.installation_id, repo.full_name, repo.default_branch)
            tarball = gh.download_tarball(repo.installation_id, repo.full_name, sha)
    except GitHubError as exc:
        raise HTTPException(502, f"GitHub: {exc}")

    result = scan_tarball(tarball, list(by_name))
    db.execute(delete(FlagReference).where(FlagReference.repository_id == repo.id))
    counts: dict[str, int] = {}
    for ref in result.references:
        db.add(FlagReference(flag_id=by_name[ref.flag_name].id, repository_id=repo.id, path=ref.path,
                             line=ref.line, snippet=ref.snippet, commit_sha=sha))
        counts[ref.flag_name] = counts.get(ref.flag_name, 0) + 1
    repo.last_scanned_at, repo.last_scan_sha = utc_now(), sha
    audit.record(db, "repository.scanned", "repository", repo.id,
                 {"commit_sha": sha, "files_scanned": result.files_scanned, "references": counts})
    db.commit()
    return {"repository": repo.full_name, "commit_sha": sha, "files_scanned": result.files_scanned,
            "references": len(result.references),
            "flags": [{"flag": n, "references": c} for n, c in sorted(counts.items())]}
