from sqlalchemy import Column, Integer, String, ForeignKey, UniqueConstraint
from backend.database.admin_db import AdminBase
from backend.models_common.audit_mixin import AuditMixin
from backend.models_common.contact_mixin import ContactMixin

class User(AdminBase, ContactMixin, AuditMixin):
    __tablename__ = "tb_user"

    i_user_id = Column(Integer, primary_key=True, autoincrement=True, index=True)

    # ✅ 관계
    i_store_id = Column(Integer, ForeignKey("tb_store.i_store_id"), nullable=True)
    i_role_id  = Column(Integer, ForeignKey("tb_role.i_role_id"), nullable=True)

    # ✅ 인증 정보  Store 관리자가 정의 한다. 
    c_password = Column(String, nullable=False)   # 해시된 비밀번호
    i_role = Column(Integer, default=2)           # 권한 (1=Admin, 2=User)
    c_status = Column(String, default="active")   # active / inactive / locked

    # ✅ ContactMixin 상속 컬럼
    # c_first_name : 이름
    # c_last_name  : 성
    # c_email      : 이메일 (로그인 ID)
    # c_phone      : 전화번호

    # ✅ AuditMixin 상속 컬럼
    # dt_created   : 최초 등록일
    # i_created_by : 최초 등록자
    # dt_updated   : 마지막 수정일
    # i_updated_by : 마지막 수정자

    __table_args__ = (
        UniqueConstraint("i_store_id", "c_email", name="uq_store_email"),  # 같은 Store 내 중복 이메일 방지
    )
