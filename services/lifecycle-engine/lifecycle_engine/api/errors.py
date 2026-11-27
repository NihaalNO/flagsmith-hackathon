from __future__ import annotations

from ..schemas.common import ErrorDetail, ErrorResponse
from ..state_machine.machine import LifecycleError


class EngineError(Exception):
    code = "engine_error"
    http_status = 400

    def __init__(self, message: str, **details):
        super().__init__(message)
        self.details = details


class RepositoryNotFound(EngineError):
    code, http_status = "repository_not_found", 404


class PathNotAllowed(EngineError):
    code, http_status = "path_not_allowed", 403


class CommandsNotAllowed(EngineError):
    code, http_status = "commands_not_allowed", 403


class MissingInput(EngineError):
    code, http_status = "missing_input", 422


LIFECYCLE_STATUS = {"invalid_transition": 409, "transition_requirements_not_met": 422, "safety_violation": 422}


def to_error(exc: Exception) -> tuple[int, ErrorResponse]:
    if isinstance(exc, EngineError):
        return exc.http_status, ErrorResponse(error=ErrorDetail(code=exc.code, message=str(exc), details=exc.details))
    if isinstance(exc, LifecycleError):
        return LIFECYCLE_STATUS.get(exc.code, 400), ErrorResponse(error=ErrorDetail(code=exc.code, message=str(exc), details=exc.details))
    return 500, ErrorResponse(error=ErrorDetail(code="internal_error", message="unexpected engine error"))
