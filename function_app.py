import json
import logging
import re

import azure.functions as func
from pydantic import ValidationError

from app.errors import AppError, InvalidRequestError
from app.foundry.client import get_assessment
from app.grounding.lookup import (
    build_grounding,
    equipment_exists,
    get_known_work_order_ids,
    list_equipment_summaries,
    resolve_equipment,
    to_equipment_summary,
)
from app.history.lookup import build_history_block, list_saved_assessments, save_assessment
from app.schemas.history_models import SaveAssessmentRequest
from app.schemas.request_models import MaintenanceRequest
from app.schemas.response_models import AssessmentResponse

logger = logging.getLogger(__name__)

app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)

# The UI already renders each recommendations item inside its own <li> — a leading
# bullet/dash the agent includes in the item's own text produces a doubled bullet on
# screen. The prompt instructs the agent not to do this, but formatting slip-ups in
# free-text generation aren't guaranteed to follow instructions every time, so this
# strips any such prefix regardless of what the agent actually returned.
# \s+ (not \s*) after the marker is required: a bare "-" with no trailing space is
# indistinguishable from the sign of a negative number (e.g. "-10°C ambient
# temperature"), and stripping that would silently flip its meaning. A real bullet
# marker is always followed by whitespace before the item's text; a negative number
# never is.
_LEADING_BULLET_RE = re.compile(r"^\s*[-*•–—]+\s+")


def _strip_leading_bullet(text: str) -> str:
    return _LEADING_BULLET_RE.sub("", text)


def _error_response(status_code: int, code: str, message: str) -> func.HttpResponse:
    return func.HttpResponse(
        body=json.dumps({"error": {"code": code, "message": message}}),
        status_code=status_code,
        mimetype="application/json",
    )


