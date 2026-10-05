"""Application logs with exception diagnostics, excluding credentials and payloads."""
import logging
import re
import traceback

from .config import get_settings


_PRIVATE_FIELD = re.compile(
    r"(?i)([\"']?\b(?:authorization|proxy-authorization|cookie|set-cookie|api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret|messages|prompt|transcript|body|input|input_value|content|text|parameters)[\"']?\s*[:=]\s*)"
)


def _redact_fields(value: str) -> str:
    """Drop quoted/multiline and nested payload values, not just their first line."""
    result = []
    offset = 0
    while match := _PRIVATE_FIELD.search(value, offset):
        result.append(value[offset:match.end()])
        start = end = match.end()
        quote = None
        stack = []
        while end < len(value):
            char = value[end]
            if quote:
                if char == "\\":
                    end += 2
                    continue
                if char == quote:
                    quote = None
                    if not stack:
                        end += 1
                        break
            elif char in "\"'" and (end == start or stack):
                quote = char
            elif char in "[{(":
                stack.append(char)
            elif char in "]})":
                if not stack:
                    break
                stack.pop()
                if not stack:
                    end += 1
                    break
            elif not stack and char in "\r\n,":
                break
            end += 1
        result.append("[REDACTED]")
        offset = end
    result.append(value[offset:])
    return "".join(result)


def redact(value: str) -> str:
    # Replace configured credentials even when an upstream exception echoes them
    # without a recognizable header/field name.
    try:
        settings = get_settings()
        secrets = [settings.llm_api_key]
        from sqlalchemy.engine import make_url
        secrets.append(make_url(settings.database_url).password)
        for secret in secrets:
            if secret:
                value = value.replace(secret, "[REDACTED]")
    except Exception:
        pass  # Logging must also work when configuration itself is invalid.
    value = _redact_fields(value)
    value = re.sub(r"[a-zA-Z][a-zA-Z0-9+.-]*://[^\s<>\"']+", "[REDACTED_URL]", value)
    value = re.sub(r"(?i)\bBearer\s+[^\s\"',;]+", "Bearer [REDACTED]", value)
    return value


class DiagnosticFormatter(logging.Formatter):
    def formatException(self, exc_info):
        from fastapi.exceptions import RequestValidationError, ResponseValidationError
        from pydantic import ValidationError
        from sqlalchemy.exc import StatementError

        seen = set()

        def diagnostic(exc, database=False):
            if id(exc) in seen:
                return ""
            seen.add(id(exc))
            database = database or isinstance(exc, StatementError)
            parts = []
            cause = exc.__cause__ or (None if exc.__suppress_context__ else exc.__context__)
            if cause:
                parts.append(diagnostic(cause, database))
                parts.append("The preceding exception caused the following failure:\n")
            parts.append("Traceback (most recent call last):\n")
            # Frame locations retain the full stack without source literals or locals.
            for frame, line in traceback.walk_tb(exc.__traceback__):
                parts.append(f'  File "{frame.f_code.co_filename}", line {line}, in {frame.f_code.co_name}\n')
            if isinstance(exc, (ValidationError, RequestValidationError, ResponseValidationError)):
                message = f"validation types={[item['type'] for item in exc.errors()]}"
            elif database:
                # SQL and driver DETAIL can echo complete user rows even with
                # hide_parameters enabled. Keep only SQLSTATE/class and a tiny
                # allowlist of generic transport diagnostics.
                original = getattr(exc, "orig", None) or exc
                sqlstate = getattr(original, "sqlstate", None) or getattr(original, "pgcode", None)
                if not isinstance(sqlstate, str) or not re.fullmatch(r"[0-9A-Z]{5}", sqlstate):
                    sqlstate = "unknown"
                generic = re.match(
                    r"(?i)^(?:database )?(?:unavailable|connection (?:refused|reset|closed)|server closed the connection unexpectedly|timeout)$",
                    str(original).strip(),
                )
                message = f"database driver failure sqlstate={sqlstate or 'unknown'}"
                if generic:
                    message += f" ({generic.group(0).lower()})"
            else:
                message = str(exc)
            parts.append(f"{type(exc).__name__}: {redact(message)}\n")
            if isinstance(exc, BaseExceptionGroup):
                parts.extend(diagnostic(child, database) for child in exc.exceptions)
            return "".join(parts)

        return redact(diagnostic(exc_info[1]))

    def format(self, record):
        return redact(super().format(record))


class RedactDiagnostics(logging.Filter):
    def filter(self, record):
        record.msg = redact(record.getMessage())
        record.args = ()
        if record.exc_info:
            record.exc_text = DiagnosticFormatter().formatException(record.exc_info)
        return True


logger = logging.getLogger("lazyclipper")
logger.setLevel(logging.INFO)
logger.addFilter(RedactDiagnostics())
handler = logging.StreamHandler()
handler.setFormatter(DiagnosticFormatter("%(asctime)s %(levelname)s %(message)s"))
logger.addHandler(handler)


def configure_logging() -> None:
    # Uvicorn can log an unhandled exception again after FastAPI's 500 handler.
    # Apply the same redaction there, including chained exception tracebacks.
    uvicorn_logger = logging.getLogger("uvicorn.error")
    if not any(isinstance(item, RedactDiagnostics) for item in uvicorn_logger.filters):
        uvicorn_logger.addFilter(RedactDiagnostics())
    for name in ("httpx", "httpcore", "openai"):
        logging.getLogger(name).setLevel(logging.WARNING)


def log_failure(exc: Exception, *, project_id: str | None, stage: str, clip_id: str | None = None, context: str = "PIPELINE") -> None:
    if clip_id and context == "PIPELINE":
        context = "CLIP"
    logger.exception(
        "[%s] project=%s clip=%s stage=%s status=failed type=%s",
        context, project_id or "-", clip_id or "-", stage, type(exc).__name__,
        exc_info=(type(exc), exc, exc.__traceback__),
    )


def log_client_error(exc: Exception, *, project_id: str | None, stage: str, status: int,
                     clip_id: str | None = None, context: str = "API") -> None:
    """Record expected request mistakes without exception tracebacks."""
    logger.warning(
        "[%s] project=%s clip=%s stage=%s status=rejected http_status=%s type=%s",
        context, project_id or "-", clip_id or "-", stage, status, type(exc).__name__,
    )


def log_stage(project_id: str, stage: str, status: str, clip_id: str | None = None) -> None:
    logger.info("[%s] project=%s clip=%s stage=%s status=%s", "CLIP" if clip_id else "PIPELINE", project_id, clip_id or "-", stage, status)
