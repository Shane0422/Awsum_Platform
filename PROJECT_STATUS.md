# Awsum Platform — 프로젝트 현황 (PROJECT_STATUS.md)

> **마지막 업데이트:** 2026-03-24  
> **작성 기준:** Nova가 이해할 수 있도록, 완료/진행중/미구현/DB/API/UI/다음단계 기준으로 정리.  
> **이 파일 관리 원칙:** 기능 추가·변경·완료 시 해당 항목을 즉시 업데이트한다.

---

## 1. 프로젝트 개요

멀티스토어 SaaS 플랫폼 (POS/키오스크/모바일 주문 구독 관리).

| 항목 | 내용 |
|---|---|
| Backend | FastAPI + SQLAlchemy (ORM) + PostgreSQL (`awsum_platform` DB) |
| Template/UI | Jinja2 + Bootstrap 5 (Platform Admin), Svelte (클라이언트 대시보드, 부분 구현) |
| Auth | JWT (jose) + bcrypt, 쿠키 저장, 플랫폼 관리자 전용 `tb_platform_user` |
| 실행 | `Awsum_Run_Server.bat` → uvicorn 127.0.0.1:8001 (단일 프로세스) |
| Admin 고정 계정 | store_code=`Admin`, email=`is2ceo@gmail.com`, pw=`Awsum123!` |

---

## 2. DB 모델 현황

### ✅ 완료된 모델 (`backend/models_admin/`)

| 테이블 | 모델 파일 | 핵심 컬럼 / 비고 |
|---|---|---|
| `tb_platform_user` | platform_user.py | 플랫폼 관리자 계정, `i_must_change_password` 강제 변경 플래그 |
| `tb_client` (Account) | account.py | `c_client_code`(CLT_XXXXX), `i_agent_id`, 로고 지원 |
| `tb_store` | store.py | `c_store_code`(STR_XXXXX), dashboard_type, 로고, 세금/타임존 |
| `tb_role` | role.py | 역할 정의 |
| `tb_business_type` | business_type.py | 업종 정의 |
| `tb_agent_type` | agent_type.py | 대리점 유형 |
| `tb_agent` | agent.py | 대리점, 수수료율, 담당자 정보 |
| `tb_license` | license.py | 라이선스 키, 스토어 연결, 상태 관리 |
| `tb_device` | device.py | POS/키오스크/모바일 디바이스, 활성화 토큰(TTL 30분), `i_device_type_id`(FK), `c_serial_no`, `dt_installed_at`, `c_memo` |
| `tb_device_log` | device_log.py | 디바이스 활성화/이벤트 로그 |
| `tb_device_category` | device_category.py | **[NEW]** 업종 무관 디바이스 카테고리 마스터 (`c_category_code`, `c_category_name`, `c_description`) |
| `tb_device_type` | device_type.py | **[NEW]** 업종 무관 디바이스 유형 마스터 (`i_device_category_id` FK, `c_device_type_code`, `c_billable_yn`, `n_default_monthly_fee`) |
| `tb_session` | session.py | 사용자 세션 (login_at, last_active_at, terminated_at) |
| `tb_payment_method` | payment_method.py | 카드/계좌이체/현금, billing_cycle, next_billing |
| `tb_billing` | billing.py | (레거시 테이블, 현재는 invoice로 대체 중) |
| `tb_provision_log` | provision_log.py | 프로비저닝 이력 |
| `tb_store_sync_status` | store_sync_status.py | 스토어 동기화 상태 |
| `tb_pricing_plan` | pricing_plan.py | SaaS 과금 플랜 (아래 세부 내용 참조) |
| `tb_subscription` | subscription.py | 계약/스냅샷/상태/청구 주기 (아래 세부 내용 참조) |
| `tb_contract` | contract.py | Subscription 기준 계약 스냅샷/월 과금/세금/총액/PDF 경로 |
| `tb_invoice` | invoice.py | 인보이스 헤더 (아래 세부 내용 참조) |
| `tb_invoice_line` | invoice.py | 인보이스 라인 아이템 |

