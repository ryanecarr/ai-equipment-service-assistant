from typing import Optional


class AppError(Exception):
    status_code: int
    code: str

    def __init__(self, detail: str, *, user_message: Optional[str] = None):
        super().__init__(detail)
        # Full diagnostic detail — always logged (via exc_info=True / str(exc)),
        # never returned to the caller. user_message is what the HTTP response
        # body actually shows; it defaults to detail for error types where the
        # detail is already safe to echo (e.g. InvalidRequestError, which only
        # ever describes what's wrong with the caller's own request).
        self.detail = detail
        self.user_message = user_message if user_message is not None else detail


class InvalidRequestError(AppError):
    status_code = 400
    code = "invalid_request"


class FoundryCallError(AppError):
    status_code = 502
    code = "upstream_agent_error"


class AgentResponseValidationError(AppError):
    status_code = 502
    code = "invalid_agent_response"


class GroundingDataUnavailableError(AppError):
    status_code = 503
    code = "grounding_data_unavailable"


class InvalidGroundingRecordError(AppError):
    status_code = 500
    code = "invalid_grounding_record"
