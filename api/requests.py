"""Validated request bodies accepted by the LoadOut HTTP API."""

from typing import List, Optional

from pydantic import BaseModel, Field


class MovementRequest(BaseModel):
    """Preview or confirm a physical move between exact locations."""

    item_ids: List[str] = Field(min_length=1)
    source: str
    destination: str
    reason: Optional[str] = None
    update_preferred: bool = False
    confirmed: bool = False


class PackingActionRequest(BaseModel):
    """Apply one pack, swap, or remove decision to a packing plan."""

    plan_id: str
    section: str
    entry_index: int = Field(ge=1)
    action: str
    replacement_item_id: Optional[str] = None
    notes: Optional[str] = None
    confirmed: bool = False


class PackingDecisionRequest(BaseModel):
    """Identify one entry in one of a plan's nine sections."""

    section: str
    entry_index: int = Field(ge=1)


class PackingBatchRequest(BaseModel):
    """Confirm multiple physical packing decisions in one request."""

    plan_id: str
    decisions: List[PackingDecisionRequest] = Field(min_length=1)
    confirmed: bool = False


class PackingPlanItemRequest(BaseModel):
    """Add an available physical item to a draft or revised plan."""

    plan_id: str
    item_id: str
    container: str
    reason: Optional[str] = None
    confirmed: bool = False


class PackingContainerRequest(BaseModel):
    """Assign a plan entry to a different trip container."""

    plan_id: str
    section: str
    entry_index: int = Field(ge=1)
    container: str
    confirmed: bool = False


class PackingUnpackRequest(BaseModel):
    """Return a packed item to its recorded pre-pack source."""

    plan_id: str
    section: str
    entry_index: int = Field(ge=1)
    confirmed: bool = False
