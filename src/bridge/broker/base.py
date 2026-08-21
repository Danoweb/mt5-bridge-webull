"""
Abstract broker interface.

The order manager and API layer only ever talk to this interface, never to
the `webull` SDK directly. Two reasons this matters in practice:

1. Testability -- the real Webull client makes network calls to Webull's
   OpenAPI. Unit tests substitute a fake implementation of this interface
   so the test suite is fast, hermetic, and doesn't depend on having real
   Webull credentials.
2. Future-proofing -- Webull's SDK is still young and its response
   formats aren't fully pinned down (see webull_client.py's module
   docstring). If it changes shape, or if you want to swap in a different
   broker entirely, only broker/webull_client.py (or a new sibling module)
   needs to change -- order_manager.py and api.py do not.
"""
from abc import ABC, abstractmethod
from typing import List, Optional

from bridge.models import AccountInfo, OrderRequest, OrderResponse, Position


class BrokerClient(ABC):
    @abstractmethod
    def is_connected(self) -> bool:
        """Whether the client currently holds a usable, logged-in session."""

    @abstractmethod
    def get_account(self) -> AccountInfo:
        ...

    @abstractmethod
    def get_positions(self) -> List[Position]:
        ...

    @abstractmethod
    def get_position(self, symbol: str) -> Optional[Position]:
        ...

    @abstractmethod
    def place_order(self, order: OrderRequest) -> OrderResponse:
        ...

    @abstractmethod
    def get_order(self, order_id: str) -> Optional[OrderResponse]:
        ...

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        ...
