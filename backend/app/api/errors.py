"""One error shape everywhere (CLAUDE.md section 9):
{ "error": { "code": str, "message": str, "request_id": str } }"""

from __future__ import annotations

import logging

import psycopg
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.db.connection import DatabaseNotConfigured

log = logging.getLogger("app.errors")


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "-")


def error_response(request: Request, status: int, code: str, message: str) -> JSONResponse:
    rid = _request_id(request)
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message, "request_id": rid}},
        headers={"X-Request-ID": rid},
    )


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
        return error_response(request, exc.status, exc.code, exc.message)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}.get(exc.status_code, "HTTP_ERROR")
        return error_response(request, exc.status_code, code, str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        where = ".".join(str(p) for p in first.get("loc", []))
        message = f"{where}: {first.get('msg', 'invalid request')}" if where else "invalid request"
        return error_response(request, 422, "VALIDATION_ERROR", message)

    @app.exception_handler(DatabaseNotConfigured)
    async def _db_not_configured(request: Request, exc: DatabaseNotConfigured) -> JSONResponse:
        log.error("DATABASE_URL is not set")
        return error_response(request, 503, "DB_NOT_CONFIGURED", "The database is not configured.")

    @app.exception_handler(psycopg.OperationalError)
    async def _db_down(request: Request, exc: psycopg.OperationalError) -> JSONResponse:
        log.error("database unavailable: %s", type(exc).__name__)  # no DSN or secrets in logs
        return error_response(request, 503, "DB_UNAVAILABLE", "The database is not reachable.")

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error")
        return error_response(request, 500, "INTERNAL_ERROR", "Something went wrong.")
