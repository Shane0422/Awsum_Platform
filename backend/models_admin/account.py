from sqlalchemy import Column, Integer, String
from backend.database.admin_db import AdminBase
from backend.models_common.audit_mixin import AuditMixin
from backend.models_common.contact_mixin import ContactMixin


class Account(AdminBase, ContactMixin, AuditMixin):
    __tablename__ = "tb_account"

    i_account_id = Column(Integer, primary_key=True, autoincrement=True, index=True)

    # ✅ 계정 기본 정보
    c_account_name = Column(String, nullable=False, unique=True)
    c_status = Column(String, default="active")

    # ✅ ContactMixin 상속 컬럼
    # c_first_name : 계정 담당자 이름
    # c_last_name  : 계정 담당자 성
    # c_email      : 계정 대표 이메일
    # c_phone      : 계정 대표 연락처

    # ✅ AuditMixin 상속 컬럼
    # dt_created   : 최초 등록일자
    # i_created_by : 최초 등록자
    # dt_updated   : 마지막 수정일자
    # i_updated_by : 마지막 수정자
