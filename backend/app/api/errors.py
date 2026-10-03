"""Sanitize every HTTP error, including validation, media and database failures."""
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException

from ..errors import CLIENT_MESSAGES, PublicError, Stage, failure_stage, processing_error
from ..logging import log_failure


def request_stage(request: Request) -> Stage:
    path = request.url.path
    if path.endswith("/media"):
        return "media"
    if path.endswith("/ingest"):
        return "ingestion"
    if "/clips" in path:
        return "rendering" if request.method == "POST" else "media"
    if "/projects" in path:
        return "database"
    return "unknown"


def error_response(status: int, stage: Stage, error: PublicError) -> JSONResponse:
    return JSONResponse(status_code=status, content={"status": "failed", "failed_stage": stage, "error": error.model_dump()})


def install_error_handlers(app: FastAPI) -> None:
    async def handle_error(request: Request, exc: Exception):
        stage = failure_stage(exc, request_stage(request))
        log_failure(exc, project_id=getattr(request.state, "project_id", request.path_params.get("project_id")),
                    clip_id=getattr(request.state, "clip_id", request.path_params.get("clip_id")), stage=stage, context="API")
        status = exc.status_code if isinstance(exc, HTTPException) else 500
        error = processing_error(stage)
        if isinstance(exc, HTTPException) and status < 500:
            code = next((code for code, message in CLIENT_MESSAGES.items() if message == exc.detail), "INVALID_REQUEST")
            error = PublicError(code=code, message=CLIENT_MESSAGES[code])
        return error_response(status, stage, error)

    async def handle_validation(request: Request, exc: RequestValidationError):
        stage = request_stage(request)
        log_failure(exc, project_id=request.path_params.get("project_id"), stage=stage, context="API")
        code = "INVALID_URL" if request.url.path.endswith("/ingest") else "INVALID_REQUEST"
        if any(item["type"] == "value_error" and item.get("loc") == ("body",) for item in exc.errors()):
            code = "INVALID_RANGE"
        return error_response(422, stage, PublicError(code=code, message=CLIENT_MESSAGES[code]))

    app.add_exception_handler(HTTPException, handle_error)
    app.add_exception_handler(SQLAlchemyError, handle_error)
    app.add_exception_handler(Exception, handle_error)
    app.add_exception_handler(RequestValidationError, handle_validation)
