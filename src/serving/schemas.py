from pydantic import BaseModel, Field


class ForecastRunRequest(BaseModel):
    scope: str = "ALL"
    fromMonth: str | None = None
    toMonth: str | None = None


class RecommendOrderRequest(BaseModel):
    yyyymm: str
    item_code: str
    institution_code: str
    department: str | None = None
    current_stock: float = Field(ge=0)
    lead_time_days: int = Field(default=0, ge=0, le=365)
    review_period_days: int = Field(default=30, ge=1, le=365)
    on_order_qty: float = Field(default=0, ge=0)
    backorder_qty: float = Field(default=0, ge=0)


class BatchJobResponse(BaseModel):
    jobId: str
    status: str
    message: str
