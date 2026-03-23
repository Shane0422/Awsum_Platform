from sqlalchemy import Column, Integer, String, ForeignKey, DateTime
from sqlalchemy.orm import synonym
from backend.database.pg_platform import PlatformBase
from backend.models_common.audit_mixin import AuditMixin
from backend.models_common.contact_mixin import ContactMixin
from backend.models_common.address_mixin import AddressMixin

class Store(PlatformBase, ContactMixin, AddressMixin, AuditMixin):
    __tablename__ = "tb_store"

    i_store_id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    i_store_seq = Column(Integer, nullable=True, unique=True, index=True)  # 업체 스토어 코드 생성용 시퀀스
    c_is_test = Column(Integer, default=0)  # 1 = test store (protected)

    # ✅ 추가 기본 정보 (신규 필드)
    c_store_code = Column(String, nullable=True, unique=True)  # 고유 스토어 코드

    # ✅ 기본 정보
    c_store_name = Column(String, nullable=False)     # 업체명
    c_store_pw   = Column(String, nullable=True)     # 업체 고유 비밀번호 (legacy; not required for profile data)
    dt_pw_expire = Column(DateTime, nullable=True)    # 업체 비밀번호 만료일자 (optional)

    # ✅ 관계 (계정/구분/업종)
    i_account_id       = Column(Integer, ForeignKey("tb_client.i_client_id"), nullable=True)            # account (1:N, Account -> Store)
    i_client_id        = synonym("i_account_id")  # Legacy compatibility

    # 관계 (업종)
    i_business_type_id = Column(Integer, ForeignKey("tb_business_type.i_business_type_id"), nullable=True) # 업종 (세탁소/웨딩/꽃집 등)

    # 기능/채널/디바이스 분류는 StoreType 마스터 대신 store 속성으로 관리
    c_operation_type = Column(String, nullable=True)   # 예: Information POS / CheckIn Kiosk
    c_store_purpose = synonym("c_operation_type")
    c_channel_type = Column(String, nullable=True)     # 예: POS / 키오스크 / 모바일 / 관리
    c_device_type = Column(String, nullable=True)      # 예: Windows POS / Android Kiosk

    # 신규 코드에서 사용하는 표준 속성명
    i_business_type = synonym("i_business_type_id")

    # ✅ 사업자 정보
    c_owner_name  = Column(String, nullable=True)     # 대표자 성명
    c_business_no = Column(String, nullable=True)     # 사업자 등록번호
    c_contact_name = Column(String, nullable=True)

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

    # ✅ 시스템 정보
    c_receipt_store_name = Column(String, nullable=True)
    c_receipt_phone = Column(String, nullable=True)
    c_receipt_email = Column(String, nullable=True)
    c_receipt_website_url = Column(String, nullable=True)
    c_receipt_message = Column(String, nullable=True)
    c_default_tax_rate = Column(String, nullable=True)
    c_timezone = Column(String, nullable=True)
    c_tax_source = Column(String, nullable=True)
    c_website = Column(String, nullable=True)
    c_db_path = Column(String, nullable=True)
    # 계약/용량 정보는 향후 Store_Head/Store_Detail 분리 대비 공통 테이블에 보관
    dt_contract_date = Column(DateTime, nullable=True)
    dt_contract_start = Column(DateTime, nullable=True)
    dt_contract_end = Column(DateTime, nullable=True)
    c_license_type = Column(String, nullable=True)
    n_license_count = Column(Integer, nullable=True)
    i_max_user_count = Column(Integer, nullable=True)
    i_max_terminal_count = Column(Integer, nullable=True)
    dt_license_expire = Column(DateTime, nullable=True)
    c_dashboard_type = Column(String, nullable=True)
    c_memo = Column(String, nullable=True)
    c_remark = Column(String, nullable=True)

    # ✅ 담당 에이전트 (설치/관리 담당자)
    i_installed_by_agent_id = Column(Integer, ForeignKey("tb_agent.i_agent_id"), nullable=True)

    # ✅ 상태
    c_status = Column(String, default="active")       # active / inactive

    # ✅ AuditMixin 상속 컬럼
    # dt_created   : 최초 등록일자
    # i_created_by : 최초 등록자
    # dt_updated   : 마지막 수정일자
    # i_updated_by : 마지막 수정자
