# AWSUM Store Server / Offline Operation / Sync Architecture

**THIS DOCUMENT IS THE AUTHORITATIVE AWSUM STORE RUNTIME ARCHITECTURE STANDARD.**

Store Server, Offline Operation, Sync, Local DB, Device Role, Device License와
관련된 설계·구현 전에 반드시 이 문서를 먼저 읽는다.
프로젝트 폴더 구조는 [AWSUM_PROJECT_ARCHITECTURE.md](AWSUM_PROJECT_ARCHITECTURE.md),
런타임 저장소 규칙은 [AWSUM_STORAGE_STANDARD.md](AWSUM_STORAGE_STANDARD.md)를 따른다.

> 상태: 설계 기준 문서. 이 문서에 설명된 Store Server, Local Store DB,
> Sync Engine, 관리 화면은 아직 구현되지 않았다. 구현 시 이 문서를 기준으로 한다.

## 핵심 원칙

> **AWSUM Central Server는 전체 Platform의 중앙 관리 Authority이며, Primary Store
> Server는 인터넷 연결 여부와 관계없이 해당 Store가 영업을 지속할 수 있도록 하는
> Local Operational Authority이다.**

> **Store Server는 단순한 Cache가 아니라 Offline Operation과 Central
> Synchronization을 담당하는 Store-level operational component이다.**

> **Internet Offline, Central Server Offline, Store Server Failure, Device Failure는
> 서로 다른 장애 상태이며 각각 별도로 처리해야 한다.**

## 1. 기본 Architecture

```text
AWSUM CENTRAL SERVER
        │
        │ Internet
        │
        ▼
PRIMARY STORE SERVER
(Local Store DB + Sync Engine)
        │
        │ Store LAN / Wi-Fi
        │
   ┌────┼──────────────┐
   ▼    ▼              ▼
POS   KIOSK       SUBSTATION
```

각 Store는 등록된 Device 중 하나를 `Primary Store Server`로 지정할 수 있다.

```text
Store ST000001

POS #1         Device Role : FULL_POS     Store Server: PRIMARY
POS #2         Device Role : FULL_POS     Store Server: CLIENT
KIOSK #1       Device Role : KIOSK        Store Server: CLIENT
SubStation #1  Device Role : ORDER_ONLY   Store Server: CLIENT
```

- 초기 버전에서는 일반 POS 장비가 Primary Store Server 역할을 겸할 수 있다.
- 별도의 물리적 Server PC를 필수로 요구하지 않는다.
- 한 Store에는 동시에 **하나의** PRIMARY만 존재한다(7장 참조).

## 2. Central Server와 Store Server의 역할

### AWSUM Central Server — Platform 중앙 Authority

- Agency, Merchant, Store
- Subscription, Plan, Modules
- Device, Device License, License Status
- Platform / Store / Master Configuration
- Reporting / Central Management

### Primary Store Server — Store Local Operational Authority

- Local Store Database
- Local Transaction Processing
- Local Device Communication (POS, KIOSK, SubStation, KDS 등)
- Offline Store Operation
- Sync Queue / Pending Transaction Management
- Central Server Synchronization
- Local Master Cache / Local Configuration Cache

## 3. Internet Offline Operation

Internet이 끊겨도 Store LAN이 정상이면 매장 영업은 계속되어야 한다.

```text
Central Server
      X   Internet Down

Primary Store Server
      │
      ├── POS #2
      ├── KIOSK
      └── SubStation
```

- Store Server가 Local Store DB로 영업을 계속 처리한다.
- 판매, 결제정보, 환불, Cash Movement, 출퇴근, 재고 이동 등 즉시 전송할 수
  없는 데이터는 Local DB와 Sync Queue에 저장한다.
- 모든 Transaction은 Central과 중복되지 않는 **Global Unique Transaction ID**를
  가진다. 개념 필드:

```text
StoreCode
DeviceID
TransactionUUID      (전역 고유 식별자)
BusinessDate
LocalSequence        (Device/Store 내 순번)
CreatedAt
SyncStatus
```

기본 Sync Status:

```text
PENDING | UPLOADING | SYNCED | FAILED
```

## 4. Internet Recovery / Synchronization

연결이 복구되면 Store Server는 PENDING 데이터를 Central Server로 전송한다.

```text
PENDING → UPLOADING → CENTRAL ACKNOWLEDGED → SYNCED
                 └──→ FAILED (재시도 대상)
```

- Sync API는 **Idempotent**해야 한다. 같은 Transaction이 반복 전송되어도
  (TransactionUUID 기준) 중복 매출이 생기지 않는다.
- Central Server가 정상 저장 **ACK**를 반환하기 전에는 Local Transaction을
  SYNCED로 처리하지 않는다.
- FAILED는 원인을 보존하고 재시도·관리자 확인 대상으로 남긴다. 임의 삭제하지 않는다.

## 5. License Offline Grace ≠ Store Business Offline Operation

두 개념은 절대 혼합하지 않는다.

| 구분 | License Offline Grace | Store Business Offline Operation |
|---|---|---|
| 의미 | Central 미연결 시 Device License를 일정 기간 계속 인정 | Internet 단절 중 Local Store Server로 실제 영업 지속 |
| 기본값 | 약 7일 (향후 System Configuration에서 변경 가능) | 기간 정책 별도 |
| 상태 | License 검증 상태 / Grace 잔여 기간 | Sync Queue / Pending / Local DB 상태 |

```text
License Offline Grace  ≠  Store Business Offline Operation
```

두 기능은 별도의 정책, 설정, 상태값을 가진다.

## 6. Data Authority

