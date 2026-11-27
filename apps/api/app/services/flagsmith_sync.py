"""Pull features from Flagsmith into FlagPilot.

Synced projects: the integration's `config.project_id` (if set) plus every repository's
own project. Flagsmith is the source of truth for flag configuration (name, description,
enabled); FlagPilot owns lifecycle metadata, which sync never changes.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.base import utc_now
from app.models.flag import Flag
from app.models.integration import Integration
from app.models.repository import Repository
from app.services import audit
from app.services.flagsmith import FlagsmithClient, FlagsmithError

DEFAULT_ENVIRONMENT_NAME = "Development"


def _projects_to_sync(db: Session, integration: Integration) -> list[int]:
    config = integration.config or {}
    ids: list[int] = []
    if config.get("project_id") is not None:
        ids.append(int(config["project_id"]))
    for pid in db.scalars(select(Repository.flagsmith_project_id).where(
            Repository.flagsmith_integration_id == integration.id,
            Repository.flagsmith_project_id.is_not(None))):
        if pid not in ids:
            ids.append(int(pid))
    return ids


def sync_flagsmith(db: Session, integration: Integration, client: FlagsmithClient) -> dict:
    config = integration.config or {}
    project_ids = _projects_to_sync(db, integration)
    if not project_ids:
        raise FlagsmithError("Nothing to sync: set config.project_id or connect a repository (GitHub sync)")

    projects = {p.get("id"): p.get("name") for p in client.list_projects()}
    stats = {"projects": [], "created": 0, "linked": 0, "updated": 0, "unchanged": 0,
             "conflicts": [], "missing_in_flagsmith": []}
    seen_ids: set[str] = set()

    for project_id in project_ids:
        project_name = projects.get(project_id)
        if project_name is None:
            raise FlagsmithError(f"Flagsmith project {project_id} not found or not accessible")
        stats["projects"].append(project_name)

        # Which environment's on/off state to mirror: explicit key for the main project,
        # otherwise the environment named config.environment_name (default "Development").
        envs = client.list_environments(project_id)
        env = None
        if project_id == config.get("project_id") and config.get("environment_key"):
            env = next((e for e in envs if e.get("api_key") == config["environment_key"]), None)
            if env is None:
                raise FlagsmithError(f"Environment key not found in Flagsmith project {project_id}")
        else:
            wanted = config.get("environment_name", DEFAULT_ENVIRONMENT_NAME)
            env = next((e for e in envs if e.get("name") == wanted), None)
        states = client.list_environment_feature_states(env["api_key"]) if env else {}

        for feature in client.list_features(project_id):
            fs_id = str(feature["id"])
            seen_ids.add(fs_id)
            values = {
                "name": feature["name"],
                "description": feature.get("description") or None,
                "project_name": project_name,
                "environment": env.get("name") if env else None,
                "flagsmith_project_id": project_id,
            }
            if env:
                values["enabled"] = states.get(feature["id"], bool(feature.get("default_enabled")))

            flag = db.scalar(select(Flag).where(Flag.flagsmith_id == fs_id,
                                                Flag.integration_id == integration.id))
            if flag is None:
                same_name = db.scalars(select(Flag).where(Flag.name == feature["name"])).all()
                in_project = [f for f in same_name if f.flagsmith_project_id == project_id]
                local_only = [f for f in same_name if f.flagsmith_id is None and f.flagsmith_project_id is None]
                if in_project:
                    stats["conflicts"].append(f"{project_name}/{feature['name']}")
                    continue
                if local_only:
                    # A FlagPilot-only flag with the same name: adopt it.
                    flag = local_only[0]
                    flag.flagsmith_id, flag.integration_id = fs_id, integration.id
                    stats["linked"] += 1
                    audit.record(db, "flag.linked_to_flagsmith", "flag", flag.id,
                                 {"flagsmith_id": fs_id, "integration_id": integration.id})
                else:
                    # Environment state (in `values`) wins over the project-wide default.
                    flag = Flag(flagsmith_id=fs_id, integration_id=integration.id,
                                **{"enabled": bool(feature.get("default_enabled")), **values})
                    db.add(flag)
                    db.flush()
                    stats["created"] += 1
                    audit.record(db, "flag.imported", "flag", flag.id,
                                 {"flagsmith_id": fs_id, "project": project_name})
                    continue

            changes = {k: v for k, v in values.items() if getattr(flag, k) != v}
            for k, v in changes.items():
                setattr(flag, k, v)
            if changes:
                stats["updated"] += 1
                audit.record(db, "flag.synced", "flag", flag.id, {"changes": changes})
            else:
                stats["unchanged"] += 1

    # Flags linked to this integration whose feature was removed in Flagsmith.
    # Not deleted automatically: a human decides (often they're ready for cleanup).
    linked = db.scalars(select(Flag).where(Flag.integration_id == integration.id,
                                           Flag.flagsmith_id.is_not(None))).all()
    stats["missing_in_flagsmith"] = [f.name for f in linked if f.flagsmith_id not in seen_ids]

    integration.status, integration.status_detail = "connected", None
    integration.last_synced_at = utc_now()
    audit.record(db, "integration.synced", "integration", integration.id, dict(stats))
    return stats
