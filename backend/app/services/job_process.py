"""One local child per accepted job, never a separate worker service or queue."""
import json
import sys

from ..config import Settings, set_process_settings
from ..logging import configure_logging, log_failure


def main() -> int:
    configure_logging()
    try:
        # Private stdin carries a settings snapshot; never use credentials in argv.
        settings = Settings(_env_file=None, **json.load(sys.stdin))
        set_process_settings(settings)
        from ..db import session_factory
        from .pipeline import Pipeline

        pipeline = Pipeline(settings, make_session=session_factory(settings.database_url))
        if len(sys.argv) == 3:
            pipeline.render_clip(sys.argv[1], sys.argv[2])
        elif len(sys.argv) == 2:
            pipeline.run(sys.argv[1])
        else:
            raise ValueError("Expected project ID and optional clip ID")
        return 0
    except Exception as exc:
        log_failure(exc, project_id=None, stage="unknown", context="JOB_STARTUP")
        return 1


if __name__ == "__main__":
    sys.exit(main())