모든 데이터를 무조건 양방향 복제하지 않는다. 데이터 종류별 Authority를 정한다.

### Central → Store (Central Authority)

License, Subscription, Plan, Module / Entitlement, Tax, Category, MD Master,
System Configuration, Store Configuration, Price / Pricing Configuration,
User Permission, Device Configuration,
Employee (Merchant Employee Master, Store assignment, POS Role, POS PIN hash — 해당 Store에 active로 배정된 직원만).
POS Login ID는 Merchant 단위 고유(같은 Merchant의 모든 Store가 하나의 ID를 공유, 다른 Merchant와는 중복 허용)이므로
Store Server는 자기 Merchant 범위의 ID만으로 오프라인 인증을 판단한다.

Employee / PIN 변경(퇴사, 배정 종료, PIN 재설정)은 Central에서만 일어나고 `i_row_version` / `i_pin_version`이
증가한다. Store Server는 증분 동기화로 반영하며, PIN 원문은 어디에도 저장하지 않는다.

Store는 이 데이터를 Local Cache로 보유하며 Central 값을 기준으로 갱신한다.

### Store → Central (Store가 생성, Central로 전송)

Sales, Payment, Refund, Void, Discount Transaction, Cash Movement,
Time & Attendance, Inventory Movement, Local Operational Event,
POS PIN 인증 성공·실패 및 잠금 Event.

### Bidirectional — Conflict Policy Required

Customer, Inventory, Selected Master Data, Store-operational configuration.

- 구현 전에 데이터별 Conflict Resolution 정책을 문서로 정의한다.
- 단순 **Last Write Wins를 무조건 적용하지 않는다.**

## 7. Primary Store Server Failure

초기 버전은 **Manual Promotion**을 우선한다.

```text
POS #1 = PRIMARY STORE SERVER
   ↓ Failure
Administrator (Platform Admin 또는 승인된 관리자)
   ↓ Promote POS #2
POS #2 = PRIMARY STORE SERVER
```

- Automatic Failover는 향후 버전에서 검토한다.
- **Split Brain 방지:** 한 Store에서 동시에 두 Device가 PRIMARY가 되어서는 안 된다.
  Promotion 시 기존 PRIMARY의 역할 해제를 보장하는 방식으로 설계한다.

## 8. 장애 상태 구분

| 장애 | 상황 | 기본 대응 |
|---|---|---|
| Internet Offline | Store LAN 정상, 외부 연결 단절 | Store Server로 영업 지속, Sync Queue 적재 |
| Central Server Offline | Internet 정상, Central 응답 없음 | Internet Offline과 동일하게 Local 영업, 복구 후 Sync |
| Store Server Failure | PRIMARY 장비 고장 | 관리자 Manual Promotion (7장) |
| Device Failure | 개별 POS/KIOSK 등 고장 | 해당 Device만 교체/재등록, Store 영업은 다른 Device로 지속 |

각 상태는 별도로 감지·표시·처리한다.

## 9. Store Server 관리 화면 (향후 UI)

Store 상세 또는 Device 관리 화면에서 최소 다음을 확인한다.

```text
Primary Store Server
Server Device
Server Status
Local Network Status
Central Server Status
Last Central Sync
Pending Sync Count
Failed Sync Count
Local DB Status
Last License Validation
Offline Grace Remaining
```

Device 등록/편집에서 지원할 개념:

```text
Device Type
Device Role
Primary Store Server YES/NO
Local DB Enabled
License Status
Last Connection
Last Sync
```

## 10. Subscription / Module / Device License와의 관계

Store Server Architecture와 상품/라이선스 Architecture는 분리하되 서로 연결한다.

```text
Business Type
    ↓
Operation Mode
    ↓
Plan
    ↓
Store Subscription
    ↓
Modules / Add-ons
    ↓
Devices
    ↓
Device Role
    ↓
Device License
```

- Inventory, Customer, Loyalty, Time Attendance, Accounting 등은 주로
  Store Subscription / Module 수준에서 관리한다.
- Full POS, Order Only, KIOSK, KDS 등 장비 역할은 Device Role / Device License
  수준에서 관리한다.
- 기능 추가 = Subscription / Entitlement 변경.
- 장비 추가 = Device 등록 + Device License 추가.
- Store Server 여부는 Device License 상품 종류와 같은 개념이 아니다.

```text
Store Server Role  ≠  Device License Product
```

Store Server는 매장 운영 Architecture상의 **역할**이다.

## 11. 장기 Architecture

```text
AWSUM CENTRAL SERVER
        │
        ▼
Merchant
        │
        ▼
Store
        │
        ├── Business Type / Operation Mode
        ├── Subscription / Plan / Modules
        │
        ├── PRIMARY STORE SERVER
        │      ├── Local Store DB
        │      ├── Sync Engine
        │      ├── Sync Queue
        │      └── Local Cache
        │
        └── Devices
               ├── POS
               ├── KIOSK
               ├── SUBSTATION
               ├── KDS
               └── Future Device Types
                      │
                      └── Device Role / License
```

## 12. Mandatory developer and coding-agent rule

Store Server, Offline Operation, Sync, Local DB, Device Role, Device License
관련 작업 전에 이 문서와 다음 문서를 먼저 읽는다.

* [docs/AWSUM_PROJECT_ARCHITECTURE.md](AWSUM_PROJECT_ARCHITECTURE.md)
* [docs/AWSUM_STORE_SERVER_ARCHITECTURE.md](AWSUM_STORE_SERVER_ARCHITECTURE.md)

명시적 승인 없이 이 문서와 충돌하는 구조를 도입하지 않는다.
