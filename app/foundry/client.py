import json
import logging
import os
from typing import Optional

from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential
from pydantic import ValidationError

from app.errors import AgentResponseValidationError, FoundryCallError
from app.schemas.response_models import MaintenanceAssessment

logger = logging.getLogger(__name__)

# Built once per worker process (cold start) and reused across warm invocations —
# DefaultAzureCredential token acquisition is the expensive part of constructing this.
_project_client: Optional[AIProjectClient] = None


def _get_project_client() -> AIProjectClient:
    global _project_client
    if _project_client is None:
        endpoint = os.environ["PROJECT_ENDPOINT"]
        _project_client = AIProjectClient(endpoint=endpoint, credential=DefaultAzureCredential())
    return _project_client


def _strip_markdown_fence(raw_text: str) -> str:
    text = raw_text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    return text.strip()


def _parse_and_validate(raw_text: str) -> MaintenanceAssessment:
    cleaned = _strip_markdown_fence(raw_text)
    data = json.loads(cleaned)
    return MaintenanceAssessment.model_validate(data)


_UPSTREAM_USER_MESSAGE = "The diagnostic assistant could not be reached. Please try again."
_VALIDATION_USER_MESSAGE = "The assistant's response did not match the expected format. Please try again."


def _run_response(openai_client, conversation_id: str, agent_name: str) -> str:
    response = openai_client.responses.create(
        conversation=conversation_id,
        extra_body={"agent_reference": {"name": agent_name, "type": "agent_reference"}},
        input="",
    )
    if response.status != "completed":
        detail = getattr(response, "error", None) or getattr(response, "incomplete_details", None)
        raise FoundryCallError(
            f"Agent response ended with status '{response.status}': {detail}",
            user_message=_UPSTREAM_USER_MESSAGE,
        )
    return response.output_text


def get_assessment(request_text: str, grounding_block: str) -> MaintenanceAssessment:
    agent_name = os.environ["AGENT_NAME"]
    client = _get_project_client()
    composed_message = f"{request_text}\n\n---\n\n{grounding_block}"

    with client.get_openai_client() as openai_client:
        conversation = None
        try:
            try:
                conversation = openai_client.conversations.create(
                    items=[{"type": "message", "role": "user", "content": composed_message}]
                )
                raw_text = _run_response(openai_client, conversation.id, agent_name)
            except FoundryCallError:
                raise
            except Exception as exc:
                raise FoundryCallError(
                    f"Failed to call Foundry agent: {exc}", user_message=_UPSTREAM_USER_MESSAGE
                ) from exc

            try:
                return _parse_and_validate(raw_text)
            except (json.JSONDecodeError, ValidationError) as exc:
                first_error = exc
                logger.warning("Agent response failed schema validation, retrying once: %s", first_error)

            corrective_message = (
                "Your previous response did not match the required JSON schema: "
                f"{first_error}. Return ONLY a valid JSON object matching the schema, with no markdown."
            )
            try:
                openai_client.conversations.items.create(
                    conversation_id=conversation.id,
                    items=[{"type": "message", "role": "user", "content": corrective_message}],
                )
                raw_text = _run_response(openai_client, conversation.id, agent_name)
            except FoundryCallError:
                raise
            except Exception as exc:
                raise FoundryCallError(
                    f"Failed to call Foundry agent on retry: {exc}", user_message=_UPSTREAM_USER_MESSAGE
                ) from exc

            try:
                return _parse_and_validate(raw_text)
            except (json.JSONDecodeError, ValidationError) as exc:
                second_error = exc
                raise AgentResponseValidationError(
                    "Agent response still did not match the required schema after one retry. "
                    f"First error: {first_error}. Second error: {second_error}. Raw response: {raw_text}",
                    user_message=_VALIDATION_USER_MESSAGE,
                ) from second_error
        finally:
            if conversation is not None:
                try:
                    openai_client.conversations.delete(conversation_id=conversation.id)
                except Exception:
                    logger.warning("Failed to delete conversation %s", conversation.id, exc_info=True)
