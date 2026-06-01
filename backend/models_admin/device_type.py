from sqlalchemy import Column, Integer, String, Numeric, ForeignKey

from backend.database.pg_platform import PlatformBase
from backend.models_common.audit_mixin import AuditMixin


class DeviceType(PlatformBase, AuditMixin):
    """
    tb_device_type — 장비 유형 마스터 (업종 공통)
    모든 업종(식당, 세탁소, 스파, 리테일, 턱시도 렌탈 등)에서 재사용 가능한 공통 구조.
    업종별 차이는 DeviceType 자체가 아니라, 어떤 DeviceType을 사용하느냐로 구분.

    과금(billable_yn):
    - 'yes'  → Subscription 계산 시 과금 대상
    - 'no'   → 과금 제외 (내부 관리용 장비)
    """
    __tablename__ = "tb_device_type"

    i_device_type_id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    i_device_category_id = Column(
        Integer,
        ForeignKey("tb_device_category.i_device_category_id"),
        nullable=False,
        index=True,
    )
    c_device_type_code = Column(String(50), nullable=False, unique=True, index=True)
    c_device_type_name = Column(String(100), nullable=False)
    c_description = Column(String, nullable=True)
    c_billable_yn = Column(String(3), nullable=False, default="yes")   # 'yes' / 'no'
    n_default_monthly_fee = Column(Numeric(10, 2), nullable=False, default=0)
    i_sort_order = Column(Integer, nullable=False, default=100)
    c_status = Column(String(50), nullable=False, default="active", index=True)
