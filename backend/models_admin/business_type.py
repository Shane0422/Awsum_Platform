from sqlalchemy import Column, Integer, String
from sqlalchemy.orm import synonym
from backend.database.pg_platform import PlatformBase
from backend.models_common.audit_mixin import AuditMixin

class BusinessType(PlatformBase, AuditMixin):
    __tablename__ = "tb_business_type"

    # 기본키 (하위 호환성 유지용 레거시 코드명 보존)
    i_business_type_id = Column(Integer, primary_key=True, autoincrement=True, index=True)

    # 신규 로직에서 사용하는 표준 속성명
    i_business_type = synonym("i_business_type_id")

    # 분류 코드 코럼
    c_business_code = Column(String, nullable=False, unique=True)
    c_business_name_kr = Column(String, nullable=True)
    c_status = Column(String, default="active")  # active / inactive

    # 표준 명칭 접근자 (기존 레거시 코럼 'c_name'에 매핑)
    c_name = Column(String, nullable=False, unique=True)
    c_business_name = synonym("c_name")

    # 레거시 별칭 (구버전 c_description 사용 코드 호환용)
    c_description = synonym("c_name")

    # ✅ AuditMixin 상속 컬럼
    # dt_created, i_created_by, dt_updated, i_updated_by
