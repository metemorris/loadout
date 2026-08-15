"""HTTP route groups for the LoadOut API."""

from .inventory import router as inventory_router
from .packing import router as packing_router
from .trips import router as trip_router

__all__ = ["inventory_router", "packing_router", "trip_router"]
