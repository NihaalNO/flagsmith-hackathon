"""Pull features from Flagsmith into FlagPilot.

Synced projects: the integration's `config.project_id` (if set) plus every repository's
own project. Flagsmith is the source of truth for flag configuration (name, description,
enabled); FlagPilot owns lifecycle metadata, which sync never changes.

Besides the one environment mirrored onto `Flag.enabled` (for the board), sync records
feature metadata (creation date, tags, owners, type, archived) and every environment's live
state (`FlagEnvironmentState`: enabled, value, segment/identity overrides, multivariate
split, last modified) for the lifecycle engine.
"""

from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.base import utc_now
from app.models.flag import Flag
from app.models.flag_environment_state import FlagEnvironmentState
from app.models.integration import Integration
from app.models.repository import Repository
from app.services import audit
from app.services.flagsmith import FlagsmithClient, FlagsmithError

DEFAULT_ENVIRONMENT_NAME = "Development"


def _parse_dt(value) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _same(a, b) -> bool:
    """Equality that ignores a missing timezone (SQLite drops it on read)."""
    if isinstance(a, datetime) and isinstance(b, datetime):
        a = a if a.tzinfo else a.replace(tzinfo=timezone.utc)
        b = b if b.tzinfo else b.replace(tzinfo=timezone.utc)
    return a == b


def _jsonable(changes: dict) -> dict:
    return {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in changes.items()}


def _feature_metadata(feature: dict, tags: dict[int, str]) -> dict:
    """Flagsmith feature metadata; keys absent from the payload stay unknown (None)."""
    owners = feature.get("owners")
    tag_ids = feature.get("tags")
    return {
        "flagsmith_created_at": _parse_dt(feature.get("created_date")),
        "flagsmith_tags": None if tag_ids is None else [
            tags.get(int(t["id"] if isinstance(t, dict) else t), str(t)) for t in tag_ids],
        "flagsmith_owner_ids": None if owners is None else [
            int(o["id"]) if isinstance(o, dict) else int(o) for o in owners],
        "flagsmith_type": feature.get("type") or None,
        "is_archived_in_flagsmith": bool(feature.get("is_archived", False)),
    }


def _split(fs: dict) -> float | None:
    """Multivariate allocation -> engine's percentage_allocation (see FlagEnvironmentState)."""
    allocs = [float(v.get("percentage_allocation") or 0) for v in fs.get("multivariate_feature_state_values") or []]
    allocs = [a for a in allocs if a > 0]
    if not allocs:
        return None
    if len(allocs) == 1 and allocs[0] >= 100:
        return 100.0
    return min(allocs)


def _environment_state(feature: dict) -> dict | None:
    """Live state of a feature from `features/?environment=`; None if the server didn't send it."""
    fs = feature.get("environment_feature_state")
    if not isinstance(fs, dict) or "enabled" not in fs:
        return None
    return {
        "enabled": bool(fs["enabled"]),
        "value": fs.get("feature_state_value"),
        "segment_overrides": int(feature.get("num_segment_overrides") or 0),
        "identity_overrides": feature.get("num_identity_overrides"),
        "percentage_allocation": _split(fs),
        "last_modified_at": _parse_dt(feature.get("last_modified_in_current_environment") or fs.get("updated_at")),
    }


def _sync_environment_states(db: Session, client: FlagsmithClient, project_id: int, envs: list[dict],
                             flags_by_feature: dict[int, Flag], stats: dict) -> None:
    now = utc_now()
    env_ids = {int(e["id"]) for e in envs if e.get("id") is not None}
    for env in envs:
        if env.get("id") is None:
            continue
        try:
            features = client.list_features(project_id, environment_id=int(env["id"]))
        except FlagsmithError as exc:
            stats["warnings"].append(f"environment '{env.get('name')}' skipped: {exc}")
            continue
        recorded = 0
        for feature in features:
            flag = flags_by_feature.get(feature.get("id"))
            state = _environment_state(feature) if flag is not None else None
            if state is None:
                continue
            row = db.scalar(select(FlagEnvironmentState).where(
                FlagEnvironmentState.flag_id == flag.id,
                FlagEnvironmentState.flagsmith_environment_id == int(env["id"])))
            if row is None:
                row = FlagEnvironmentState(flag_id=flag.id, flagsmith_environment_id=int(env["id"]))
                db.add(row)
            row.environment_name = env.get("name") or str(env["id"])
            for k, v in state.items():
                setattr(row, k, v)
            row.synced_at = now
            recorded += 1
        stats["environment_states"] += recorded
        if features and not recorded and flags_by_feature:
            stats["warnings"].append(f"environment '{env.get('name')}': Flagsmith returned no per-environment state")
    # States for environments that no longer exist in the project.
    flag_ids = [f.id for f in flags_by_feature.values()]
    if flag_ids:
        db.execute(delete(FlagEnvironmentState).where(FlagEnvironmentState.flag_id.in_(flag_ids),
                                                      FlagEnvironmentState.flagsmith_environment_id.not_in(env_ids)))


MAX_FALLBACK_PROJECTS = 10


