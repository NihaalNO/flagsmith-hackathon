"""Pull features from a Flagsmith project into FlagPilot.

Flagsmith is the source of truth for flag configuration (name, description,
enabled). FlagPilot owns lifecycle metadata, which sync never changes.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.base import utc_now
from app.models.flag import Flag
from app.models.integration import Integration
from app.services import audit
from app.services.flagsmith import FlagsmithClient, FlagsmithError


def sync_flagsmith(db: Session, integration: Integration, client: FlagsmithClient) -> dict:
    config = integration.config or {}
    project_id = config.get("project_id")
    if project_id is None:
        raise FlagsmithError("Set config.project_id on the integration before syncing")
    project_id = int(project_id)
    env_key = config.get("environment_key")

    project_name = next(
        (p.get("name") for p in client.list_projects() if p.get("id") == project_id), None
    )
    if project_name is None:
        raise FlagsmithError(f"Flagsmith project {project_id} not found or not accessible")

    env_name, states = None, {}
    if env_key:
        env_name = next(
            (e.get("name") for e in client.list_environments(project_id) if e.get("api_key") == env_key),
            None,
        )
        if env_name is None:
            raise FlagsmithError(f"Environment key not found in Flagsmith project {project_id}")
        states = client.list_environment_feature_states(env_key)

    features = client.list_features(project_id)
    stats = {"created": 0, "linked": 0, "updated": 0, "unchanged": 0, "conflicts": [],
             "missing_in_flagsmith": []}
    seen_ids: set[str] = set()

    for feature in features:
        fs_id = str(feature["id"])
        seen_ids.add(fs_id)
        values = {
            "name": feature["name"],
            "description": feature.get("description") or None,
            "project_name": project_name,
            "environment": env_name,
        }
        if env_key:
            values["enabled"] = states.get(feature["id"], bool(feature.get("default_enabled")))

        flag = db.scalar(select(Flag).where(Flag.flagsmith_id == fs_id, Flag.integration_id == integration.id))
        if flag is None:
            by_name = db.scalar(select(Flag).where(Flag.name == feature["name"]))
            if by_name and by_name.flagsmith_id is None:
                # A FlagPilot-only flag with the same name: adopt it.
                flag = by_name
                flag.flagsmith_id, flag.integration_id = fs_id, integration.id
                stats["linked"] += 1
                audit.record(db, "flag.linked_to_flagsmith", "flag", flag.id,
                             {"flagsmith_id": fs_id, "integration_id": integration.id})
            elif by_name:
                stats["conflicts"].append(feature["name"])
                continue
            else:
                # Environment state (in `values`) wins over the project-wide default.
                flag = Flag(flagsmith_id=fs_id, integration_id=integration.id,
                            **{"enabled": bool(feature.get("default_enabled")), **values})
                db.add(flag)
                db.flush()
                stats["created"] += 1
                audit.record(db, "flag.imported", "flag", flag.id,
                             {"flagsmith_id": fs_id, "integration_id": integration.id})
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
    linked = db.scalars(select(Flag).where(Flag.integration_id == integration.id)).all()
    stats["missing_in_flagsmith"] = [f.name for f in linked if f.flagsmith_id not in seen_ids]

    integration.status, integration.status_detail = "connected", None
    integration.last_synced_at = utc_now()
    audit.record(db, "integration.synced", "integration", integration.id,
                 {k: v for k, v in stats.items()})
    return stats
