"""Give every repository its own Flagsmith project.

A repository is linked to an existing Flagsmith project when one clearly matches it (same
name as the repo's full name, or as its short name ignoring case and punctuation, e.g.
`acme/web-app` <-> "Web App"). Only when nothing matches is a new project created. If
several projects match, nothing is guessed: the repo stays unlinked until a person picks
one (PUT /api/repositories/{id}/flagsmith-project).
"""

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.integration import Integration
from app.models.repository import Repository
from app.services import audit
from app.services.flagsmith import ClientFactory, FlagsmithError


def flagsmith_integration_for_projects(db: Session) -> Integration | None:
    """The Flagsmith connection new projects are created in (oldest one with a key)."""
    return db.scalar(
        select(Integration)
        .where(Integration.provider == "flagsmith", Integration.encrypted_secret.is_not(None))
        .order_by(Integration.created_at)
    )


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def match_existing_project(repo: Repository, projects: dict[str, int]) -> tuple[int | None, list[str]]:
    """(project id, []) for a clear match; (None, candidates) when ambiguous; (None, []) for none."""
    if repo.full_name in projects:
        return projects[repo.full_name], []
    keys = {_norm(repo.full_name), _norm(repo.name or repo.full_name.split("/")[-1])}
    keys.discard("")
    candidates = sorted(name for name in projects if _norm(name) in keys)
    if len(candidates) == 1:
        return projects[candidates[0]], []
    return None, candidates


def ensure_projects(db: Session, repos: list[Repository], make_client: ClientFactory) -> dict:
    """Create (or link, if one with the same name exists) a Flagsmith project per repo."""
    result = {"projects_created": [], "projects_linked": [], "project_errors": []}
    pending = [r for r in repos if r.flagsmith_project_id is None]
    if not pending:
        return result
    integration = flagsmith_integration_for_projects(db)
    if integration is None:
        result["project_errors"].append("No Flagsmith integration with an API key; projects not created")
        return result
    try:
        with make_client(integration) as fs:
            org_id = (integration.config or {}).get("organisation_id") or fs.list_organisations()[0]["id"]
            existing = {p["name"]: p["id"] for p in fs.list_projects()}
            for repo in pending:
                name = repo.full_name
                try:
                    project_id, ambiguous = match_existing_project(repo, existing)
                    if ambiguous:
                        result["project_errors"].append(
                            f"{name}: several Flagsmith projects match ({', '.join(ambiguous)}); "
                            "choose one on the Repositories page")
                        continue
                    if project_id is not None:
                        result["projects_linked"].append(name)
                    else:
                        project_id = fs.create_project(name, int(org_id))["id"]
                        existing[name] = project_id
                        result["projects_created"].append(name)
                        audit.record(db, "flagsmith.project_created", "repository", repo.id,
                                     {"flagsmith_project_id": project_id, "name": name})
                except FlagsmithError as exc:
                    result["project_errors"].append(f"{name}: {exc}")
                    continue
                repo.flagsmith_project_id = int(project_id)
                repo.flagsmith_integration_id = integration.id
    except (FlagsmithError, IndexError, KeyError) as exc:
        result["project_errors"].append(f"Flagsmith: {exc}")
    return result