#### PricingPlan 주요 컬럼
```
plan_code, plan_name, store_base_fee (월기본료),
included_{pos,kiosk,mobile_order,user}_count (포함 수량),
{pos,kiosk,mobile_order,extra_user,extra_device}_fee (초과 단가),
setup_fee, contract_term_month, transaction_fee_rate (%),
currency, sort_order, is_default, status
```

#### Subscription 주요 컬럼 (계약 시점 스냅샷 포함)
```
account_id, store_id, plan_id (FK),
plan_code/name (스냅샷), store_base_fee/pos_fee/.../transaction_fee_rate (스냅샷),
monthly_fee (계약 월액), start_date, end_date,
device_limit, status, billing_cycle, renewal_status, dt_next_billing
```

#### Invoice / InvoiceLine
```
invoice_no (INV-YYYYMMDD-XXXX), subscription_id, account_id, store_id,
invoice_date, due_date, subtotal, tax, total, currency,
status: issued → paid | void (paid→void 차단, void→* 차단)

InvoiceLine: line_type, description, quantity (4dp), unit_price, amount
```

#### Contract
```
contract_id, subscription_id, account_id, store_id, pricing_plan_id,
contract_start_date, contract_end_date, contract_term_month,
setup_fee, monthly_base_fee, monthly_device_fee, monthly_user_fee,
monthly_total_fee, tax_rate, tax_amount, total_monthly_fee,
status(active/terminated/expired), contract_pdf_path, created_at
```

### 🔄 DB 마이그레이션 전략

- `backend/database/db_init_platform.py` — 앱 시작 시 자동 실행
- `backend/utils/safe_schema_migrate.py` — 컬럼 추가만 허용 (삭제 없음), 백업 후 정렬
- 수동 보강 함수: `_ensure_client_code_schema_and_backfill`, pricing_plan SaaS 컬럼, subscription 스냅샷 컬럼
- 시드 데이터: 기본 역할/업종/플랜/플랫폼 관리자 계정 자동 생성

---

## 3. API 엔드포인트 현황

### ✅ 완료 — 인증 (`backend/routers/auth.py`, prefix `/auth`)

| Method | Path | 설명 |
|---|---|---|
| GET | `/auth/login` | 로그인 페이지 (reason 파라미터 → 안내 문구 + 모달 자동 오픈) |
| POST | `/auth/login` | 로그인 처리, JWT 쿠키 발급 |
| GET/POST | `/auth/change-password` | 비밀번호 변경 (forced rotation 지원) |
| POST | `/auth/register` | 회원가입 (스토어 사용자용) |
| GET | `/auth/logout` | 로그아웃 (쿠키 삭제) |
| GET | `/auth/redirector` | 역할 기반 대시보드 리다이렉트 |

### ✅ 완료 — 플랫폼 대시보드/마스터 (`backend/routers/dashboard.py`)

| Method | Path | 설명 |
|---|---|---|
| GET | `/platform/dashboard` | 플랫폼 어드민 대시보드 (KPI: 계정수, 스토어수 등) |
| GET | `/platform/master/{module}` | 마스터 관리 화면 (12개 모듈 공통 렌더) |
| GET | `/platform/master/invoices` | `/platform/master/invoice` 로 303 리다이렉트 (별칭) |

### ✅ 완료 — 플랫폼 관리 API (`backend/routers/platform_store.py`, prefix 없음)

#### 계정(Client/Account)
| Method | Path |
|---|---|
| GET | `/platform/clients` |
| GET/PUT/DELETE | `/platform/client/{id}` |
| POST | `/platform/client` |
| POST/DELETE | `/platform/client/{id}/logo` |

#### 스토어
| Method | Path |
|---|---|
| GET | `/platform/stores`, `/platform/store/{id}` |
| POST/PUT/DELETE | `/platform/store`, `/platform/store/{id}` |
| POST/DELETE | `/platform/store/{id}/logo` |
| GET/POST/PUT/DELETE | `/platform/client/{id}/stores`, `/platform/client/{id}/stores/{store_id}` |

