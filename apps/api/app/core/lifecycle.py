from enum import StrEnum


class LifecycleStatus(StrEnum):
    CREATED = "created"
    IN_DEVELOPMENT = "in_development"
    TESTING = "testing"
    ROLLING_OUT = "rolling_out"
    FULLY_RELEASED = "fully_released"
    READY_FOR_CLEANUP = "ready_for_cleanup"
    CLEANUP_IN_PROGRESS = "cleanup_in_progress"
    RETIRED = "retired"


S = LifecycleStatus

# Which statuses a flag may move to from each status.
ALLOWED_TRANSITIONS: dict[LifecycleStatus, set[LifecycleStatus]] = {
    S.CREATED: {S.IN_DEVELOPMENT, S.RETIRED},
    S.IN_DEVELOPMENT: {S.TESTING, S.RETIRED},
    S.TESTING: {S.IN_DEVELOPMENT, S.ROLLING_OUT, S.RETIRED},
    S.ROLLING_OUT: {S.TESTING, S.FULLY_RELEASED},
    S.FULLY_RELEASED: {S.ROLLING_OUT, S.READY_FOR_CLEANUP},
    S.READY_FOR_CLEANUP: {S.CLEANUP_IN_PROGRESS, S.FULLY_RELEASED},
    S.CLEANUP_IN_PROGRESS: {S.RETIRED, S.READY_FOR_CLEANUP},
    S.RETIRED: set(),
}


def can_transition(current: str, target: str) -> bool:
    return LifecycleStatus(target) in ALLOWED_TRANSITIONS[LifecycleStatus(current)]
