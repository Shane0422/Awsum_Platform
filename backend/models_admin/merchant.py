from sqlalchemy import Column, ForeignKey, Integer, String, Text

from backend.database.pg_platform import PlatformBase
from backend.models_common.audit_mixin import AuditMixin
from backend.models_common.address_mixin import AddressMixin
from backend.models_common.contact_mixin import ContactMixin


class Merchant(PlatformBase, ContactMixin, AddressMixin, AuditMixin):
    __tablename__ = "tb_merchant"

    i_merchant_id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    c_merchant_code = Column(String(20), nullable=False, unique=True)
    i_business_type = Column(Integer, nullable=True)
    i_agency_id = Column(Integer, ForeignKey("tb_agency.i_agency_id"), nullable=True, index=True)
    c_channel_type = Column(String, nullable=True)   # 예: Direct / Reseller / Partner / Online / Franchise / Internal

    # 표준 B2B 명칭
    c_merchant_name = Column(String, nullable=False, unique=True)
    c_logo_url = Column(String(1000), nullable=True)
    c_status = Column(String, default="active")
    c_memo = Column(String, nullable=True)
    c_website = Column(String(255), nullable=True)
    c_notes = Column(Text, nullable=True)
    c_timezone = Column(String(64), nullable=True)

    # ContactMixin 상속 컬럼
    # c_first_name : 고객사 담당자 이름
    # c_last_name  : 고객사 담당자 성
    # c_email      : 고객사 대표 이메일
    # c_phone      : 고객사 대표 전화번호

    # AddressMixin 상속 컬럼
    # c_address_line1 : 기본 주소
    # c_address_line2 : 상세 주소
    # c_city          : 도시
    # c_state         : 주/도
    # c_zip           : 우편번호
    # c_country       : 국가

    # AuditMixin 상속 컬럼
    # dt_created   : 최초 등록일자
    # i_created_by : 최초 등록자
    # dt_updated   : 마지막 수정일자
    # i_updated_by : 마지막 수정자
