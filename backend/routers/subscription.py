from datetime import date
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import and_
from sqlalchemy.orm import Session
from jose import jwt, JWTError

from backend.database.pg_platform import PlatformSessionLocal
from backend.models_admin.subscription import Subscription
from backend.models_admin.account import Client
from backend.models_admin.store import Store
from backend.schemas.subscription import (
    SubscriptionCreate,
    SubscriptionUpdate,
    SubscriptionDetail,
    SubscriptionList,
)
from backend.utils.jwt_handler import SECRET_KEY, ALGORITHM

router = APIRouter(prefix="/platform/subscriptions", tags=["subscriptions"])


def get_db():
    db = PlatformSessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("", response_model=List[SubscriptionList])
async def list_subscriptions(
    account_id: Optional[int] = Query(None),
    store_id: Optional[int] = Query(None),
    status: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """List all subscriptions with optional filters"""
    query = db.query(Subscription)

    if account_id:
        query = query.filter(Subscription.i_account_id == account_id)
    if store_id:
        query = query.filter(Subscription.i_store_id == store_id)
    if status:
        query = query.filter(Subscription.c_status == status)

    subscriptions = query.order_by(Subscription.dt_start_date.desc()).all()
    return subscriptions


@router.get("/{subscription_id}", response_model=SubscriptionDetail)
async def get_subscription(
    subscription_id: int,
    db: Session = Depends(get_db),
):
    """Get subscription details by ID"""
    subscription = db.query(Subscription).filter(
        Subscription.i_subscription_id == subscription_id
    ).first()

    if not subscription:
        raise HTTPException(status_code=404, detail="Subscription not found")

    return subscription


@router.post("", response_model=SubscriptionDetail)
async def create_subscription(
    data: SubscriptionCreate,
    db: Session = Depends(get_db),
):
    """Create a new subscription"""
    # Verify account exists
    account = db.query(Client).filter(Client.i_account_id == data.i_account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    # Verify store exists and belongs to account
    store = db.query(Store).filter(
        and_(
            Store.i_store_id == data.i_store_id,
            Store.i_account_id == data.i_account_id,
        )
    ).first()
    if not store:
        raise HTTPException(status_code=404, detail="Store not found for this account")

    # Create subscription
    subscription = Subscription(
        i_account_id=data.i_account_id,
        i_store_id=data.i_store_id,
        c_plan_code=data.c_plan_code,
        n_monthly_fee=data.n_monthly_fee,
        dt_start_date=data.dt_start_date,
        dt_end_date=data.dt_end_date,
        i_device_limit=data.i_device_limit,
        c_status=data.c_status,
        c_billing_cycle=data.c_billing_cycle,
        c_memo=data.c_memo,
        i_created_by=1,  # TODO: Get from session
    )
    db.add(subscription)
    db.commit()
    db.refresh(subscription)
    return subscription


@router.put("/{subscription_id}", response_model=SubscriptionDetail)
async def update_subscription(
    subscription_id: int,
    data: SubscriptionUpdate,
    db: Session = Depends(get_db),
):
    """Update subscription details"""
    subscription = db.query(Subscription).filter(
        Subscription.i_subscription_id == subscription_id
    ).first()

    if not subscription:
        raise HTTPException(status_code=404, detail="Subscription not found")

    # Update fields
    update_data = data.dict(exclude_unset=True)
    for field, value in update_data.items():
        setattr(subscription, field, value)

    subscription.i_updated_by = 1  # TODO: Get from session
    db.commit()
    db.refresh(subscription)
    return subscription


@router.delete("/{subscription_id}")
async def delete_subscription(
    subscription_id: int,
    db: Session = Depends(get_db),
):
    """Delete subscription (soft delete by marking as cancelled)"""
    subscription = db.query(Subscription).filter(
        Subscription.i_subscription_id == subscription_id
    ).first()

    if not subscription:
        raise HTTPException(status_code=404, detail="Subscription not found")

    # Soft delete: mark as cancelled
    subscription.c_status = "cancelled"
    subscription.i_updated_by = 1  # TODO: Get from session
    db.commit()

    return {"message": "Subscription cancelled"}


@router.get("/account/{account_id}/subscriptions", response_model=List[SubscriptionList])
async def get_account_subscriptions(
    account_id: int,
    db: Session = Depends(get_db),
):
    """Get all subscriptions for an account"""
    subscriptions = db.query(Subscription).filter(
        Subscription.i_account_id == account_id
    ).order_by(Subscription.dt_start_date.desc()).all()

    return subscriptions


@router.get("/store/{store_id}/subscriptions", response_model=List[SubscriptionList])
async def get_store_subscriptions(
    store_id: int,
    db: Session = Depends(get_db),
):
    """Get subscription for a specific store"""
    subscription = db.query(Subscription).filter(
        Subscription.i_store_id == store_id
    ).order_by(Subscription.dt_start_date.desc()).first()

    if subscription:
        return [subscription]
    return []
