from typing import Optional, List
from datetime import date
from pydantic import BaseModel, Field


class SubscriptionBase(BaseModel):
    i_account_id: int
    i_store_id: int
    c_plan_code: str
    n_monthly_fee: float
    dt_start_date: date
    dt_end_date: Optional[date] = None
    i_device_limit: int = 5
    c_status: str = "active"
    c_billing_cycle: str = "monthly"
    c_memo: Optional[str] = None


class SubscriptionCreate(SubscriptionBase):
    pass


class SubscriptionUpdate(BaseModel):
    c_plan_code: Optional[str] = None
    n_monthly_fee: Optional[float] = None
    dt_start_date: Optional[date] = None
    dt_end_date: Optional[date] = None
    i_device_limit: Optional[int] = None
    c_status: Optional[str] = None
    c_billing_cycle: Optional[str] = None
    c_renewal_status: Optional[str] = None
    dt_next_billing: Optional[date] = None
    c_memo: Optional[str] = None


class SubscriptionDetail(SubscriptionBase):
    i_subscription_id: int
    c_renewal_status: Optional[str]
    dt_next_billing: Optional[date]
    dt_created: Optional[str]
    dt_updated: Optional[str]

    class Config:
        from_attributes = True


class SubscriptionList(BaseModel):
    i_subscription_id: int
    i_account_id: int
    i_store_id: int
    c_plan_code: str
    n_monthly_fee: float
    dt_start_date: date
    dt_end_date: Optional[date]
    i_device_limit: int
    c_status: str
    c_billing_cycle: str
    c_renewal_status: Optional[str]

    class Config:
        from_attributes = True
