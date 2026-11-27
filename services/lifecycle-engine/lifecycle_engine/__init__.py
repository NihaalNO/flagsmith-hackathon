"""FlagPilot lifecycle intelligence engine.

Deterministic analysis (discovery, code intelligence, lifecycle, stale detection,
verification) never depends on an LLM. The cleanup planner can use an injectable
LLM provider for narrative reasoning, but every fact it emits is validated against
deterministic evidence.
"""

ENGINE_VERSION = "0.1.0"

__all__ = ["ENGINE_VERSION"]