#### 디바이스
| Method | Path |
|---|---|
| GET/POST/PUT/DELETE | `/platform/client/{id}/stores/{store_id}/devices[/{device_id}]` |
| POST | `/platform/device/activate` (활성화 토큰 발급) |
| POST | `/platform/device/validate` (토큰 검증) |

#### 디바이스 마스터 (`backend/routers/device_master.py`) **[NEW]**
| Method | Path | 설명 |
|---|---|---|
| GET | `/platform/device-categories` | 카테고리 목록 |
| GET/PATCH/DELETE | `/platform/device-categories/{id}` | 카테고리 상세/수정/삭제(soft) |
| POST | `/platform/device-categories` | 카테고리 생성 |
| GET | `/platform/device-types` | 디바이스 유형 목록 (카테고리 join) |
| GET/PATCH/DELETE | `/platform/device-types/{id}` | 유형 상세/수정/삭제(soft) |
| POST | `/platform/device-types` | 유형 생성 |

#### 디바이스 기반 과금 (`backend/routers/subscription.py`) **[NEW]**
| Method | Path | 설명 |
|---|---|---|
| GET | `/subscriptions/store/{store_id}/device-billing-summary` | 실제 설치 디바이스 기준 과금 미리보기 (카테고리별 수량, 초과 요금 계산) |

#### 마스터 데이터
| 도메인 | 경로 패턴 | CRUD |
|---|---|---|
| Role | `/platform/roles`, `/platform/role[/{id}]` | CRUD |
| BusinessType | `/platform/business-types`, `/platform/business-type[/{id}]` | CRUD |
| AgentType | `/platform/agent-types`, `/platform/agent-type[/{id}]` | CRUD |
| Agent | `/platform/agents`, `/platform/agent[/{id}]` | CRUD |
| License | `/platform/licenses`, `/platform/license[/{id}]` | CRUD |
| PaymentMethod | `/platform/payment-methods`, `/platform/payment-method[/{id}]` | CRUD |
| User | `/platform/users`, `/platform/user[/{id}]` | CRUD |
| Session | `/platform/sessions`, `/platform/session/{id}` | GET/PUT(terminate)/DELETE |

#### Pricing Plan
| Method | Path | 설명 |
|---|---|---|
| GET | `/platform/pricing-plans` | 목록 (검색/상태 필터) |
| GET | `/platform/pricing-plan/{id}` | 상세 |
| POST | `/platform/pricing-plan` | 생성 (plan_code 중복 체크) |
| PUT | `/platform/pricing-plan/{id}` | 수정 |
| DELETE | `/platform/pricing-plan/{id}` | 비활성화(soft delete) |

#### Invoice (플랫폼 관리자)
| Method | Path | 설명 |
|---|---|---|
| GET | `/platform/invoices` | 목록 (검색/상태 필터, line_count 집계) |
| GET | `/platform/invoice/{id}` | 상세 (라인 포함) |
| GET | `/platform/invoice/{id}/download-html` | HTML 인보이스 출력 (인쇄용 팝업) |
| PUT | `/platform/invoice/{id}` | 상태/만기일/메모 수정 (전환 규칙 강제) |
| DELETE | `/platform/invoice/{id}` | Void 처리 (issued→void만 허용) |

#### Contract (플랫폼 관리자)
| Method | Path | 설명 |
|---|---|---|
| GET | `/platform/contracts` | 목록 (검색/상태 필터) |
| GET | `/platform/contract/{id}` | 상세 |
| PUT | `/platform/contract/{id}` | 상태/종료일 수정 |
| DELETE | `/platform/contract/{id}` | 계약 종료(terminated) |
| GET | `/platform/contract/{id}/download-html` | 계약서 인쇄용 HTML |
| GET | `/platform/contract/{id}/download-pdf` | 계약서 PDF 생성/다운로드 + 경로 저장 |

**상태 전환 규칙:**
- `issued → paid` ✅
- `issued → void` ✅
- `paid → void` ❌ 차단
- `void → *` ❌ 차단

