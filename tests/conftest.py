import pytest
from fastapi.testclient import TestClient

from bridge.api import create_app
from bridge.order_manager import OrderManager
from tests.fakes import FakeBrokerClient

TEST_API_KEY = "test-api-key-12345"


@pytest.fixture
def fake_broker():
    return FakeBrokerClient()


@pytest.fixture
def order_manager(fake_broker):
    # dry_run=False here because the whole point of these tests is to
    # verify orders reach the (fake) broker; dry-run behaviour is covered
    # separately in test_order_manager.py.
    return OrderManager(broker=fake_broker, dry_run=False)


@pytest.fixture
def app(order_manager):
    return create_app(order_manager=order_manager, api_key=TEST_API_KEY, mode="paper")


@pytest.fixture
def client(app):
    return TestClient(app)


@pytest.fixture
def auth_headers():
    return {"X-API-Key": TEST_API_KEY}
