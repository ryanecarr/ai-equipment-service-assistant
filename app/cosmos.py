import logging
import os
from contextlib import contextmanager
from typing import Any, Iterator, Mapping, Optional

from azure.core.exceptions import AzureError
from azure.cosmos import CosmosClient, DatabaseProxy
from azure.identity import DefaultAzureCredential
from pydantic import BaseModel, ValidationError

from app.errors import GroundingDataUnavailableError, InvalidGroundingRecordError

logger = logging.getLogger(__name__)

_UNAVAILABLE_USER_MESSAGE = "Equipment records are temporarily unavailable. Please try again."
_INVALID_RECORD_USER_MESSAGE = "A stored equipment record could not be read."

# Built once per worker process (cold start) and reused across warm invocations, and
# shared by every repository — same pattern as the AIProjectClient singleton in
# app/foundry/client.py. Only the client is cached, never data, so a record change is
# visible on the next request.
_database: Optional[DatabaseProxy] = None


def log_request_charge(operation: str, headers: Mapping[str, Any], _result: Any) -> None:
    logger.info("Cosmos %s request charge: %s RU", operation, headers.get("x-ms-request-charge"))


@contextmanager
def translate_cosmos_errors(operation: str) -> Iterator[None]:
    try:
        yield
    except AzureError as exc:
        # AzureError rather than CosmosHttpResponseError: a failed token acquisition
        # (ClientAuthenticationError) or an unreachable endpoint (ServiceRequestError)
        # never produces a Cosmos HTTP response at all. Throttling lands here too, but
        # only after the SDK's own 429 retries are exhausted.
        status_code = getattr(exc, "status_code", None)
        sub_status = getattr(exc, "sub_status", None)
        raise GroundingDataUnavailableError(
            f"Cosmos {operation} failed: {type(exc).__name__} (status={status_code}, sub_status={sub_status})",
            user_message=_UNAVAILABLE_USER_MESSAGE,
        ) from exc


def validated_record(
    document: Mapping[str, Any], schema: type[BaseModel], storage_fields: frozenset[str]
) -> dict[str, Any]:
    record = {k: v for k, v in document.items() if k not in storage_fields and not k.startswith("_")}
    try:
        schema.model_validate(record)
    except ValidationError as exc:
        # Field names and error types only — ValidationError's own text quotes the
        # offending values, so it stays out of the detail, and "from None" keeps it
        # out of the logged traceback, which would otherwise quote record contents.
        problems = [
            f"{'.'.join(str(part) for part in error['loc'])}: {error['type']}"
            for error in exc.errors(include_url=False, include_context=False, include_input=False)
        ]
        raise InvalidGroundingRecordError(
            f"{schema.__name__} '{document.get('id')}' failed validation: {problems}",
            user_message=_INVALID_RECORD_USER_MESSAGE,
        ) from None
    return record


def get_database() -> DatabaseProxy:
    global _database
    if _database is None:
        # Inside the translation because CosmosClient() does network I/O while
        # constructing (it fetches the account's regional endpoints). A failure leaves
        # _database unset, so the next request retries rather than staying broken.
        with translate_cosmos_errors("client initialization"):
            client = CosmosClient(url=os.environ["COSMOS_ENDPOINT"], credential=DefaultAzureCredential())
            _database = client.get_database_client(os.environ.get("COSMOS_DATABASE_NAME", "equipment-service-db"))
    return _database
