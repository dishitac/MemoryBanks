"""Atlas checks are opt-in and use only a uniquely named disposable database."""

from uuid import uuid4

import pytest


def pytest_addoption(parser):
    parser.addoption("--atlas", action="store_true", help="Run live Atlas checkpoint tests")


@pytest.fixture(scope="module")
def atlas_db(request):
    if not request.config.getoption("--atlas"):
        pytest.skip("Pass --atlas to run against a disposable Atlas test database")
    from memorybank.db import get_client
    from memorybank.schema import setup_database

    client = get_client()
    name = "memorybank_checkpoint_test_" + uuid4().hex
    db = client[name]
    try:
        setup_database(db)
        yield db
    finally:
        # Never drop the configured application database.
        assert name.startswith("memorybank_checkpoint_test_")
        client.drop_database(name)