### ✅ 완료 — 구독/인보이스 (`backend/routers/subscription.py`, prefix `/subscriptions`)

| Method | Path | 설명 |
|---|---|---|
| GET | `/subscriptions` | 목록 (검색/상태/계정/스토어 필터) |
| GET | `/subscriptions/{id}` | 상세 |
| POST | `/subscriptions` | 생성 (플랜 스냅샷 자동 저장) |
| PUT | `/subscriptions/{id}` | 수정 |
| DELETE | `/subscriptions/{id}` | 취소 |
| GET | `/subscriptions/account/{id}/subscriptions` | 계정별 구독 목록 |
| GET | `/subscriptions/store/{id}/subscriptions` | 스토어별 구독 목록 |
| GET | `/subscriptions/{id}/billing-preview` | Billing preview 계산 (과금 미리보기) |
| GET | `/subscriptions/{id}/contract-summary` | 구독의 최신 Contract 요약 (계약 ID/월요금/상태) |
| POST | `/subscriptions/{id}/invoices` | 단건 인보이스 발행 |
| POST | `/subscriptions/invoices/batch` | 월 배치 인보이스 발행 (옵션: only_due, skip_existing_month, include_setup_for_first_invoice) |
| GET | `/subscriptions/{id}/invoices` | 구독별 인보이스 목록 |
| GET | `/subscriptions/invoices/{invoice_id}` | 인보이스 상세 |

### 🔲 미구현 — API

- 인보이스 이메일 자동 발송
- PDF 생성 (현재는 HTML 인쇄 방식)
- 결제 게이트웨이 연동 (Stripe/etc)
- 구독 자동 갱신/만료 처리 (배치 Job)
- 매출/정산 리포트 API
- 고객 포털 API (스토어 고객용)

---

## 4. UI 화면 현황

### ✅ 완료 — 플랫폼 어드민 UI

모든 화면은 `templates/platform/` 에 위치하며 `platform_base.html` 레이아웃 상속.

#### 로그인 (`login.html`)
- JWT 쿠키 방식 로그인, 비밀번호 표시/숨기기
- Store Code 기억, Email 기억 (localStorage)
- 세션 만료/권한 부족 시 안내 배너 + 로그인 모달 자동 오픈 (`?reason=session_expired | platform_admin_required`)

#### 플랫폼 대시보드 (`platform_dashboard.html`)
- KPI 카드 (총 계정수, 활성/비활성 스토어, 사용자 수, 최근 계정)
- 사이드바 (Dashboard / Accounts / Agents / Agent Types / Subscriptions / Contracts / Pricing Plans / Invoices / Payment Methods / Device Categories / Device Types / Business Types / Roles / Users / Sessions)
- 사이드바 하단 고정 Logout 버튼 (flex 분리 레이아웃)

#### 마스터 관리 (`master_management.html`) — 공통 CRUD 엔진

`/platform/master/{module}` 으로 접근. 모든 모듈 공통 구조:
- 리스트뷰 (검색, 상태 필터, 정렬, Export CSV/Excel/PDF/Print)
- 편집 모달 (섹션 그룹, 탭, 입력 검증, dirty 감지)
- Confirm 모달 (Delete/Terminate/Void 시 확인)
- Device Type 모듈은 Category 필터를 지원하며, Device Category/Type 코드는 편집 시 read-only

