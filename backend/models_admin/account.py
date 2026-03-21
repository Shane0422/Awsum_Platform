from sqlalchemy import Column, Integer, String
from sqlalchemy.orm import synonym

from backend.database.pg_platform import PlatformBase
from backend.models_common.audit_mixin import AuditMixin
from backend.models_common.address_mixin import AddressMixin
from backend.models_common.contact_mixin import ContactMixin


class Client(PlatformBase, ContactMixin, AddressMixin, AuditMixin):
    __tablename__ = "tb_client"

    i_client_id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    c_client_code = Column(String(20), nullable=False, unique=True)
    i_business_type = Column(Integer, nullable=True)
    c_channel_type = Column(String, nullable=True)   # 예: Direct / Reseller / Partner / Online / Franchise / Internal

    # 표준 B2B 명칭
    c_client_name = Column(String, nullable=False, unique=True)
    c_status = Column(String, default="active")

    # 구버전 account 코드 호환용 별칭
    i_account_id = synonym("i_client_id")
    c_account_name = synonym("c_client_name")

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


# 구버전 모듈 호환용 클래스 별칭 (마이그레이션 전까지 사용)
Account = Client
