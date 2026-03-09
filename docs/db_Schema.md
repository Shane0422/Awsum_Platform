
# tuxedo_rental\docs\DB_Schema.md

📄 DB_Schema.md (최종본)
1. 공통 설계 원칙

모든 테이블에는 AuditMixin (dt_created, i_created_by, dt_updated, i_updated_by) 적용
필요 시 ContactMixin (c_first_name, c_last_name, c_email, c_phone) 적용
필요 시 AddressMixin (c_address_line1, c_address_line2, c_city, c_state, c_zip, c_country) 적용
모든 참조(FK)는 숫자 ID(PK) 로만 연결 → c_name 은 변경 가능, ID는 불변
Seed 데이터는 영문 코드 저장 + Python 코드에 한글 주석

2. 주요 테이블
2.1 tb_store_type (업체 구분)
HeadOffice (# 본사)
Branch (# 지사)
Agency (# 대리점)
Partner (# 협력사)

2.2 tb_business_type (업종 구분)
Laundry (# 세탁소)
Wedding (# 웨딩)
FlowerShop (# 꽃집)
CakeShop (# 케이크)
Other (# 기타)

2.3 tb_role (사용자 권한)
SuperAdmin (# 전체 시스템 관리자, Shane)
Admin (# 지점 관리자)
Manager (# 매장 운영 책임자)
Staff (# 매장 직원)
Customer (# 일반 고객)

2.4 tb_account (상위 계정)
Account명, 상태
ContactMixin
AuditMixin

2.5 tb_store (업체 관리)
업체명, 고유 비밀번호, 만료일자
StoreType / BusinessType FK
대표자, 사업자 등록번호
ContactMixin, AddressMixin
2.6 tb_user (사용자 계정)
Store FK, Role FK
Email + Password (유니크 조합)
ContactMixin
AuditMixin

2.7 tb_session (로그인 세션)
User FK
JWT 토큰, 로그인/활동 시간
세션 상태 (active/terminated)
AuditMixin

3. 공통 Mixin
AuditMixin
dt_created, i_created_by
dt_updated, i_updated_by
ContactMixin
c_first_name, c_last_name
c_email, c_phone

AddressMixin
c_address_line1, c_address_line2
c_city, c_state, c_zip, c_country

4. 로그인/등록 흐름 요약
Shane이 업체 등록 (StoreID + StorePW 발급, 만료일자 옵션)
고객 Register → StoreID + StorePW 확인 후 Email + Password 등록
로그인: StoreID + Email + Password
세션 충돌 시: “이미 다른 곳에서 사용 중” → 승인 시 기존 세션 종료 후 신규 로그인

5. ERD (엔티티 관계 다이어그램)
ERD 다이어그램 (공통 Mixin 포함)
erDiagram

    STORE_TYPE {
        int i_store_type_id PK
        string c_name
        string c_description
        string c_status
    }

    BUSINESS_TYPE {
        int i_business_type_id PK
        string c_name
        string c_description
        string c_status
    }

    ROLE {
        int i_role_id PK
        string c_name
        string c_description
        string c_status
    }

    ACCOUNT {
        int i_account_id PK
        string c_account_name
        string c_status
    }

    STORE {
        int i_account_id FK
        int i_store_id PK
        string c_store_name
        string c_store_pw
        datetime dt_pw_expire
        string c_owner_name
        string c_business_no
        string c_status
    }

    USER {
        int i_user_id PK
        int i_store_id FK
        int i_role_id FK
        string c_password
        string c_status
    }

    SESSION {
        int i_session_id PK
        int i_user_id FK
        string c_jwt_token
        datetime dt_login
        datetime dt_last_active
        string c_status
        datetime dt_terminated
    }

    %% 공통 Mixin
    AUDIT_MIXIN {
        datetime dt_created
        int i_created_by FK
        datetime dt_updated
        int i_updated_by FK
    }

    CONTACT_MIXIN {
        string c_first_name
        string c_last_name
        string c_email
        string c_phone
    }

    ADDRESS_MIXIN {
        string c_address_line1
        string c_address_line2
        string c_city
        string c_state
        string c_zip
        string c_country
    }

    %% 관계
    ACCOUNT ||--o{ STORE : "owns"
    STORE_TYPE ||--o{ STORE : "has"
    BUSINESS_TYPE ||--o{ STORE : "has"
    ROLE ||--o{ USER : "assigns"
    STORE ||--o{ USER : "owns"
    USER ||--o{ SESSION : "creates"

    %% 공통 부분 연결
    AUDIT_MIXIN ||..|| ACCOUNT : "includes"
    AUDIT_MIXIN ||..|| STORE : "includes"
    AUDIT_MIXIN ||..|| USER : "includes"
    AUDIT_MIXIN ||..|| SESSION : "includes"
    AUDIT_MIXIN ||..|| STORE_TYPE : "includes"
    AUDIT_MIXIN ||..|| BUSINESS_TYPE : "includes"
    AUDIT_MIXIN ||..|| ROLE : "includes"

    CONTACT_MIXIN ||..|| ACCOUNT : "includes"
    CONTACT_MIXIN ||..|| STORE : "includes"
    CONTACT_MIXIN ||..|| USER : "includes"

    ADDRESS_MIXIN ||..|| STORE : "includes"