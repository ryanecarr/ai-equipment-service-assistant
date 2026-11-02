import os
import re
from functools import partial
from typing import Any, Optional

from azure.cosmos import ContainerProxy
from azure.cosmos.exceptions import CosmosResourceNotFoundError

from app.cosmos import get_database, log_request_charge, translate_cosmos_errors, validated_record
from app.schemas.grounding_models import EquipmentRecord, WorkOrderRecord

# ORDER BY source_order reproduces the source JSON's array order, which is the order
# the grounding block lists work orders in. It is not a date sort, and there is
# deliberately no TOP/limit: the prompt has always carried a machine's full history.
_WORK_ORDERS_QUERY = "SELECT * FROM c WHERE c.equipment_id = @equipment_id ORDER BY c.source_order ASC"
_ALL_EQUIPMENT_QUERY = "SELECT * FROM c"

# Added by scripts/migrate_grounding_to_cosmos.py, not part of the source records.
# Removing them (and Cosmos's own _rid/_etag/_ts/...) hands the context builder
# exactly the dicts it received from the source JSON.
_EQUIPMENT_STORAGE_FIELDS = frozenset({"id"})
_WORK_ORDER_STORAGE_FIELDS = frozenset({"id", "equipment_id", "source_order"})

# azure-cosmos formats read_item's id straight into the request URL without escaping
# it, and equipment_id comes from the HTTP request — "../x" or "a/b" would otherwise
# address a different resource rather than simply not matching. The migration enforces
# the same pattern, so anything rejected here cannot exist in the container.
_DOCUMENT_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,254}")

_repository: Optional["EquipmentRepository"] = None


def is_valid_document_id(value: str) -> bool:
    return _DOCUMENT_ID_RE.fullmatch(value) is not None


class EquipmentRepository:
    def __init__(self, equipment_container: ContainerProxy, work_orders_container: ContainerProxy):
        self._equipment = equipment_container
        self._work_orders = work_orders_container

    def get_equipment(self, equipment_id: str) -> Optional[dict[str, Any]]:
        if not is_valid_document_id(equipment_id):
            return None

        with translate_cosmos_errors("equipment read"):
            try:
                document = self._equipment.read_item(
                    item=equipment_id,
                    partition_key=equipment_id,
                    response_hook=partial(log_request_charge, "equipment read"),
                )
            except CosmosResourceNotFoundError as exc:
                # A plain missing item is 404 with sub-status 0. Any other sub-status
                # (e.g. 1002 session unavailable, 1003 owner resource missing) is a
                # service problem, and calling it "not found" would send the agent an
                # ungrounded request as if this equipment simply had no records.
                if exc.sub_status:
                    raise
                return None

        return validated_record(document, EquipmentRecord, _EQUIPMENT_STORAGE_FIELDS)

    def get_work_orders(self, equipment_id: str) -> list[dict[str, Any]]:
        # list() inside the error translation: query_items only pages lazily, so
        # failures surface during iteration rather than at the call itself.
        with translate_cosmos_errors("work order query"):
            documents = list(
                self._work_orders.query_items(
                    query=_WORK_ORDERS_QUERY,
                    parameters=[{"name": "@equipment_id", "value": equipment_id}],
                    partition_key=equipment_id,
                    response_hook=partial(log_request_charge, "work order query"),
                )
            )
        return [validated_record(d, WorkOrderRecord, _WORK_ORDER_STORAGE_FIELDS) for d in documents]

    def list_equipment(self) -> list[dict[str, Any]]:
        # Used by free-text matching, the UI's equipment list and the migration script's
        # verify step. It has no partition to target: matching has to score every record's
        # model and customer against the request text before it knows which equipment is meant.
        with translate_cosmos_errors("equipment scan"):
            documents = list(
                self._equipment.query_items(
                    query=_ALL_EQUIPMENT_QUERY,
                    enable_cross_partition_query=True,
                    response_hook=partial(log_request_charge, "equipment scan"),
                )
            )
        return [validated_record(d, EquipmentRecord, _EQUIPMENT_STORAGE_FIELDS) for d in documents]


def get_equipment_repository() -> EquipmentRepository:
    global _repository
    if _repository is None:
        database = get_database()
        with translate_cosmos_errors("container check"):
            equipment = database.get_container_client(os.environ.get("COSMOS_EQUIPMENT_CONTAINER", "equipment"))
            work_orders = database.get_container_client(os.environ.get("COSMOS_WORK_ORDERS_CONTAINER", "work-orders"))
            # read_item resolves container metadata first, and a missing container is
            # also a 404 — confirm both exist up front so a misconfigured name fails
            # here as a service error instead of reading as "equipment not found".
            equipment.read()
            work_orders.read()
        _repository = EquipmentRepository(equipment, work_orders)
    return _repository
