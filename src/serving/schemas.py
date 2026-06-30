from pydantic import BaseModel


class RecommendOrderRequest(BaseModel):
    yyyymm: str
    item_code: str
    sido: str | None = None
    current_stock: float
    lead_time_days: int = 0