| 모듈 키 | 경로 | 특이사항 |
|---|---|---|
| `account` | /platform/master/account | 로고 업로드, ZIP 자동완성(API), 하위 스토어 패널, 디바이스 패널 |
| `agent` | /platform/master/agent | 수수료율, 담당자 정보 |
| `agent-type` | /platform/master/agent-type | 코드 자동 정규화(대문자+언더스코어) |
| `subscription` | /platform/master/subscription | Monthly Billing Preview (Store Base/Device/User/Subtotal/Tax/Total), Contract Monthly Fee 자동 계산 |
| `contract` | /platform/master/contract | Contract 상세/월요금/세금 표시, Print Contract(PDF 생성) |
| `pricing-plan` | /platform/master/pricing-plan | 섹션 그룹화 (포함수량/요금설정), 요금 요약 문장 자동생성 |
| `invoice` | /platform/master/invoice | 읽기전용(생성 불가), Download 버튼 (HTML 팝업 인쇄), 상태 전환 (issued→paid/void), Void = soft delete |
| `payment-method` | /platform/master/payment-method | 결제 유형/주기/다음 청구일 |
| `license` | /platform/master/license | 라이선스 키, 만료 상태 |
| `business-type` | /platform/master/business-type | 업종 코드/이름 |
| `role` | /platform/master/role | 역할 이름/설명 |
| `user` | /platform/master/user | 플랫폼 사용자 관리 |
| `session` | /platform/master/session | 세션 조회/강제 종료 |
| `store` | /platform/master/store | 스토어 기본정보 (로고, 주소, 세금) |
| `device-category` | /platform/master/device-category | **[NEW]** 디바이스 카테고리 마스터 (코드/이름/설명/순서) |
| `device-type` | /platform/master/device-type | **[NEW]** 디바이스 유형 마스터 (카테고리 연결, 과금여부, 기본월요금) |

### 🔄 진행중 — 클라이언트 대시보드 UI

| 템플릿 | 상태 | 비고 |
|---|---|---|
| `client/dashboard_standard.html` | 기본 구조 완성 | 실제 데이터 연동 미완 |
| `client/dashboard_restaurant.html` | 플레이스홀더 | 미구현 |
| `client/dashboard_deli.html` | 플레이스홀더 | 미구현 |
| `client/dashboard_tuxedo.html` | 플레이스홀더 | 미구현 |
| `store/workspace_dashboard.html` | 기본 구조 | 재고/업무 패널 미구현 |

### 🔲 미구현 — UI

- 고객 포털 (스토어 고객 로그인/프로필/주문 내역)
- 인보이스 이메일 발송 UI
- 구독 갱신/업그레이드 셀프서비스 화면
- 관리자 권한별 메뉴 제어 (현재 단순 platform admin 여부만 체크)
- 리포트/정산 대시보드
- 모바일 최적화

---

## 5. 보안 & 인프라 현황

| 항목 | 상태 | 비고 |
|---|---|---|
| JWT 인증 | ✅ | HttpOnly 쿠키, SameSite=lax |
| 비밀번호 해싱 | ✅ | bcrypt, 72byte 제한 검증 |
| 강제 비밀번호 변경 | ✅ | `i_must_change_password` 플래그 |
| 세션 만료 안내 | ✅ | reason 파라미터 → 모달 자동 오픈 |
| SQL Injection 방지 | ✅ | SQLAlchemy ORM, 파라미터 바인딩 |
| DB 커넥션 풀 | ✅ | `get_db()` with yield/finally (누수 방지) |
| CORS | 🔲 | 미설정 (현재 동일 오리진만 사용) |
| HTTPS | 🔲 | 개발 환경, 배포 시 설정 필요 |
| Rate Limiting | 🔲 | 미구현 |
| 로그 | 부분 | uvicorn 기본 로그, 앱 로그 미구현 |

---

## 6. 다음 단계 (Next Steps)

### 단기 (즉시 가능한 항목)

1. **Invoice 이메일 발송** — 발행 시 계정 담당자에게 인보이스 HTML 이메일 전송 (SMTP 설정 필요)
2. **Invoice PDF 저장** — `weasyprint` 또는 서버사이드 HTML→PDF 변환, 파일 저장/다운로드
3. **구독 자동 만료 처리** — 배치 Job: `dt_end_date` 초과 구독 자동 `expired` 전환
4. **next 파라미터 기반 로그인 후 원래 페이지 복귀** — `/auth/login?next=...` 로그인 성공 후 해당 URL로 리다이렉트
5. **클라이언트 대시보드 데이터 연동** — 스토어별 KPI(매출/주문/재고) API 및 차트 렌더

> 참고: Subscription 생성 시 Contract가 자동 생성되며, Invoice 생성은 Contract의 `total_monthly_fee`를 기준으로 계산되도록 반영됨.

