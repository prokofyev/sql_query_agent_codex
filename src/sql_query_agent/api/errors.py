"""Ошибки HTTP-слоя в едином конверте."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from sql_query_agent.logging_setup import get_logger

logger = get_logger(__name__)

INVALID_SQL = "invalid_sql"
VALIDATION_ERROR = "validation_error"
NOT_FOUND = "not_found"
PRESETS_UNAVAILABLE = "presets_unavailable"
SCHEMA_UNAVAILABLE = "schema_unavailable"


class ApiError(RuntimeError):
    """Ошибка обработки запроса с кодом и текстом для пользователя."""

    def __init__(self, code: str, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def install_error_handlers(application: FastAPI) -> None:
    """Зарегистрировать обработчики ошибок."""

    @application.exception_handler(ApiError)
    async def handle_api_error(_request: Request, error: ApiError) -> JSONResponse:
        """Вернуть ошибку в едином конверте."""

        return JSONResponse(
            status_code=error.status_code,
            content={"code": error.code, "message": error.message},
        )

    @application.exception_handler(RequestValidationError)
    async def handle_validation_error(
        _request: Request, error: RequestValidationError
    ) -> JSONResponse:
        """Привести ошибки разбора тела запроса к тому же конверту."""

        message = "; ".join(
            f"{'.'.join(str(part) for part in item.get('loc', ()))}: {item.get('msg')}"
            for item in error.errors()
        )
        return JSONResponse(
            status_code=422,
            content={"code": VALIDATION_ERROR, "message": message or "неверный запрос"},
        )


__all__ = [
    "INVALID_SQL",
    "NOT_FOUND",
    "PRESETS_UNAVAILABLE",
    "SCHEMA_UNAVAILABLE",
    "VALIDATION_ERROR",
    "ApiError",
    "install_error_handlers",
]
