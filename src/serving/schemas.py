from typing import Any, Literal

from pydantic import BaseModel, Field


Role = Literal["INST", "CENTRAL", "SYS"]
ImportReprocessScope = Literal["VALIDATE_ONLY", "FROM_MAPPING", "FULL"]
MappingDecisionStatus = Literal["HUMAN_APPROVED", "REJECTED"]
MappingDecisionScope = Literal["GLOBAL", "INSTITUTION_ONLY"]


class LoginRequest(BaseModel):
    loginId: str
    password: str


class RefreshRequest(BaseModel):
    refreshToken: str


class UserCreateRequest(BaseModel):
    loginId: str
    role: Role
    institutionId: str | None = None
    status: str = "ACTIVE"


class UserUpdateRequest(BaseModel):
    role: Role | None = None
    institutionId: str | None = None
    status: str | None = None


class ImportReprocessRequest(BaseModel):
    scope: ImportReprocessScope


class MappingDecisionRequest(BaseModel):
    rawItemId: str
    standardItemId: str
    status: MappingDecisionStatus
    scope: MappingDecisionScope = "GLOBAL"
    memo: str | None = None


class DictionaryRequest(BaseModel):
    rawTerm: str
    normalizedTerm: str
    memo: str | None = None


class ForecastRunRequest(BaseModel):
    scope: str = "ALL"
    fromMonth: str | None = None
    toMonth: str | None = None


class InventoryPolicyRunRequest(BaseModel):
    scope: str = "ALL"
    riskTriggered: bool = False


class MaterialDependencyUpdateRequest(BaseModel):
    materialType: str
    dependencyWeight: float = Field(ge=0, le=1)
    rationale: str | None = None


class RelocationDecisionRequest(BaseModel):
    status: Literal["APPROVED", "REJECTED"]
    memo: str | None = None


class AlertResolveRequest(BaseModel):
    memo: str | None = None


class AlertSettingsRequest(BaseModel):
    cooldownHours: int = Field(ge=1, le=168)
    thresholds: dict[str, Any] = Field(default_factory=dict)


class RecommendOrderRequest(BaseModel):
    yyyymm: str
    item_code: str
    sido: str | None = None
    current_stock: float
    lead_time_days: int = 0