def _projects_to_sync(db: Session, integration: Integration, visible: dict[int, str], stats: dict) -> list[int]:
    """Which Flagsmith projects to sync with this connection's key.

    1. `config.project_id`, if set (an error if the key can't see it: it was configured explicitly).
    2. Every repository's linked project that this key can see, whichever connection linked it.
       Repos whose connection was removed are re-attached to this one. Links to projects the key
       can't see are skipped with a warning instead of failing the whole sync.
    3. If nothing is linked yet, the projects the key can see (up to MAX_FALLBACK_PROJECTS, by
       name), with a warning, so flags from Flagsmith appear instead of a dead end.
    """
    config = integration.config or {}
    ids: list[int] = []
    if config.get("project_id") is not None:
        pid = int(config["project_id"])
        if pid not in visible:
            raise FlagsmithError(f"Flagsmith project {pid} (config.project_id) not found or not accessible with this API key")
        ids.append(pid)
    repos = db.scalars(select(Repository).where(Repository.flagsmith_project_id.is_not(None))
                       .order_by(Repository.full_name)).all()
    live = set(db.scalars(select(Integration.id).where(Integration.provider == "flagsmith")))
    for repo in repos:
        pid = int(repo.flagsmith_project_id)
        if repo.flagsmith_integration_id is not None and repo.flagsmith_integration_id not in live:
            repo.flagsmith_integration_id = None  # its connection was deleted
        other_connection = repo.flagsmith_integration_id not in (None, integration.id)
        if pid not in visible:
            if not other_connection:
                stats["warnings"].append(
                    f"{repo.full_name}: its Flagsmith project #{pid} isn't visible to this API key; "
                    "choose its project again on the Repositories page")
            continue
        if repo.flagsmith_integration_id is None:
            repo.flagsmith_integration_id = integration.id  # its old connection was removed
        if pid not in ids:
            ids.append(pid)
    if ids:
        return ids
    if not visible:
        raise FlagsmithError("This Flagsmith API key can't see any projects")
    by_name = sorted(visible, key=lambda pid: (visible[pid] or "").lower())
    chosen = by_name[:MAX_FALLBACK_PROJECTS]
    more = f" (the first {len(chosen)} of {len(visible)} by name)" if len(visible) > len(chosen) else ""
    stats["warnings"].append(
        f"No repository is linked to a Flagsmith project yet, so every project this key can see was synced{more}. "
        "Link each repository to its project on the Repositories page so scans and analyses match its flags.")
    return chosen


def _sync_project(db: Session, integration: Integration, client: FlagsmithClient, config: dict,
                  project_id: int, project_name: str, stats: dict, seen_ids: set[str]) -> None:
    """Import/update one project's features, metadata and environment states.

    Every Flagsmith call that can fail for the whole project happens before anything is written."""
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
    try:
        tags = client.list_tags(project_id)
    except FlagsmithError as exc:
        tags = {}
        stats["warnings"].append(f"{project_name}: tags not read ({exc})")
    flags_by_feature: dict[int, Flag] = {}

    for feature in client.list_features(project_id):
        fs_id = str(feature["id"])
        seen_ids.add(fs_id)
        values = {
            "name": feature["name"],
            "description": feature.get("description") or None,
            "project_name": project_name,
            "environment": env.get("name") if env else None,
            "flagsmith_project_id": project_id,
            **_feature_metadata(feature, tags),
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
                flags_by_feature[feature["id"]] = flag
                stats["created"] += 1
                audit.record(db, "flag.imported", "flag", flag.id,
                             {"flagsmith_id": fs_id, "project": project_name})
                continue

        flags_by_feature[feature["id"]] = flag
        changes = {k: v for k, v in values.items() if not _same(getattr(flag, k), v)}
        for k, v in changes.items():
            setattr(flag, k, v)
        if changes:
            stats["updated"] += 1
            audit.record(db, "flag.synced", "flag", flag.id, {"changes": _jsonable(changes)})
        else:
            stats["unchanged"] += 1

    _sync_environment_states(db, client, project_id, envs, flags_by_feature, stats)


def sync_flagsmith(db: Session, integration: Integration, client: FlagsmithClient) -> dict:
    config = integration.config or {}
    projects = {int(p["id"]): p.get("name") or str(p["id"]) for p in client.list_projects() if p.get("id") is not None}
    stats = {"projects": [], "created": 0, "linked": 0, "updated": 0, "unchanged": 0,
             "conflicts": [], "missing_in_flagsmith": [], "environment_states": 0, "warnings": []}
    seen_ids: set[str] = set()
    project_ids = _projects_to_sync(db, integration, projects, stats)

    skipped: dict[int, str] = {}
    for project_id in project_ids:
        project_name = projects[project_id]
        try:
            _sync_project(db, integration, client, config, project_id, project_name, stats, seen_ids)
        except FlagsmithError as exc:
            skipped[project_id] = str(exc)
            stats["warnings"].append(f"{project_name}: skipped ({exc})")
            continue
        stats["projects"].append(project_name)
    if skipped and len(skipped) == len(project_ids):
        raise FlagsmithError("No project could be synced: " + "; ".join(
            f"{projects[pid]}: {err}" for pid, err in skipped.items()))

    # Flags linked to this integration whose feature was removed in Flagsmith.
    # Not deleted automatically: a human decides (often they're ready for cleanup).
    linked = db.scalars(select(Flag).where(Flag.integration_id == integration.id,
                                           Flag.flagsmith_id.is_not(None))).all()
    # Flags of skipped projects weren't checked, so they can't be called missing.
    stats["missing_in_flagsmith"] = [f.name for f in linked
                                     if f.flagsmith_id not in seen_ids and f.flagsmith_project_id not in skipped]

    integration.status, integration.status_detail = "connected", None
    integration.last_synced_at = utc_now()
    audit.record(db, "integration.synced", "integration", integration.id, dict(stats))
    return stats