### 중기

6. **결제 게이트웨이 연동** — Stripe 또는 타 PG, Payment Method와 실제 청구 연결
7. **구독 자동 갱신** — 만료 전 자동 재발행, 실패 시 `renewal_status=failed` 처리
8. **리포트/정산 API** — 기간별 인보이스 집계, 계정별 매출 요약
9. **고객 포털** — 스토어 고객 계정 로그인, 청구서 조회, 주문 이력
10. **권한 세분화** — 관리자/매니저/직원 레벨별 메뉴/API 접근 제어

### 장기

11. **Svelte 클라이언트 대시보드 완성** — `frontend/` 디렉터리, 현재 설정만 존재
12. **모바일 API** — 스토어 앱용 REST API 별도 라우터
13. **HTTPS/CORS 설정** — 배포 환경 인프라 구성
14. **로그 & 모니터링** — 구조화 로그, Sentry 또는 유사 오류 모니터링

---

## 7. 파일 구조 요약

```
Awsum_Platform/
├── backend/
│   ├── main.py                    # FastAPI 앱 + 라우터 등록
│   ├── config/
│   │   ├── settings.py            # APP_NAME, 환경 변수
│   │   └── templates.py           # Jinja2 + get_brand_context()
│   ├── core/
│   │   └── logging_config.py
│   ├── database/
│   │   ├── pg_platform.py         # DB 연결 (PlatformSessionLocal)
│   │   └── db_init_platform.py    # 앱 시작 시 스키마 정렬 + 시드
│   ├── models_admin/              # SQLAlchemy ORM 모델 (platform DB)
│   ├── models_common/             # 공통 Mixin (AuditMixin, AddressMixin)
│   ├── models_store/              # 스토어 DB 모델 (order, product, rental)
│   ├── routers/
│   │   ├── auth.py                # 인증
│   │   ├── dashboard.py           # 플랫폼 대시보드/마스터 페이지 라우트
│   │   ├── platform_store.py      # 플랫폼 관리 API (CRUD 전체)
│   │   ├── subscription.py        # 구독/인보이스 API
│   │   ├── store.py               # 스토어 워크스페이스
│   │   └── common.py              # 공용 유틸 엔드포인트
│   ├── schemas/                   # Pydantic 스키마
│   └── utils/
│       ├── jwt_handler.py
│       ├── passwords.py
│       └── safe_schema_migrate.py
├── templates/
│   ├── platform/                  # 플랫폼 어드민 UI
│   │   ├── platform_base.html     # 레이아웃 (사이드바 포함)
│   │   ├── master_management.html # 공통 마스터 CRUD 엔진 (핵심)
│   │   ├── platform_dashboard.html
│   │   └── login.html
│   ├── client/                    # 클라이언트 대시보드 (부분 구현)
│   └── store/                     # 스토어 워크스페이스 (부분 구현)
├── static/                        # CSS, JS, 이미지, Bootstrap 오프라인
├── frontend/                      # Svelte 설정 (미완성)
├── tools/                         # 개발/검증 스크립트
└── PROJECT_STATUS.md              # ← 이 파일
```

---

## 8. 알려진 이슈 / 주의사항

| 이슈 | 상태 | 비고 |
|---|---|---|
| 배치 발행은 DB 변경 작업 | 주의 | `/subscriptions/invoices/batch`는 실제 데이터에 커밋됨 |
| `httpx` 미설치 | 알림 | TestClient 사용 시 `pip install httpx` 필요 |
| 사이드바 HTML 들여쓰기 불일치 | 완료 | Pricing Plans / Invoices 메뉴 추가 시 생긴 indent 불일치 (기능에 영향 없음) |
| `tb_billing` 레거시 테이블 | 주의 | 현재 스키마에 남아있으나 `tb_invoice`로 대체됨, 향후 정리 예정 |
| `dt_started`, `dt_ended`, `i_license_id` 서버 컬럼 | 알림 | `tb_subscription`에 ORM에 없는 컬럼 존재 (기존 마이그레이션 잔재) |
