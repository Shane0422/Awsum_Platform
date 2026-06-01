from datetime import date, timedelta
from decimal import Decimal
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_
from sqlalchemy.orm import Session

from backend.database.pg_platform import PlatformSessionLocal
from backend.models_admin.subscription import Subscription
from backend.models_admin.account import Client
from backend.models_admin.store import Store
from backend.models_admin.pricing_plan import PricingPlan
from backend.models_admin.invoice import Invoice, InvoiceLine
from backend.models_admin.contract import Contract
from backend.models_admin.device import Device
from backend.models_admin.device_type import DeviceType
from backend.models_admin.device_category import DeviceCategory
from backend.schemas.subscription import (
    SubscriptionCreate,
    SubscriptionUpdate,
    SubscriptionDetail,
    SubscriptionList,
)

router = APIRouter(prefix="/subscriptions", tags=["subscriptions"])


def get_db():
    db = PlatformSessionLocal()
    try:
        yield db
    finally:
        db.close()


def _money(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(Decimal("0.01"))


def _decimal(value) -> Decimal:
    return Decimal(str(value or 0))


def _int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _resolve_subscription_device_usage(db: Session, store_id: int) -> tuple[dict[str, int], int, int, int, int]:
    """
    Subscription billing용 실제 장비 사용량을 tb_device 기준으로 계산.
    - POS/KIOSK/MOBILE 카테고리는 포함수량/초과수량 계산 대상
    - 그 외 billable 카테고리는 extra_device_fee 대상으로 집계
    """
    category_counts = _count_billable_devices_by_category(db, store_id)
    usage_pos = max(0, _int(category_counts.get("POS", 0)))
    usage_kiosk = max(0, _int(category_counts.get("KIOSK", 0)))
    usage_mobile = max(0, _int(category_counts.get("MOBILE", 0)))
    usage_other = max(0, sum(cnt for code, cnt in category_counts.items() if code not in {"POS", "KIOSK", "MOBILE"}))
    return category_counts, usage_pos, usage_kiosk, usage_mobile, usage_other


def _billing_preview(db: Session, subscription: Subscription, *, user_count: Optional[int] = None, transaction_amount: Optional[Decimal] = None, include_setup: bool = False) -> dict:
    category_counts, usage_pos, usage_kiosk, usage_mobile, usage_other = _resolve_subscription_device_usage(db, subscription.i_store_id)
    usage_user = max(0, _int(user_count) if user_count is not None else _int(subscription.i_included_user_count))

    included_pos = max(0, _int(subscription.i_included_pos_count))
    included_kiosk = max(0, _int(subscription.i_included_kiosk_count))
    included_mobile = max(0, _int(subscription.i_included_mobile_order_count))
    included_user = max(0, _int(subscription.i_included_user_count))
    included_other = 0

    pos_over = max(0, usage_pos - included_pos)
    kiosk_over = max(0, usage_kiosk - included_kiosk)
    mobile_over = max(0, usage_mobile - included_mobile)
    user_over = max(0, usage_user - included_user)
    other_over = max(0, usage_other - included_other)

    monthly_base = _money(subscription.n_monthly_fee)
    pos_overage_fee = _money(_decimal(subscription.n_pos_fee) * pos_over)
    kiosk_overage_fee = _money(_decimal(subscription.n_kiosk_fee) * kiosk_over)
    mobile_overage_fee = _money(_decimal(subscription.n_mobile_order_fee) * mobile_over)
    user_overage_fee = _money(_decimal(subscription.n_extra_user_fee) * user_over)
    extra_device_overage_fee = _money(_decimal(subscription.n_extra_device_fee) * other_over)
    setup_fee = _money(subscription.n_setup_fee) if include_setup else Decimal("0.00")

    tx_amount = _decimal(transaction_amount or 0)
    tx_rate_percent = _decimal(subscription.n_transaction_fee_rate)
    transaction_fee = _money((tx_amount * tx_rate_percent) / Decimal("100"))

    total = _money(monthly_base + pos_overage_fee + kiosk_overage_fee + mobile_overage_fee + extra_device_overage_fee + user_overage_fee + setup_fee + transaction_fee)

    return {
        "currency": subscription.c_currency or "USD",
        "usage": {
            "pos_count": usage_pos,
            "kiosk_count": usage_kiosk,
            "mobile_order_count": usage_mobile,
            "other_device_count": usage_other,
            "user_count": usage_user,
            "transaction_amount": float(tx_amount),
            "device_category_counts": category_counts,
        },
        "included": {
            "pos_count": included_pos,
            "kiosk_count": included_kiosk,
            "mobile_order_count": included_mobile,
            "other_device_count": included_other,
            "user_count": included_user,
        },
        "overage": {
            "pos_count": pos_over,
            "kiosk_count": kiosk_over,
            "mobile_order_count": mobile_over,
            "other_device_count": other_over,
            "user_count": user_over,
        },
        "fees": {
            "monthly_base": float(monthly_base),
            "pos_overage": float(pos_overage_fee),
            "kiosk_overage": float(kiosk_overage_fee),
            "mobile_overage": float(mobile_overage_fee),
            "extra_device": float(extra_device_overage_fee),
            "user_overage": float(user_overage_fee),
            "setup": float(setup_fee),
            "transaction": float(transaction_fee),
            "total": float(total),
        },
    }


# ------------------------------------------------------------------
# Device-based billing summary (업종 공통 Device Type 마스터 연동)
# ------------------------------------------------------------------

# Mapping: device category_code → PricingPlan field name
_CATEGORY_TO_PLAN_FIELD = {
    "POS":    ("i_included_pos_count",          "n_pos_fee"),
    "KIOSK":  ("i_included_kiosk_count",         "n_kiosk_fee"),
    "MOBILE": ("i_included_mobile_order_count",  "n_mobile_order_fee"),
}


def _count_billable_devices_by_category(db: Session, store_id: int) -> dict[str, int]:
    """
    Store에 등록된 활성 + billable 장비를 category_code별로 집계.
    i_device_type_id 가 없는 장비는 집계에서 제외.
    반환값: { "POS": 2, "KIOSK": 1, ... }
    """
    rows = (
        db.query(DeviceCategory.c_category_code, DeviceType.c_billable_yn)
        .join(DeviceType, DeviceType.i_device_category_id == DeviceCategory.i_device_category_id)
        .join(Device, Device.i_device_type_id == DeviceType.i_device_type_id)
        .filter(
            Device.i_store_id == store_id,
            Device.c_status == "active",
            DeviceType.c_billable_yn == "yes",
            DeviceType.c_status == "active",
            DeviceCategory.c_status == "active",
        )
        .all()
    )
    counts: dict[str, int] = {}
    for cat_code, _ in rows:
        counts[cat_code] = counts.get(cat_code, 0) + 1
    return counts


def _device_billing_preview(plan: PricingPlan, category_counts: dict[str, int]) -> dict:
    """
    Category 집계 수량과 Pricing Plan included 수량을 비교해 초과 과금 계산.
      POS → included_pos_count / pos_fee
      KIOSK → included_kiosk_count / kiosk_fee
      MOBILE → included_mobile_order_count / mobile_order_fee
      기타 billable → extra_device_fee (per device)
    """
    total_known_categories = set(_CATEGORY_TO_PLAN_FIELD.keys())
    store_base = _money(plan.n_store_base_fee)

    breakdown: dict[str, dict] = {}
    overage_total = Decimal("0.00")

    for cat_code, (inc_field, fee_field) in _CATEGORY_TO_PLAN_FIELD.items():
        actual = category_counts.get(cat_code, 0)
        included = _int(getattr(plan, inc_field, 0))
        fee_per = _decimal(getattr(plan, fee_field, 0))
        over = max(0, actual - included)
        fee = _money(fee_per * over)
        overage_total += fee
        breakdown[cat_code.lower()] = {
            "actual": actual,
            "included": included,
            "overage": over,
            "fee_per_unit": float(fee_per),
            "overage_fee": float(fee),
        }

    # Other billable categories → extra_device_fee each
    other_total = 0
    for cat_code, cnt in category_counts.items():
        if cat_code not in total_known_categories:
            other_total += cnt
    extra_device_fee_per = _decimal(plan.n_extra_device_fee)
    extra_fee = _money(extra_device_fee_per * other_total)
    overage_total += extra_fee
    breakdown["other_billable"] = {
        "actual": other_total,
        "included": 0,
        "overage": other_total,
        "fee_per_unit": float(extra_device_fee_per),
        "overage_fee": float(extra_fee),
    }

    subtotal = _money(store_base + overage_total)
    return {
        "store_base_fee": float(store_base),
        "device_overage_total": float(overage_total),
        "subtotal": float(subtotal),
        "breakdown": breakdown,
    }


def _generate_invoice_no(db: Session) -> str:
    today = date.today()
    prefix = f"INV-{today.strftime('%Y%m%d')}-"
    latest = (
        db.query(Invoice)
        .filter(Invoice.c_invoice_no.like(f"{prefix}%"))
        .order_by(Invoice.i_invoice_id.desc())
        .first()
    )
    if latest and latest.c_invoice_no:
        try:
            seq = int(str(latest.c_invoice_no).split("-")[-1]) + 1
        except ValueError:
            seq = 1
    else:
        seq = 1
    return f"{prefix}{seq:04d}"


def _invoice_dict(invoice: Invoice, lines: list[InvoiceLine]) -> dict:
    return {
        "invoice_id": invoice.i_invoice_id,
        "invoice_no": invoice.c_invoice_no,
        "subscription_id": invoice.i_subscription_id,
        "account_id": invoice.i_account_id,
        "store_id": invoice.i_store_id,
        "invoice_date": str(invoice.dt_invoice_date) if invoice.dt_invoice_date else None,
        "due_date": str(invoice.dt_due_date) if invoice.dt_due_date else None,
        "currency": invoice.c_currency,
        "subtotal": float(invoice.n_subtotal or 0),
        "tax": float(invoice.n_tax or 0),
        "total": float(invoice.n_total or 0),
        "status": invoice.c_status,
        "memo": invoice.c_memo,
        "lines": [
            {
                "line_id": line.i_invoice_line_id,
                "line_type": line.c_line_type,
                "description": line.c_description,
                "quantity": float(line.n_quantity or 0),
                "unit_price": float(line.n_unit_price or 0),
                "amount": float(line.n_amount or 0),
                "currency": line.c_currency,
            }
            for line in lines
        ],
    }


def _add_months(base_date: date, months: int) -> date:
    month_index = (base_date.month - 1) + months
    year = base_date.year + (month_index // 12)
    month = (month_index % 12) + 1
    month_days = [31, 29 if (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    day = min(base_date.day, month_days[month - 1])
    return date(year, month, day)


def _parse_store_tax_rate(store: Optional[Store]) -> Decimal:
    if not store:
        return Decimal("0")
    raw = str(getattr(store, "c_default_tax_rate", "") or "").strip()
    if not raw:
        return Decimal("0")
    try:
        value = Decimal(raw)
    except Exception:
        return Decimal("0")
    # Accept both percent-form (8.875) and ratio-form (0.08875)
    if value <= Decimal("1"):
        value = value * Decimal("100")
    if value < 0:
        return Decimal("0")
    return value


def _create_contract_for_subscription(db: Session, subscription: Subscription) -> Contract:
    store = db.query(Store).filter(Store.i_store_id == subscription.i_store_id).first()
    tax_rate = _parse_store_tax_rate(store)

    preview = _billing_preview(db, subscription)

    monthly_base_fee = _money(subscription.n_store_base_fee)
    monthly_device_fee = _money(
        _decimal(preview["fees"]["pos_overage"])
        + _decimal(preview["fees"]["kiosk_overage"])
        + _decimal(preview["fees"]["mobile_overage"])
        + _decimal(preview["fees"]["extra_device"])
    )
    monthly_user_fee = _money(_decimal(preview["fees"]["user_overage"]))

    monthly_total_fee = _money(monthly_base_fee + monthly_device_fee + monthly_user_fee)
    tax_amount = _money(monthly_total_fee * tax_rate / Decimal("100"))
    total_monthly_fee = _money(monthly_total_fee + tax_amount)

    term_month = max(1, _int(subscription.i_contract_term_month or 1))
    start_date = subscription.dt_start_date
    end_date = subscription.dt_end_date or _add_months(start_date, term_month)

    contract = Contract(
        i_subscription_id=subscription.i_subscription_id,
        i_account_id=subscription.i_account_id,
        i_store_id=subscription.i_store_id,
        i_pricing_plan_id=subscription.i_plan_id,
        dt_contract_start_date=start_date,
        dt_contract_end_date=end_date,
        i_contract_term_month=term_month,
        n_setup_fee=_money(subscription.n_setup_fee),
        n_monthly_base_fee=monthly_base_fee,
        n_monthly_device_fee=monthly_device_fee,
        n_monthly_user_fee=monthly_user_fee,
        n_monthly_total_fee=monthly_total_fee,
        n_tax_rate=_decimal(tax_rate),
        n_tax_amount=tax_amount,
        n_total_monthly_fee=total_monthly_fee,
        c_status="active",
        c_contract_pdf_path=None,
    )
    db.add(contract)
    db.flush()
    return contract


def backfill_missing_subscription_contracts(db: Session) -> int:
    subscriptions = (
        db.query(Subscription)
        .filter(Subscription.c_status == "active")
        .order_by(Subscription.i_subscription_id.asc())
        .all()
    )

    created = 0
    for subscription in subscriptions:
        existing = (
            db.query(Contract.i_contract_id)
            .filter(Contract.i_subscription_id == subscription.i_subscription_id)
            .first()
        )
        if existing:
            continue
        _create_contract_for_subscription(db, subscription)
        created += 1

    return created


def _issue_invoice_for_subscription(db: Session, subscription: Subscription, payload: Optional[dict] = None, *, invoice_date: Optional[date] = None, include_setup_default: bool = False) -> tuple[Invoice, list[InvoiceLine]]:
    payload = payload or {}

    active_contract = (
        db.query(Contract)
        .filter(
            Contract.i_subscription_id == subscription.i_subscription_id,
            Contract.c_status == "active",
        )
        .order_by(Contract.i_contract_id.desc())
        .first()
    )

    if active_contract:
        subtotal = _money(active_contract.n_monthly_total_fee)
        tax_amount = _money(active_contract.n_tax_amount)
        total = _money(active_contract.n_total_monthly_fee)

        effective_invoice_date = invoice_date or date.today()
        due_in_days = max(0, int(payload.get("due_in_days") or 0))
        due_date = effective_invoice_date + timedelta(days=due_in_days) if due_in_days else None

        invoice = Invoice(
            i_subscription_id=subscription.i_subscription_id,
            i_account_id=subscription.i_account_id,
            i_store_id=subscription.i_store_id,
            c_invoice_no=_generate_invoice_no(db),
            dt_invoice_date=effective_invoice_date,
            dt_due_date=due_date,
            c_currency=subscription.c_currency or "USD",
            n_subtotal=subtotal,
            n_tax=tax_amount,
            n_total=total,
            c_status=str(payload.get("status") or "issued").strip().lower() or "issued",
            c_memo=payload.get("memo"),
            i_created_by=1,
        )
        db.add(invoice)
        db.flush()

        line_defs = [
            ("monthly_base", "Monthly Base Fee", Decimal("1"), _money(active_contract.n_monthly_base_fee)),
            ("monthly_device", "Monthly Device Fee", Decimal("1"), _money(active_contract.n_monthly_device_fee)),
            ("monthly_user", "Monthly User Fee", Decimal("1"), _money(active_contract.n_monthly_user_fee)),
        ]
        for line_type, description, quantity, unit_price in line_defs:
            if unit_price <= 0:
                continue
            amount = _money(quantity * unit_price)
            db.add(
                InvoiceLine(
                    i_invoice_id=invoice.i_invoice_id,
                    c_line_type=line_type,
                    c_description=description,
                    n_quantity=quantity,
                    n_unit_price=unit_price,
                    n_amount=amount,
                    c_currency=invoice.c_currency,
                    i_created_by=1,
                )
            )

        db.flush()
        lines = db.query(InvoiceLine).filter(InvoiceLine.i_invoice_id == invoice.i_invoice_id).order_by(InvoiceLine.i_invoice_line_id).all()
        return invoice, lines

    has_previous_invoice = db.query(Invoice.i_invoice_id).filter(Invoice.i_subscription_id == subscription.i_subscription_id).first() is not None
    include_setup = bool(payload.get("include_setup", include_setup_default and not has_previous_invoice))

    preview = _billing_preview(
        db,
        subscription,
        user_count=payload.get("user_count"),
        transaction_amount=_decimal(payload.get("transaction_amount")),
        include_setup=include_setup,
    )

    tax_rate = max(Decimal("0"), _decimal(payload.get("tax_rate")))
    subtotal = _money(preview["fees"]["total"])
    tax_amount = _money(subtotal * tax_rate / Decimal("100"))
    total = _money(subtotal + tax_amount)

    effective_invoice_date = invoice_date or date.today()
    due_in_days = max(0, int(payload.get("due_in_days") or 0))
    due_date = effective_invoice_date + timedelta(days=due_in_days) if due_in_days else None

    invoice = Invoice(
        i_subscription_id=subscription.i_subscription_id,
        i_account_id=subscription.i_account_id,
        i_store_id=subscription.i_store_id,
        c_invoice_no=_generate_invoice_no(db),
        dt_invoice_date=effective_invoice_date,
        dt_due_date=due_date,
        c_currency=subscription.c_currency or "USD",
        n_subtotal=subtotal,
        n_tax=tax_amount,
        n_total=total,
        c_status=str(payload.get("status") or "issued").strip().lower() or "issued",
        c_memo=payload.get("memo"),
        i_created_by=1,
    )
    db.add(invoice)
    db.flush()

    line_defs = [
        ("monthly_base", "Monthly Base Fee", Decimal("1"), _money(preview["fees"]["monthly_base"])),
        ("pos_overage", "POS Overage", Decimal(str(preview["overage"]["pos_count"])), _money(subscription.n_pos_fee)),
        ("kiosk_overage", "Kiosk Overage", Decimal(str(preview["overage"]["kiosk_count"])), _money(subscription.n_kiosk_fee)),
        ("mobile_overage", "Mobile Overage", Decimal(str(preview["overage"]["mobile_order_count"])), _money(subscription.n_mobile_order_fee)),
        ("extra_device", "Extra Device Overage", Decimal(str(preview["overage"]["other_device_count"])), _money(subscription.n_extra_device_fee)),
        ("user_overage", "User Overage", Decimal(str(preview["overage"]["user_count"])), _money(subscription.n_extra_user_fee)),
        ("setup", "Setup Fee", Decimal("1") if include_setup else Decimal("0"), _money(subscription.n_setup_fee)),
        ("transaction", "Transaction Fee", Decimal("1"), _money(preview["fees"]["transaction"])),
    ]

    for line_type, description, quantity, unit_price in line_defs:
        if quantity <= 0 or unit_price <= 0:
            continue
        amount = _money(quantity * unit_price)
        db.add(
            InvoiceLine(
                i_invoice_id=invoice.i_invoice_id,
                c_line_type=line_type,
                c_description=description,
                n_quantity=quantity,
                n_unit_price=unit_price,
                n_amount=amount,
                c_currency=invoice.c_currency,
                i_created_by=1,
            )
        )

    db.flush()
    lines = db.query(InvoiceLine).filter(InvoiceLine.i_invoice_id == invoice.i_invoice_id).order_by(InvoiceLine.i_invoice_line_id).all()
    return invoice, lines


def _as_dict(model) -> dict:
    if hasattr(model, "model_dump"):
        return model.model_dump(exclude_unset=True)
    return model.dict(exclude_unset=True)


def _resolve_plan(db: Session, plan_id: Optional[int], plan_code: Optional[str]) -> PricingPlan:
    plan = None
    if plan_id:
        plan = db.query(PricingPlan).filter(PricingPlan.i_plan_id == int(plan_id)).first()
    elif plan_code:
        plan = db.query(PricingPlan).filter(PricingPlan.c_plan_code == str(plan_code)).first()

    if not plan:
        raise HTTPException(status_code=404, detail="Pricing plan not found")
    return plan


def _apply_plan_snapshot(subscription: Subscription, plan: PricingPlan) -> None:
    subscription.i_plan_id = plan.i_plan_id
    subscription.c_plan_code = plan.c_plan_code
    subscription.c_plan_name = plan.c_plan_name
    subscription.n_store_base_fee = _money(plan.n_store_base_fee)
    subscription.i_included_pos_count = _int(plan.i_included_pos_count)
    subscription.n_pos_fee = _money(plan.n_pos_fee)
    subscription.i_included_kiosk_count = _int(plan.i_included_kiosk_count)
    subscription.n_kiosk_fee = _money(plan.n_kiosk_fee)
    subscription.i_included_mobile_order_count = _int(plan.i_included_mobile_order_count)
    subscription.n_mobile_order_fee = _money(plan.n_mobile_order_fee)
    subscription.i_included_user_count = _int(plan.i_included_user_count)
    subscription.n_extra_user_fee = _money(plan.n_extra_user_fee)
    subscription.n_setup_fee = _money(plan.n_setup_fee)
    subscription.i_contract_term_month = max(1, _int(plan.i_contract_term_month))
    subscription.n_transaction_fee_rate = _decimal(plan.n_transaction_fee_rate)
    subscription.n_extra_device_fee = _money(plan.n_extra_device_fee)
    subscription.c_currency = plan.c_currency


@router.get("")
async def list_subscriptions(
    account_id: Optional[int] = Query(None),
    store_id: Optional[int] = Query(None),
    status: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """List all subscriptions with account/store/plan names joined"""
    query = (
        db.query(Subscription, Client.c_client_name, Store.c_store_name, PricingPlan.c_plan_name)
        .outerjoin(Client, Subscription.i_account_id == Client.i_account_id)
        .outerjoin(Store, Subscription.i_store_id == Store.i_store_id)
        .outerjoin(PricingPlan, Subscription.i_plan_id == PricingPlan.i_plan_id)
    )

    if account_id:
        query = query.filter(Subscription.i_account_id == account_id)
    if store_id:
        query = query.filter(Subscription.i_store_id == store_id)
    if status:
        query = query.filter(Subscription.c_status == status)

    rows = query.order_by(Subscription.dt_start_date.desc()).all()

    subscription_ids = [sub.i_subscription_id for sub, _, _, _ in rows]
    latest_contract_by_subscription: dict[int, Contract] = {}
    if subscription_ids:
        contract_rows = (
            db.query(Contract)
            .filter(Contract.i_subscription_id.in_(subscription_ids))
            .order_by(Contract.i_subscription_id.asc(), Contract.i_contract_id.desc())
            .all()
        )
        for contract in contract_rows:
            latest_contract_by_subscription.setdefault(contract.i_subscription_id, contract)

    result = []
    for sub, account_name, store_name, plan_name in rows:
        latest_contract = latest_contract_by_subscription.get(sub.i_subscription_id)
        result.append({
            "id": sub.i_subscription_id,
            "subscription_id": sub.i_subscription_id,
            "account_id": sub.i_account_id,
            "account_name": account_name or "",
            "store_id": sub.i_store_id,
            "store_name": store_name or "",
            "plan_id": sub.i_plan_id,
            "plan_code": sub.c_plan_code or "",
            "plan_name": plan_name or sub.c_plan_name or "",
            "store_base_fee": float(sub.n_store_base_fee) if sub.n_store_base_fee is not None else 0.0,
            "included_pos_count": int(sub.i_included_pos_count or 0),
            "pos_fee": float(sub.n_pos_fee) if sub.n_pos_fee is not None else 0.0,
            "included_kiosk_count": int(sub.i_included_kiosk_count or 0),
            "kiosk_fee": float(sub.n_kiosk_fee) if sub.n_kiosk_fee is not None else 0.0,
            "included_mobile_order_count": int(sub.i_included_mobile_order_count or 0),
            "mobile_order_fee": float(sub.n_mobile_order_fee) if sub.n_mobile_order_fee is not None else 0.0,
            "included_user_count": int(sub.i_included_user_count or 0),
            "extra_user_fee": float(sub.n_extra_user_fee) if sub.n_extra_user_fee is not None else 0.0,
            "setup_fee": float(sub.n_setup_fee) if sub.n_setup_fee is not None else 0.0,
            "contract_term_month": int(sub.i_contract_term_month or 1),
            "transaction_fee_rate": float(sub.n_transaction_fee_rate) if sub.n_transaction_fee_rate is not None else 0.0,
            "monthly_fee": float(sub.n_monthly_fee) if sub.n_monthly_fee is not None else 0.0,
            "contract_id": latest_contract.i_contract_id if latest_contract else None,
            "contract_monthly_total_fee": float(latest_contract.n_total_monthly_fee) if latest_contract and latest_contract.n_total_monthly_fee is not None else None,
            "contract_status": latest_contract.c_status if latest_contract else "",
            "currency": sub.c_currency or "USD",
            "start_date": str(sub.dt_start_date) if sub.dt_start_date else None,
            "end_date": str(sub.dt_end_date) if sub.dt_end_date else None,
            "device_limit": sub.i_device_limit,
            "status": sub.c_status or "",
            "billing_cycle": sub.c_billing_cycle or "",
            "renewal_status": sub.c_renewal_status,
            "memo": sub.c_memo,
            "billing_preview": _billing_preview(db, sub),
        })
    return result


@router.get("/{subscription_id}/contract-summary")
async def get_subscription_contract_summary(
    subscription_id: int,
    db: Session = Depends(get_db),
):
    subscription = db.query(Subscription).filter(Subscription.i_subscription_id == subscription_id).first()
    if not subscription:
        raise HTTPException(status_code=404, detail="Subscription not found")

    contract = (
        db.query(Contract)
        .filter(Contract.i_subscription_id == subscription_id)
        .order_by(Contract.i_contract_id.desc())
        .first()
    )

    if not contract:
        return {
            "subscription_id": subscription_id,
            "contract_id": None,
            "contract_status": "",
            "contract_monthly_total_fee": None,
            "contract_start_date": None,
            "contract_end_date": None,
        }

    return {
        "subscription_id": subscription_id,
        "contract_id": contract.i_contract_id,
        "contract_status": contract.c_status or "",
        "contract_monthly_total_fee": float(contract.n_total_monthly_fee) if contract.n_total_monthly_fee is not None else None,
        "contract_start_date": str(contract.dt_contract_start_date) if contract.dt_contract_start_date else None,
        "contract_end_date": str(contract.dt_contract_end_date) if contract.dt_contract_end_date else None,
    }


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
    # Validate required fields
    if not data.account_id or not data.store_id:
        raise HTTPException(status_code=400, detail="Account and Store are required")

    if not data.plan_id and not data.plan_code:
        raise HTTPException(status_code=400, detail="Pricing plan is required")
    
    # Verify account exists
    account = db.query(Client).filter(Client.i_account_id == data.account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail=f"Account {data.account_id} not found")

    # Verify store exists and belongs to account
    store = db.query(Store).filter(
        and_(
            Store.i_store_id == data.store_id,
            Store.i_account_id == data.account_id,
        )
    ).first()
    if not store:
        raise HTTPException(status_code=404, detail=f"Store {data.store_id} not found for account {data.account_id}")

    plan = _resolve_plan(db, data.plan_id, data.plan_code)

    # Create subscription with mapped field names
    try:
        contract_monthly_fee = data.monthly_fee if data.monthly_fee is not None else plan.n_store_base_fee
        subscription = Subscription(
            i_account_id=data.account_id,
            i_store_id=data.store_id,
            n_monthly_fee=_money(contract_monthly_fee),
            dt_start_date=data.start_date,
            dt_end_date=data.end_date,
            i_device_limit=int(data.device_limit),
            c_status=data.status,
            c_billing_cycle=data.billing_cycle,
            c_memo=data.memo or None,
            i_created_by=1,  # TODO: Get from session
        )
        _apply_plan_snapshot(subscription, plan)
        db.add(subscription)
        db.flush()
        _create_contract_for_subscription(db, subscription)
        db.commit()
        db.refresh(subscription)
        return subscription
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to create subscription: {str(e)}")


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
    update_data = _as_dict(data)

    if "plan_id" in update_data or "plan_code" in update_data:
        plan = _resolve_plan(db, update_data.get("plan_id"), update_data.get("plan_code"))
        _apply_plan_snapshot(subscription, plan)

    field_map = {
        "monthly_fee": "n_monthly_fee",
        "start_date": "dt_start_date",
        "end_date": "dt_end_date",
        "device_limit": "i_device_limit",
        "status": "c_status",
        "billing_cycle": "c_billing_cycle",
        "renewal_status": "c_renewal_status",
        "next_billing": "dt_next_billing",
        "memo": "c_memo",
    }

    for field, column_name in field_map.items():
        if field not in update_data:
            continue
        value = update_data[field]
        if field == "monthly_fee" and value is not None:
            value = _money(value)
        setattr(subscription, column_name, value)

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


@router.get("/{subscription_id}/billing-preview")
async def get_subscription_billing_preview(
    subscription_id: int,
    user_count: Optional[int] = Query(None),
    transaction_amount: Optional[float] = Query(None),
    include_setup: bool = Query(False),
    db: Session = Depends(get_db),
):
    subscription = db.query(Subscription).filter(Subscription.i_subscription_id == subscription_id).first()
    if not subscription:
        raise HTTPException(status_code=404, detail="Subscription not found")

    preview = _billing_preview(
        db,
        subscription,
        user_count=user_count,
        transaction_amount=_decimal(transaction_amount),
        include_setup=include_setup,
    )
    return {
        "subscription_id": subscription.i_subscription_id,
        "plan_code": subscription.c_plan_code,
        "plan_name": subscription.c_plan_name,
        "contract_term_month": subscription.i_contract_term_month,
        "transaction_fee_rate": float(subscription.n_transaction_fee_rate or 0),
        "preview": preview,
    }


@router.get("/store/{store_id}/device-billing-summary")
async def get_store_device_billing_summary(
    store_id: int,
    plan_id: Optional[int] = Query(None, description="Pricing Plan ID to use for overage calculation"),
    db: Session = Depends(get_db),
):
    """
    Store에 등록된 실제 활성 장비를 기준으로 Billing Summary 계산.
    - billable_yn = yes 인 장비만 대상
    - plan_id 를 주면 해당 Plan의 included 수량과 비교하여 초과 과금 계산
    """
    store = db.query(Store).filter(Store.i_store_id == store_id).first()
    if not store:
        raise HTTPException(status_code=404, detail="Store not found")

    # 실제 장비 집계
    category_counts = _count_billable_devices_by_category(db, store_id)

    # 전체 billable 장비 상세 목록
    device_rows = (
        db.query(
            Device.i_device_id,
            Device.c_device_name,
            Device.c_serial_no,
            Device.c_status,
            DeviceType.c_device_type_code,
            DeviceType.c_device_type_name,
            DeviceType.c_billable_yn,
            DeviceCategory.c_category_code,
            DeviceCategory.c_category_name,
        )
        .outerjoin(DeviceType, Device.i_device_type_id == DeviceType.i_device_type_id)
        .outerjoin(DeviceCategory, DeviceType.i_device_category_id == DeviceCategory.i_device_category_id)
        .filter(Device.i_store_id == store_id, Device.c_status == "active")
        .order_by(DeviceCategory.i_sort_order, DeviceType.i_sort_order, Device.i_device_id)
        .all()
    )

    devices_detail = [
        {
            "device_id": row[0],
            "device_name": row[1] or "",
            "serial_no": row[2] or "",
            "status": row[3] or "",
            "device_type_code": row[4] or "",
            "device_type_name": row[5] or "",
            "billable_yn": row[6] or "no",
            "category_code": row[7] or "",
            "category_name": row[8] or "",
        }
        for row in device_rows
    ]

    result = {
        "store_id": store_id,
        "category_counts": category_counts,
        "total_billable": sum(category_counts.values()),
        "devices": devices_detail,
        "plan_preview": None,
    }

    # Plan 기반 overage 계산 (선택)
    if plan_id:
        plan = db.query(PricingPlan).filter(PricingPlan.i_plan_id == plan_id, PricingPlan.c_status == "active").first()
        if plan:
            result["plan_preview"] = _device_billing_preview(plan, category_counts)
            result["plan_preview"]["plan_id"] = plan_id
            result["plan_preview"]["plan_code"] = plan.c_plan_code
            result["plan_preview"]["plan_name"] = plan.c_plan_name

    return result

@router.post("/{subscription_id}/invoices")
async def issue_subscription_invoice(
    subscription_id: int,
    payload: Optional[dict] = None,
    db: Session = Depends(get_db),
):
    subscription = db.query(Subscription).filter(Subscription.i_subscription_id == subscription_id).first()
    if not subscription:
        raise HTTPException(status_code=404, detail="Subscription not found")
    invoice, lines = _issue_invoice_for_subscription(db, subscription, payload)
    db.commit()
    return _invoice_dict(invoice, lines)


@router.post("/invoices/batch")
async def batch_issue_monthly_invoices(
    payload: Optional[dict] = None,
    db: Session = Depends(get_db),
):
    payload = payload or {}
    billing_date_raw = str(payload.get("billing_date") or "").strip()
    if billing_date_raw:
        try:
            billing_date = date.fromisoformat(billing_date_raw)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid billing_date. Use YYYY-MM-DD")
    else:
        billing_date = date.today()

    include_setup_for_first_invoice = bool(payload.get("include_setup_for_first_invoice", False))
    skip_existing_month = bool(payload.get("skip_existing_month", True))
    only_due = bool(payload.get("only_due", True))

    subscriptions = db.query(Subscription).filter(Subscription.c_status == "active").all()

    month_start = date(billing_date.year, billing_date.month, 1)
    month_end = _add_months(month_start, 1)

    issued = []
    skipped = []
    failed = []

    for subscription in subscriptions:
        try:
            if only_due and subscription.dt_next_billing and subscription.dt_next_billing > billing_date:
                skipped.append({"subscription_id": subscription.i_subscription_id, "reason": "not_due"})
                continue

            if skip_existing_month:
                exists = (
                    db.query(Invoice.i_invoice_id)
                    .filter(
                        Invoice.i_subscription_id == subscription.i_subscription_id,
                        Invoice.dt_invoice_date >= month_start,
                        Invoice.dt_invoice_date < month_end,
                    )
                    .first()
                )
                if exists:
                    skipped.append({"subscription_id": subscription.i_subscription_id, "reason": "already_issued_for_month"})
                    continue

            invoice, _ = _issue_invoice_for_subscription(
                db,
                subscription,
                payload,
                invoice_date=billing_date,
                include_setup_default=include_setup_for_first_invoice,
            )

            subscription.dt_next_billing = _add_months(billing_date, 1)
            subscription.i_updated_by = 1

            issued.append({
                "subscription_id": subscription.i_subscription_id,
                "invoice_id": invoice.i_invoice_id,
                "invoice_no": invoice.c_invoice_no,
                "total": float(invoice.n_total or 0),
            })
        except Exception as exc:
            failed.append({"subscription_id": subscription.i_subscription_id, "error": str(exc)})

    db.commit()
    return {
        "billing_date": str(billing_date),
        "issued_count": len(issued),
        "skipped_count": len(skipped),
        "failed_count": len(failed),
        "issued": issued,
        "skipped": skipped,
        "failed": failed,
    }


@router.get("/{subscription_id}/invoices")
async def list_subscription_invoices(
    subscription_id: int,
    db: Session = Depends(get_db),
):
    invoices = (
        db.query(Invoice)
        .filter(Invoice.i_subscription_id == subscription_id)
        .order_by(Invoice.dt_invoice_date.desc(), Invoice.i_invoice_id.desc())
        .all()
    )
    return [
        {
            "invoice_id": inv.i_invoice_id,
            "invoice_no": inv.c_invoice_no,
            "invoice_date": str(inv.dt_invoice_date) if inv.dt_invoice_date else None,
            "due_date": str(inv.dt_due_date) if inv.dt_due_date else None,
            "currency": inv.c_currency,
            "subtotal": float(inv.n_subtotal or 0),
            "tax": float(inv.n_tax or 0),
            "total": float(inv.n_total or 0),
            "status": inv.c_status,
        }
        for inv in invoices
    ]


@router.get("/invoices/{invoice_id}")
async def get_invoice_detail(
    invoice_id: int,
    db: Session = Depends(get_db),
):
    invoice = db.query(Invoice).filter(Invoice.i_invoice_id == invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    lines = db.query(InvoiceLine).filter(InvoiceLine.i_invoice_id == invoice.i_invoice_id).order_by(InvoiceLine.i_invoice_line_id).all()
    return _invoice_dict(invoice, lines)

