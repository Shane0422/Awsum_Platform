from fastapi import APIRouter

router = APIRouter(prefix="/admin/store", tags=["admin_store"])

# 매장 목록 조회
@router.get("/")
def list_stores():
    return [
        {"store_id": 1001, "name": "Kim's Market"},
        {"store_id": 1002, "name": "Arirang"}
    ]

# 매장 신규 등록
@router.post("/")
def create_store(name: str):
    return {"message": f"Store '{name}' created successfully"}
