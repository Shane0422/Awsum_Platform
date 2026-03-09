from sqlalchemy import Column, Integer, String, ForeignKey, DateTime
from backend.database.admin_db import AdminBase
from backend.models_common.audit_mixin import AuditMixin
from backend.models_common.contact_mixin import ContactMixin
from backend.models_common.address_mixin import AddressMixin

class Store(AdminBase, ContactMixin, AddressMixin, AuditMixin):
    __tablename__ = "tb_store"

    i_store_id = Column(Integer, primary_key=True, autoincrement=True, index=True)

    # ✅ 기본 정보
    c_store_name = Column(String, nullable=False)     # 업체명
    c_store_pw   = Column(String, nullable=False)     # 업체 고유 비밀번호 (Shane 발급, 고객 변경 불가)
    dt_pw_expire = Column(DateTime, nullable=True)    # 업체 비밀번호 만료일자 (optional)

    # ✅ 관계 (계정/구분/업종)
    i_account_id       = Column(Integer, ForeignKey("tb_account.i_account_id"), nullable=True)            # 계정 (1:N, Account -> Store)
    i_store_type_id    = Column(Integer, ForeignKey("tb_store_type.i_store_type_id"), nullable=True)       # 본사/지사/대리점/협력사
    i_business_type_id = Column(Integer, ForeignKey("tb_business_type.i_business_type_id"), nullable=True) # 업종 (세탁소/웨딩/꽃집 등)

    # ✅ 사업자 정보
    c_owner_name  = Column(String, nullable=True)     # 대표자 성명
    c_business_no = Column(String, nullable=True)     # 사업자 등록번호

    # ✅ ContactMixin 상속 컬럼
    # c_first_name : 담당자 이름
    # c_last_name  : 담당자 성
    # c_email      : 대표 이메일
    # c_phone      : 대표 전화번호

    # ✅ AddressMixin 상속 컬럼
    # c_address_line1 : 기본 주소
    # c_address_line2 : 상세 주소
    # c_city          : 도시
    # c_state         : 주/도
    # c_zip           : 우편번호
    # c_country       : 국가 (기본 USA)

    # ✅ 상태
    c_status = Column(String, default="active")       # active / inactive

    # ✅ AuditMixin 상속 컬럼
    # dt_created   : 최초 등록일자
    # i_created_by : 최초 등록자
    # dt_updated   : 마지막 수정일자
    # i_updated_by : 마지막 수정자