@app.route(route="assess", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
def assess(req: func.HttpRequest) -> func.HttpResponse:
    if req.method != "POST":
        return _error_response(405, "method_not_allowed", "Only POST is supported.")

    try:
        body = req.get_json()
    except ValueError:
        return _error_response(400, "invalid_json", "Request body must be valid JSON.")

    try:
        parsed = MaintenanceRequest.model_validate(body)
    except ValidationError as exc:
        return _error_response(400, "invalid_request", str(exc))

    try:
        # Grounding + history lookups both do real Cosmos DB I/O and can
        # genuinely fail (RBAC misconfiguration, outage, malformed stored
        # records) — kept inside this same try block as the
        # agent call so any such failure still produces the standard JSON
        # error envelope instead of propagating as an unhandled exception.
        equipment_match = resolve_equipment(parsed.equipment_id, parsed.request_text)
        matched_record, grounding_block = build_grounding(equipment_match)
        history_equipment_id = matched_record["equipment_id"] if matched_record else None
        history_block, has_saved_history = build_history_block(history_equipment_id)
        combined_grounding = f"{grounding_block}\n\n{history_block}"

        # Fetched before the agent call, not at the scrubbing step below: with Cosmos
        # this is a real query that can fail, and failing here costs no agent call.
        known_wo_ids = get_known_work_order_ids(history_equipment_id) if matched_record else set()

        assessment = get_assessment(parsed.request_text, combined_grounding)
    except AppError as exc:
        logger.error("assess failed: %s", exc, exc_info=True)
        return _error_response(exc.status_code, exc.code, exc.user_message)
    except Exception:
        logger.exception("Unhandled error in /api/assess")
        return _error_response(500, "internal_error", "An unexpected error occurred.")

    assessment.equipment_id = matched_record["equipment_id"] if matched_record else None
    assessment.customer = matched_record["customer"] if matched_record else None

    # Defense in depth beyond the agent's own "never invent a work order ID"
    # instruction: strip any related_work_orders entry that isn't actually a
    # real work order for the matched equipment, using the same ground-truth
    # data used for grounding. A schema-valid hallucinated ID would otherwise
    # reach the UI and become selectable when saving the assessment.
    assessment.related_work_orders = [wo for wo in assessment.related_work_orders if wo in known_wo_ids]

    # Same defense-in-depth posture as related_work_orders above: don't trust the
    # agent's own grounding_used/comparison_notes claims when we deterministically
    # know no grounding/history was actually supplied. A schema-valid response could
    # otherwise claim grounding_used=true with null equipment/customer, or fabricate
    # a comparison_notes claim ("matches a previously saved assessment") when no
    # saved history block was ever sent — both would reach the UI as if true.
    if not matched_record:
        assessment.grounding_used = False
    if not has_saved_history:
        assessment.comparison_notes = None

    assessment.recommendations = [_strip_leading_bullet(r) for r in assessment.recommendations]

    response = AssessmentResponse(
        **assessment.model_dump(),
        match_status=equipment_match.status,
        equipment_candidates=[to_equipment_summary(r) for r in equipment_match.candidates],
    )

    return func.HttpResponse(
        body=response.model_dump_json(),
        status_code=200,
        mimetype="application/json",
    )


@app.route(route="equipment", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
def equipment(req: func.HttpRequest) -> func.HttpResponse:
    if req.method != "GET":
        return _error_response(405, "method_not_allowed", "Only GET is supported.")

    try:
        summaries = list_equipment_summaries()
    except AppError as exc:
        logger.error("list equipment failed: %s", exc, exc_info=True)
        return _error_response(exc.status_code, exc.code, exc.user_message)
    except Exception:
        logger.exception("Unhandled error in GET /api/equipment")
        return _error_response(500, "internal_error", "An unexpected error occurred.")

    body = json.dumps([s.model_dump() for s in summaries])
    return func.HttpResponse(body=body, status_code=200, mimetype="application/json")


@app.route(route="assessments", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
def assessments(req: func.HttpRequest) -> func.HttpResponse:
    if req.method == "GET":
        return _list_assessments(req)
    if req.method == "POST":
        return _save_assessment(req)
    return _error_response(405, "method_not_allowed", "Only GET and POST are supported.")


def _list_assessments(req: func.HttpRequest) -> func.HttpResponse:
    equipment_id = req.params.get("equipment_id")

    try:
        if not equipment_id:
            raise InvalidRequestError("equipment_id query parameter is required.")
        records = list_saved_assessments(equipment_id)
    except AppError as exc:
        logger.error("list assessments failed: %s", exc, exc_info=True)
        return _error_response(exc.status_code, exc.code, exc.user_message)
    except Exception:
        logger.exception("Unhandled error in GET /api/assessments")
        return _error_response(500, "internal_error", "An unexpected error occurred.")

    body = json.dumps([r.model_dump() for r in records])
    return func.HttpResponse(body=body, status_code=200, mimetype="application/json")


def _save_assessment(req: func.HttpRequest) -> func.HttpResponse:
    try:
        body = req.get_json()
    except ValueError:
        return _error_response(400, "invalid_json", "Request body must be valid JSON.")

    try:
        parsed = SaveAssessmentRequest.model_validate(body)
    except ValidationError as exc:
        return _error_response(400, "invalid_request", str(exc))

    try:
        equipment_id = parsed.assessment.equipment_id
        if equipment_id is None:
            raise InvalidRequestError("Cannot save an assessment with no matched equipment_id.")

        # Don't trust the client-supplied assessment payload at face value —
        # verify equipment_id and every claimed related_work_order against the
        # same authoritative data used for grounding before persisting
        # anything. Without this, a request carrying a fabricated equipment_id
        # or an invented work order ID would pass Pydantic validation (which
        # only checks internal consistency with the client's own claims) and
        # get written to storage as if it were real.
        if not equipment_exists(equipment_id):
            raise InvalidRequestError(f"Unknown equipment_id '{equipment_id}'.")

        known_wo_ids = get_known_work_order_ids(equipment_id)
        invented_wos = [wo for wo in parsed.assessment.related_work_orders if wo not in known_wo_ids]
        if invented_wos:
            raise InvalidRequestError(
                f"assessment.related_work_orders contains unknown work order id(s) for "
                f"equipment '{equipment_id}': {invented_wos}"
            )

        record = save_assessment(
            equipment_id=equipment_id,
            assessment=parsed.assessment,
            saved_by=parsed.saved_by,
            associated_work_order_id=parsed.associated_work_order_id,
        )
    except AppError as exc:
        logger.error("save_assessment failed: %s", exc, exc_info=True)
        return _error_response(exc.status_code, exc.code, exc.user_message)
    except Exception:
        logger.exception("Unhandled error in POST /api/assessments")
        return _error_response(500, "internal_error", "An unexpected error occurred.")

    return func.HttpResponse(body=record.model_dump_json(), status_code=201, mimetype="application/json")
