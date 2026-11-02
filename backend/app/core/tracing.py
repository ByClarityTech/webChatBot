from app.core.config import get_settings


def tag_langsmith_run(**metadata: str) -> None:
    """Attach metadata to the current LangSmith run when tracing is enabled."""
    settings = get_settings()
    if not settings.langsmith_enabled:
        return
    try:
        from langsmith.run_helpers import get_current_run_tree

        run = get_current_run_tree()
        if run is not None:
            run.metadata = {**(run.metadata or {}), **metadata}
    except Exception:
        pass
