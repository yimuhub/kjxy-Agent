"""组织架构接口出入参（v3.57）。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

UnitKind = Literal["company", "coop", "department", "team"]
UnitStatus = Literal["active", "archived"]


class OrgUnitCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: str = Field(min_length=1, max_length=80)
    parent_id: str | None = Field(default=None, max_length=36)
    code: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9_-]{1,59}$")
    kind: UnitKind = "department"
    description: str | None = Field(default=None, max_length=2000)
    sort_order: int = Field(default=0, ge=0, le=9999)


class OrgUnitUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: str | None = Field(default=None, min_length=1, max_length=80)
    parent_id: str | None = Field(default=None, max_length=36)
    code: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9_-]{1,59}$")
    kind: UnitKind | None = None
    description: str | None = Field(default=None, max_length=2000)
    sort_order: int | None = Field(default=None, ge=0, le=9999)
    status: UnitStatus | None = None


class OrgPositionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    org_unit_id: str = Field(min_length=1, max_length=36)
    title: str = Field(min_length=1, max_length=80)
    responsibilities: str | None = Field(default=None, max_length=4000)
    headcount: int = Field(default=1, ge=1, le=999)
    agent_id: str | None = Field(default=None, max_length=36)
    sort_order: int = Field(default=0, ge=0, le=9999)


class OrgPositionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    org_unit_id: str | None = Field(default=None, min_length=1, max_length=36)
    title: str | None = Field(default=None, min_length=1, max_length=80)
    responsibilities: str | None = Field(default=None, max_length=4000)
    headcount: int | None = Field(default=None, ge=1, le=999)
    agent_id: str | None = Field(default=None, max_length=36)
    sort_order: int | None = Field(default=None, ge=0, le=9999)
    status: UnitStatus | None = None


class OrgProposalCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    instruction: str = Field(default="", max_length=2000)


class OrgProposalApply(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    #: 把提案挂到已有组织单元下；不传则作为顶层
    parent_id: str | None = Field(default=None, max_length=36)


__all__ = [
    "OrgPositionCreate",
    "OrgPositionUpdate",
    "OrgProposalApply",
    "OrgProposalCreate",
    "OrgUnitCreate",
    "OrgUnitUpdate",
    "UnitKind",
    "UnitStatus",
]
