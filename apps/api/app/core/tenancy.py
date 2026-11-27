"""Workspace isolation, enforced centrally.

While a request has a current workspace (set by the `get_workspace` dependency):
- every ORM SELECT/UPDATE/DELETE on a tenant model is filtered to that workspace, and
- every new tenant row gets that workspace_id on flush.
So an endpoint can't forget to scope a query. Outside a request (scripts, migrations)
there's no current workspace and no filtering.
"""

from contextvars import ContextVar

from sqlalchemy import ForeignKey, String, event
from sqlalchemy.orm import Mapped, ORMExecuteState, Session, mapped_column, with_loader_criteria

current_workspace_id: ContextVar[str | None] = ContextVar("current_workspace_id", default=None)


class TenantScoped:
    """Mixin: adds `workspace_id`; the model is isolated per workspace."""

    workspace_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )


@event.listens_for(Session, "do_orm_execute")
def _scope_queries(state: ORMExecuteState) -> None:
    ws = current_workspace_id.get()
    if ws is None or state.is_column_load or state.is_relationship_load:
        return
    if state.is_select or state.is_update or state.is_delete:
        state.statement = state.statement.options(
            with_loader_criteria(TenantScoped, lambda cls: cls.workspace_id == ws, include_aliases=True)
        )


@event.listens_for(Session, "before_flush")
def _stamp_new_rows(session: Session, flush_context, instances) -> None:
    ws = current_workspace_id.get()
    if ws is None:
        return
    for obj in session.new:
        if isinstance(obj, TenantScoped) and getattr(obj, "workspace_id", None) is None:
            obj.workspace_id = ws
