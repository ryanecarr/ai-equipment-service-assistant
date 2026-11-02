"""In-memory stand-in for azure.cosmos.ContainerProxy, shared by the Cosmos test suites.

Enforces the parts of the real SDK's contract these tests depend on: read_item needs
the right partition key, queries must be scoped to a partition or explicitly marked
cross-partition, query results page lazily, and documents come back carrying Cosmos's
system properties.
"""

from typing import Any, Iterator, Optional

from azure.cosmos.exceptions import CosmosHttpResponseError, CosmosResourceExistsError, CosmosResourceNotFoundError

from app.grounding.repository import EquipmentRepository
from scripts.migrate_grounding_to_cosmos import build_documents

COSMOS_SYSTEM_PROPERTIES = {
    "_rid": "ZXhhbXBsZQ==",
    "_self": "dbs/x/colls/y/docs/z/",
    "_etag": '"00000000-0000-0000-0000-000000000000"',
    "_attachments": "attachments/",
    "_ts": 1793400000,
}


class FakeContainer:
    def __init__(self, id: str = "fake", partition_key_path: str = "/equipment_id", documents: tuple = ()):
        self.id = id
        self.partition_key_path = partition_key_path
        self._documents: dict[tuple[str, str], dict[str, Any]] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.read_item_error: Optional[Exception] = None
        self.query_error: Optional[Exception] = None
        self.upsert_error: Optional[Exception] = None
        self.create_error: Optional[Exception] = None
        for document in documents:
            self._store(document)

    def _store(self, document: dict[str, Any]) -> None:
        partition_key = document[self.partition_key_path.lstrip("/")]
        self._documents[(partition_key, document["id"])] = {**document, **COSMOS_SYSTEM_PROPERTIES}

    @property
    def document_count(self) -> int:
        return len(self._documents)

    def call_names(self) -> list[str]:
        return [name for name, _ in self.calls]

    def read(self) -> dict[str, Any]:
        self.calls.append(("read", {}))
        return {"id": self.id, "partitionKey": {"paths": [self.partition_key_path], "kind": "Hash"}}

    def read_item(self, item: str, partition_key: str, response_hook=None, **kwargs) -> dict[str, Any]:
        self.calls.append(("read_item", {"item": item, "partition_key": partition_key, **kwargs}))
        if self.read_item_error is not None:
            raise self.read_item_error
        document = self._documents.get((partition_key, item))
        if document is None:
            raise CosmosResourceNotFoundError(status_code=404, message="Entity with the specified id does not exist")
        if response_hook is not None:
            response_hook({"x-ms-request-charge": "1.0"}, document)
        return dict(document)

    def create_item(self, body: dict[str, Any], response_hook=None, **kwargs) -> dict[str, Any]:
        self.calls.append(("create_item", {"body": body, **kwargs}))
        if self.create_error is not None:
            raise self.create_error
        if (body[self.partition_key_path.lstrip("/")], body["id"]) in self._documents:
            raise CosmosResourceExistsError(status_code=409, message="Entity with the specified id already exists")
        self._store(body)
        if response_hook is not None:
            response_hook({"x-ms-request-charge": "9.0"}, body)
        return body

    def upsert_item(self, body: dict[str, Any], response_hook=None, **kwargs) -> dict[str, Any]:
        self.calls.append(("upsert_item", {"body": body, **kwargs}))
        if self.upsert_error is not None:
            raise self.upsert_error
        self._store(body)
        if response_hook is not None:
            response_hook({"x-ms-request-charge": "6.0"}, body)
        return body

    def query_items(
        self,
        query: str,
        parameters: Optional[list[dict[str, Any]]] = None,
        partition_key: Optional[str] = None,
        enable_cross_partition_query: Optional[bool] = None,
        response_hook=None,
        **kwargs,
    ) -> Iterator[Any]:
        self.calls.append(
            (
                "query_items",
                {
                    "query": query,
                    "parameters": parameters,
                    "partition_key": partition_key,
                    "enable_cross_partition_query": enable_cross_partition_query,
                },
            )
        )
        if partition_key is None and not enable_cross_partition_query:
            raise CosmosHttpResponseError(status_code=400, message="Cross partition query is required")
        return self._page(query, parameters or [], partition_key, response_hook)

    def _page(self, query, parameters, partition_key, response_hook) -> Iterator[Any]:
        # A generator, like the SDK's ItemPaged: nothing runs — and no error surfaces —
        # until the caller iterates.
        if self.query_error is not None:
            raise self.query_error

        documents = [dict(d) for (pk, _), d in self._documents.items() if partition_key in (None, pk)]
        limit = None
        for parameter in parameters:
            if parameter["name"] == "@limit":
                limit = parameter["value"]
                continue
            field = parameter["name"].lstrip("@")
            documents = [d for d in documents if d.get(field) == parameter["value"]]
        if "COUNT(1)" in query:
            documents = [len(documents)]
        elif "ORDER BY c.source_order" in query:
            documents.sort(key=lambda d: d["source_order"])
        elif "ORDER BY c.saved_at DESC" in query:
            documents.sort(key=lambda d: d["saved_at"], reverse=True)
        documents = documents[:limit]
        if response_hook is not None:
            response_hook({"x-ms-request-charge": "2.9"}, documents)
        yield from documents


def repository_for(equipment_master: dict[str, Any], work_orders: Optional[dict[str, Any]] = None) -> EquipmentRepository:
    """A repository over fake containers holding these source-format records, written
    through the migration's own transforms."""
    plan = build_documents(equipment_master, work_orders or {})
    assert plan.failures == []
    return EquipmentRepository(
        FakeContainer("equipment", documents=tuple(plan.equipment)),
        FakeContainer("work-orders", documents=tuple(plan.work_orders)),
    )
