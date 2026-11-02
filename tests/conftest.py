import pytest

import app.cosmos as cosmos_mod
import app.grounding.repository as repository_mod
import app.history.repository as history_repository_mod
from cosmos_fakes import FakeContainer, repository_for


@pytest.fixture(autouse=True)
def reset_cosmos_singletons():
    yield
    cosmos_mod._database = None
    repository_mod._repository = None
    history_repository_mod._repository = None


@pytest.fixture
def seed_grounding():
    def seed(equipment_master, work_orders=None):
        repository_mod._repository = repository_for(equipment_master, work_orders)
        return repository_mod._repository

    return seed


@pytest.fixture
def saved_assessments():
    container = FakeContainer("saved-assessments")
    history_repository_mod._repository = history_repository_mod.SavedAssessmentRepository(container)
    return container
