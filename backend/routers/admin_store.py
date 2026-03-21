from fastapi import APIRouter, HTTPException
from sqlalchemy.orm import Session
import bcrypt

from backend.database.pg_platform import PlatformSessionLocal, get_next_customer_store_id
from backend.models_admin.store import Store
from backend.models_admin.business_type import BusinessType

# bcrypt has a hard limit of 72 bytes for passwords
MAX_BCRYPT_PASSWORD_BYTES = 72

router = APIRouter(prefix="/admin/store", tags=["admin_store"])

# 매장 목록 조회
@router.get("/")
def list_stores():
    db: Session = PlatformSessionLocal()
    try:
        stores = db.query(Store).all()
        return [{"store_id": s.i_store_id, "name": s.c_store_name, "status": s.c_status} for s in stores]
    finally:
        db.close()

# 매장 신규 등록
@router.post("/")
def create_store(
    name: str,
    store_pw: str,
    business_code: str = "PLATFORM",
    operation_type: str = "Default Operation",
    channel_type: str = "Admin",
    device_type: str = "Server",
):
    db: Session = PlatformSessionLocal()
    try:
        business_type = db.query(BusinessType).filter_by(c_business_code=business_code).first()
        if not business_type:
            raise HTTPException(status_code=400, detail="Invalid business type code")

        next_id = get_next_customer_store_id(db)

        if len(store_pw.encode("utf-8")) > MAX_BCRYPT_PASSWORD_BYTES:
            raise HTTPException(
                status_code=400,
                detail=f"Store password too long (max {MAX_BCRYPT_PASSWORD_BYTES} bytes).",
            )

        hashed_pw = bcrypt.hashpw(store_pw.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

        store = Store(
            i_store_id=next_id,
            c_store_name=name,
            c_store_pw=hashed_pw,
            i_business_type=business_type.i_business_type_id,
            c_operation_type=operation_type,
            c_channel_type=channel_type,
            c_device_type=device_type,
            c_status="active",
        )
        db.add(store)
        db.commit()
        return {"store_id": next_id, "name": name}
    finally:
        db.close()
