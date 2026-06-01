from typing import Optional, List
from datetime import date, datetime
from pydantic import BaseModel, Field


class SubscriptionBase(BaseModel):
    account_id: int = Field(..., alias="i_account_id")
    store_id: int = Field(..., alias="i_store_id")
    plan_id: Optional[int] = Field(None, alias="i_plan_id")
    plan_code: str = Field(..., alias="c_plan_code")
    plan_name: Optional[str] = Field(None, alias="c_plan_name")
    store_base_fee: Optional[float] = Field(None, alias="n_store_base_fee")
    included_pos_count: Optional[int] = Field(None, alias="i_included_pos_count")
    pos_fee: Optional[float] = Field(None, alias="n_pos_fee")
    included_kiosk_count: Optional[int] = Field(None, alias="i_included_kiosk_count")
    kiosk_fee: Optional[float] = Field(None, alias="n_kiosk_fee")
    included_mobile_order_count: Optional[int] = Field(None, alias="i_included_mobile_order_count")
    mobile_order_fee: Optional[float] = Field(None, alias="n_mobile_order_fee")
    included_user_count: Optional[int] = Field(None, alias="i_included_user_count")
    extra_user_fee: Optional[float] = Field(None, alias="n_extra_user_fee")
    setup_fee: Optional[float] = Field(None, alias="n_setup_fee")
    contract_term_month: Optional[int] = Field(None, alias="i_contract_term_month")
    transaction_fee_rate: Optional[float] = Field(None, alias="n_transaction_fee_rate")
    extra_device_fee: Optional[float] = Field(None, alias="n_extra_device_fee")
    monthly_fee: float = Field(..., alias="n_monthly_fee")
    currency: Optional[str] = Field(None, alias="c_currency")
    start_date: date = Field(..., alias="dt_start_date")
    end_date: Optional[date] = Field(None, alias="dt_end_date")
    device_limit: int = Field(5, alias="i_device_limit")
    status: str = Field("active", alias="c_status")
    billing_cycle: str = Field("monthly", alias="c_billing_cycle")
    memo: Optional[str] = Field(None, alias="c_memo")


class SubscriptionCreate(BaseModel):
    account_id: int = Field(..., alias="i_account_id")
    store_id: int = Field(..., alias="i_store_id")
    plan_id: Optional[int] = Field(None, alias="i_plan_id")
    plan_code: Optional[str] = Field(None, alias="c_plan_code")
    monthly_fee: Optional[float] = Field(None, alias="n_monthly_fee")
    start_date: date = Field(..., alias="dt_start_date")
    end_date: Optional[date] = Field(None, alias="dt_end_date")
    device_limit: int = Field(5, alias="i_device_limit")
    status: str = Field("active", alias="c_status")
    billing_cycle: str = Field("monthly", alias="c_billing_cycle")
    memo: Optional[str] = Field(None, alias="c_memo")

    class Config:
        populate_by_name = True  # Accept both alias and field name


class SubscriptionUpdate(BaseModel):
    plan_id: Optional[int] = Field(None, alias="i_plan_id")
    plan_code: Optional[str] = Field(None, alias="c_plan_code")
    monthly_fee: Optional[float] = Field(None, alias="n_monthly_fee")
    start_date: Optional[date] = Field(None, alias="dt_start_date")
    end_date: Optional[date] = Field(None, alias="dt_end_date")
    device_limit: Optional[int] = Field(None, alias="i_device_limit")
    status: Optional[str] = Field(None, alias="c_status")
    billing_cycle: Optional[str] = Field(None, alias="c_billing_cycle")
    renewal_status: Optional[str] = Field(None, alias="c_renewal_status")
    next_billing: Optional[date] = Field(None, alias="dt_next_billing")
    memo: Optional[str] = Field(None, alias="c_memo")

    class Config:
        populate_by_name = True


class SubscriptionDetail(SubscriptionBase):
    subscription_id: int = Field(..., alias="i_subscription_id")
    renewal_status: Optional[str] = Field(None, alias="c_renewal_status")
    next_billing: Optional[date] = Field(None, alias="dt_next_billing")
    created_at: Optional[datetime] = Field(None, alias="dt_created")
    updated_at: Optional[datetime] = Field(None, alias="dt_updated")

    class Config:
        populate_by_name = True
        from_attributes = True


class SubscriptionList(BaseModel):
    subscription_id: int = Field(..., alias="i_subscription_id")
    account_id: int = Field(..., alias="i_account_id")
    store_id: int = Field(..., alias="i_store_id")
    plan_code: str = Field(..., alias="c_plan_code")
    plan_name: Optional[str] = Field(None, alias="c_plan_name")
    store_base_fee: Optional[float] = Field(None, alias="n_store_base_fee")
    included_pos_count: Optional[int] = Field(None, alias="i_included_pos_count")
    pos_fee: Optional[float] = Field(None, alias="n_pos_fee")
    included_kiosk_count: Optional[int] = Field(None, alias="i_included_kiosk_count")
    kiosk_fee: Optional[float] = Field(None, alias="n_kiosk_fee")
    included_mobile_order_count: Optional[int] = Field(None, alias="i_included_mobile_order_count")
    mobile_order_fee: Optional[float] = Field(None, alias="n_mobile_order_fee")
    included_user_count: Optional[int] = Field(None, alias="i_included_user_count")
    extra_user_fee: Optional[float] = Field(None, alias="n_extra_user_fee")
    setup_fee: Optional[float] = Field(None, alias="n_setup_fee")
    contract_term_month: Optional[int] = Field(None, alias="i_contract_term_month")
    transaction_fee_rate: Optional[float] = Field(None, alias="n_transaction_fee_rate")
    monthly_fee: float = Field(..., alias="n_monthly_fee")
    currency: Optional[str] = Field(None, alias="c_currency")
    start_date: date = Field(..., alias="dt_start_date")
    end_date: Optional[date] = Field(None, alias="dt_end_date")
    device_limit: int = Field(..., alias="i_device_limit")
    status: str = Field(..., alias="c_status")
    billing_cycle: str = Field(..., alias="c_billing_cycle")
    renewal_status: Optional[str] = Field(None, alias="c_renewal_status")

    class Config:
        populate_by_name = True
        from_attributes = True
