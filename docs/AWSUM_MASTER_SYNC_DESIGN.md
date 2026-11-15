# AWSUM Master Sync Design (P1, 설계안)

> **상태: 설계안 (Draft, 2026-11-15, Store Override 정책 반영).** 이 문서는 구현 전 합의용 설계다. Sync / Change Log /
> Store Override의 DB Migration, API, 화면은 아직 없다. 구현은 단계별 승인 후 진행한다 (P2 MD + Default Detail
> 원자적 저장은 9절).
>
> 관련 문서: [AWSUM STORE SERVER ARCHITECTURE](AWSUM_STORE_SERVER_ARCHITECTURE.md) (Data Authority, Offline,
> Store Server), [AWSUM FORM DEVELOPMENT STANDARD](AWSUM_FORM_DEVELOPMENT_STANDARD.md),
> [AWSUM COMMON COMPONENT INVENTORY](AWSUM_COMMON_COMPONENT_INVENTORY.md).

## 1. 목표와 원칙

Restaurant, Deli, Supermarket의 모든 POS / KIOSK가 **하나의 Common Master Sync Engine**으로 Central의
Master Data를 자기 **Local SQLite Master DB**에 받아 쓴다.

1. 모든 POS는 자체 Local Master DB로 판매한다 (Central / Store Server 장애와 무관하게 조회).
2. **POS 최초 로그인 시** 변경분만 자동 다운로드한다 (판매 시작 전).
3. 운영 중 Central에서 변경이 생기면 POS 상단에 **알림만** 표시한다.
4. 변경분 적용은 **사용자가 수동 다운로드를 선택할 때만** 한다.
5. **판매 중 자동 Master 업데이트는 금지**한다 (열린 거래가 있으면 수동 다운로드도 막는다).
6. Merchant / Store Scope를 서버가 강제한다. 다른 Merchant의 데이터는 어떤 경로로도 내려가지 않는다.
7. 업종별 차이는 Store Options / Item Features / Item Kind 같은 **데이터**로 표현한다. 업종별 동기화 코드는 없다.
8. Master는 **Central Authority** (아키텍처 문서 6절). POS / Store Server는 Master를 수정하지 않는다.
9. 동기화 위치(Cursor)는 **시각이 아닌 단조 증가 번호**로 한다. 장비 시계와 무관하다.
10. 기존 Common Service / DB 구조를 재사용한다. 새 조회 로직, 새 범위 규칙을 만들지 않는다.

## 2. 구조

```text
Cloud (Central Platform, PostgreSQL)
   │  Master Change Log (단일 Cursor) + Snapshot
   ▼  HTTPS, 장비 인증 (S2 Device Token) + S1 Rate Limit / Store·Merchant Active
Store Server (Store LAN, Local Store DB)          ← 아직 미구현 (아키텍처 문서)
   │  같은 Sync 계약 (LAN)
   ▼
POS / KIOSK (Local SQLite Master DB)
```

- Store Server는 Central과 같은 Sync 계약을 LAN에 제공한다 (Central의 Cursor를 그대로 중계, 자체 Master 수정 없음).
- **Store Server 구현 전**: POS가 Central에 직접 같은 계약으로 연결한다. 계약이 같으므로 Store Server 도입 시 POS
  변경은 연결 대상 주소뿐이다.
- Store Server 오프라인 인증, Primary Store Server 승격은 별도 설계 (이 문서 범위 밖).

## 3. 동기화 대상 (Master Catalog)

POS용 필드만 내려보낸다 (Projection). 원가(Cost), Platform 전용 필드(P1~P3, Billing, License 필드), 내부 메모는
**제외**한다.

| Entity | 원본 | 소유 범위 | POS용 필드 (요약) | 비고 |
|---|---|---|---|---|
| `item_kind` | `tb_item_kind` | 전체 공용 (Platform System Master) | System Code, 이름, 순서, 활성 | MD는 System Code로 참조. 로직 정의된 코드만 (`SYSTEM_ITEM_KIND_CODES`) |
| `tax` | `tb_tax` | Merchant | 코드, 이름, 세율, 활성 | |
| `category1/2/3` | `tb_category1/2/3` | Merchant | 코드, 이름, 순서, 상위 ID, 활성. III: `tax1~3`, 재고 유형 / 보관, 권장 위치 | |
| `md` | `tb_md_master` | Merchant (`i_owner_key`) | 품목 코드 / 이름 / 2차명, Item Kind, Category I~III, 단위, 판매가, 재고 유형 / 보관 (상속 계산 결과 포함), 출력 장치 목록, 권장 위치 (상속), 이미지 버전, 활성 | `n_cost`, P1~P3, 과금 / 라이선스 제외 |
| `md_detail` | `tb_md_detail` | MD 소유자 | Detail No, Default 여부, 바코드 (정규화), 단위, PK / Size, 가격, Modifier, Color 코드 | 원가 제외 |
| `store_md` | `tb_store_md` (예정, 3.1절) | Store (그 Store 행만) | MD ID, POS 노출 (`b_pos_visible`) | 행이 없으면 노출 |
| `store_md_detail` | `tb_store_md_detail` (예정, 3.1절) | Store (그 Store 행만) | Detail ID, Store 가격 (NULL = Merchant 가격 상속) | 행이 없으면 Merchant 가격 |
| `common_code` | `tb_common_code` | Merchant (UNIT, COLOR) | 그룹, 코드, 이름, 값, 순서, 활성 | Merchant 첫 초기화로 복사된 코드 |
| `store_options` | `tb_store` Option 컬럼 + Feature Registry (`services.features`) | Store | Business Options / Item Features **유효 상태** (Common Feature Resolver 결과) | 판정은 서버 공통 Resolver, POS는 결과만 사용 |
| `store_profile` | `tb_store` | Store | 영수증 정보, 기본 세율, Time Zone, Store 상태 | Platform 전용 / 내부 필드 제외 |
| `store_location` / `md_location` | `tb_store_location`, `tb_store_md_location` | Store | 위치 코드 / 이름 / 구역, MD Detail ↔ 위치 | Location Management 사용 Store만 |
| `employee` | `tb_employee` 등 | Merchant + Store 배정 | 기존 설계 (아키텍처 문서: PIN 해시, `i_row_version` / `i_pin_version`) | 같은 엔진의 별도 Entity로 편입 |
| `keymap` | **없음** | Store (예정) | POS 버튼 / 화면 배치 | **현재 DB / API 없음.** 신규 설계 필요 (8절 미결) |
| `modifier` | 현재 `tb_md_detail.c_modifier` (자유 문구) | MD 소유자 | 문구 그대로 | 선택형 Modifier Group / Option Master는 미구현 (8절 미결) |
| 이미지 | `c_image_path` + 공통 이미지 URL | 소유자 | `image_version` (경로 해시) | 바이너리는 별도 다운로드 (이미지 버전이 바뀔 때만) |

- POS용 필드 계산은 기존 `services.master_group.serialize()` / 상속 계산 함수 (`inventory.resolve_inventory`,
  `resolve_md_location`) / `output_devices_list` / Feature Resolver를 재사용하고, 그 결과에서 POS 필드만 추린다.
- **세금 적용 규칙 (확정)**: POS 세금은 **Category III의 Tax 1~3만** 적용한다. MD의 `i_default_tax_id` (화면에서
  숨겨진 legacy 저장값)는 POS 세금에 적용하지 않는다 (이중 적용 금지). Store별 Tax Override는 없다.

### 3.1 Store Override (가격 / POS 노출)

원칙: **Merchant는 상품 정보를 공유하고, Store별 가격과 POS 노출 설정은 완전히 독립적이다.**

- Merchant 공통 MD Master / MD Detail은 **하나만** 유지한다. Store별 MD 복사본은 만들지 않는다.
- Store 설정은 **기본값과 다를 때만** 행으로 저장한다. 기본값으로 되돌리면 행을 삭제한다.
  - `tb_store_md` (Store × MD): `b_pos_visible` (기본 true). Unique(Store, MD).
  - `tb_store_md_detail` (Store × MD Detail): `n_price` (NULL = Merchant 가격 상속, **$0.00은 유효한 Override**).
    Unique(Store, Detail).
  - 두 테이블 모두 `i_owner_key` (MD 소유자와 같은 Merchant, 복합 FK)와 공통 `AuditMixin`을 둔다. MD / Detail
    삭제 시 함께 삭제한다 (CASCADE).
- **적용 규칙 (서버 공통 함수 하나, POS는 같은 규칙)**
  - Detail 적용 가격 = Store Detail 가격 (NULL이 아니면) 또는 Merchant Detail `n_price`.
  - MD 적용 가격 = Default Detail의 적용 가격.
  - POS 버튼 / 목록 표시 = 공통 MD Active **그리고** Store MD `b_pos_visible` (행이 없으면 true).
  - **POS Hidden은 판매 차단이 아니다.** 숨긴 MD도 바코드 스캔으로 판매한다. Hidden은 MD 수준만 지원한다.
- **판매 중지는 이 범위에서 제외한다.** Merchant 전체 / 특정 Store, MD / Detail 단위 판매 차단은 향후 Product
  Policy → Block Sales에서, Recall은 별도 Recall Service에서 다룬다.
- **Audit 분리**: 공통 MD / Detail 변경은 MD / Detail 행의 AuditMixin, Store Override 변경은 Override 행의
  AuditMixin에만 기록한다 (Store 가격 변경이 공통 MD의 Updated By / At을 바꾸지 않음). 기록자는 서버 로그인 사용자.
- **권한**: Merchant Portal은 소속 모든 Store의 Override를 조회·관리한다 (다른 Merchant의 Store는 404, Inactive
  Store는 공통 규칙대로 읽기 전용). Store Portal은 자기 Store의 Override만 (Store ID는 로그인에서만 결정).
  Store Portal의 공통 MD 등록·수정은 Store Business Options의 **"Allow Store to Manage Merchant MD"** (기본 OFF)와
  기존 Common Permission (`AppScope.require`)을 함께 확인한다 (Store Override 단계에서 구현). 다른 Merchant는 차단.

## 4. Scope (Merchant / Store 격리)

- 동기화 요청의 범위는 **장비가 속한 Store** (S2 Device Token으로 인증된 장비 → `tb_device.i_store_id`)에서만
  정한다. 요청에 담긴 Merchant / Store ID는 쓰지 않는다.
- Merchant 범위 데이터: 그 Store의 Merchant 소유분만 (`_scoped_query`, MD는 `i_owner_key = merchant_id`).
  Platform MD (owner 0)는 내려보내지 않는다 (AWSUM 자체 상품용).
- Store 범위 데이터: 그 Store의 것만 (Store Options, Location, Store Override `store_md` / `store_md_detail`, KeyMap).
  Store A의 Override는 Store A 장비에만 내려간다. B / C Store의 POS SQLite에는 들어가지 않는다.
- 전체 공용: Item Kind (System Master)만.
- Store / Merchant가 Active가 아니면 S1 규칙대로 403 (`services.device_access.require_operational_store`).
- 테스트는 다른 Merchant의 Store 장비로 요청했을 때 어떤 Entity도 섞이지 않는지 고정한다.

## 5. 변경 기록, 삭제, Cursor

### 5.1 변경 기록 (Master Change Log, Migration 필요)

공통 테이블 `tb_master_change_log` (제안):

| 컬럼 | 의미 |
|---|---|
| `i_change_seq` (BIGINT, PK, 증가) | **Cursor**. Central 전체에서 단조 증가 |
| `c_entity` | `md`, `md_detail`, `category3` … |
| `i_entity_id` | 원본 행 ID |
| `i_owner_key` | Merchant 범위 키 (Platform 0, 공용 Entity는 NULL) |
| `i_store_id` | Store 범위 Entity의 Store (그 외 NULL) |
| `c_op` | `U` (생성 / 수정 / 활성 변경) 또는 `D` (삭제) |
| `dt_changed`, `i_changed_by` | 기록용 (Cursor로 쓰지 않음) |

- **기록 위치**: Master 쓰기는 이미 공통 서비스 함수를 거친다 — `services.master_group` 의 `create_item` /
  `update_item` / `delete_item`, MD Detail 함수, Category III 일괄 적용, Common Code 저장, Store Option 저장,
  Location 저장, Store Override 저장 (`i_store_id` 함께 기록). 이 함수들이 **같은 트랜잭션 안에서** 변경 기록을 남긴다 (한 곳의 공통 함수
  `record_master_change(...)`). 일괄 UPDATE (Category III → MD)는 영향받은 ID마다 기록한다.
- **기록 순서 = 커밋 순서**: 동시 트랜잭션이 번호 순서와 다르게 커밋되면 POS가 앞 번호를 건너뛸 수 있다.
  변경 기록을 남길 때 소유자 단위 Advisory Lock (트랜잭션 범위)을 잡아 같은 소유자 안에서는 번호 순서와 커밋
  순서를 같게 한다. 응답은 "안전 Watermark" (모든 앞 번호가 커밋된 최대 번호) 이하만 내보낸다.
- 행마다 `i_row_version` 을 두는 방식 (Employee 선례)보다 **변경 기록 한 테이블**을 권장한다. 삭제와 일괄
  변경까지 하나의 Cursor로 다룰 수 있기 때문이다. Employee도 같은 Change Log로 편입할 수 있다.

### 5.2 삭제

- 원본 삭제는 기존처럼 물리 삭제를 유지해도 된다. 삭제 시 `c_op = D` 기록이 **삭제 흔적 (Tombstone)** 역할을 한다.
- Store Override 행 삭제 (기본값으로 복귀)도 `D`로 기록한다. POS는 그 행을 지우고 Merchant 가격 / 노출 기본값을 쓴다.
- 비활성화 (`b_active = false`)는 `U`. POS는 비활성 항목을 판매 화면에서 숨기고 과거 거래 표시용으로 보관한다.
- 변경 기록 보관 기간 (예: 90일, 설정)이 지나면 정리한다. POS의 Cursor가 보관 범위보다 오래되면 Full Snapshot.

### 5.3 Cursor

- POS / Store Server는 **마지막으로 적용한 `i_change_seq`** 를 저장한다 (`sync_state`).
- Cursor는 시각이 아니므로 장비 시계, Time Zone, 서머타임과 무관하다.

## 6. 동기화 계약 (제안)

| 요청 | 응답 | 용도 |
|---|---|---|
| `GET …/sync/master/status` | `{latest_cursor, schema_version}` | 변경 여부 확인 (알림 표시용, 가벼움) |
| `GET …/sync/master/changes?cursor=N&limit=M` | `{changes:[{seq, entity, id, op, data}], next_cursor, has_more}` 또는 `{snapshot_required:true}` | 변경분 다운로드 (`data`는 POS 필드, `D`는 ID만) |
| `GET …/sync/master/snapshot?entity=E&after_id=K&limit=M` | `{rows, next_after_id, snapshot_cursor}` | 최초 설치 / 재구축 / 보관 범위 초과 |

- 응답은 범위 (4절) 안의 변경만 포함한다. 범위 밖 변경은 Cursor만 지나간다 (`next_cursor`).
- Snapshot은 시작 시점의 `latest_cursor` 를 `snapshot_cursor` 로 함께 주고, 완료 후 그 Cursor부터 변경분을 받는다.
- 인증: S2 Device Token. 제한: S1 Rate Limit (`device_access.enforce_rate_limits` 에 `master_sync` 정책 추가).
- `schema_version`: Projection 형식 버전. POS가 지원하지 않는 버전이면 다운로드를 멈추고 앱 업데이트를 안내한다.

### 6.1 POS 동작 흐름

```text
[POS 로그인]
  status → latest_cursor > local_cursor ?
     예 → changes 자동 다운로드 (판매 시작 전, 진행률 표시) → 적용 → 로그인 완료
     오류 → 기존 Local Master로 로그인 허용 + "Master 업데이트 실패" 알림 (판매 계속 가능)

[운영 중] (주기적 status 확인 또는 Store Server 알림)
  latest_cursor > local_cursor → 상단 알림만 표시 ("Master 변경 N건, 다운로드 가능")
  자동 적용 없음

[사용자가 수동 다운로드 선택]
  열린 거래 / 결제 진행 중 → 거절 ("판매 종료 후 다운로드")
  아니면 → 다운로드 → 적용 → 알림 해제
```

### 6.2 적용 (POS Local SQLite)

1. 받은 변경을 **Staging 테이블**에 모두 받는다 (`has_more`가 없을 때까지, 페이지별로 이어받기 가능).
2. 검증: 참조 무결성 (Detail의 MD, MD의 Category / Item Kind / Tax), Projection 형식, 건수.
3. **하나의 SQLite 트랜잭션**으로 Upsert (`U`) / Delete (`D`) 적용 후 `sync_state.cursor` 갱신.
4. 실패하면 트랜잭션을 되돌리고 기존 Master를 그대로 쓴다. Staging은 다음 시도에 재사용 (멱등).

## 7. 오류 복구

| 상황 | 처리 |
|---|---|
| 네트워크 중단 (다운로드 중) | Staging에 받은 페이지부터 이어받기. 적용 전이라 판매 영향 없음 |
| 적용 중 오류 / 앱 종료 | SQLite 트랜잭션 롤백 → 기존 Master 유지, 같은 Cursor부터 재시도 (Upsert / Delete 멱등) |
| 참조 무결성 오류 (서버 데이터 불일치) | 적용 중단, 오류 보고 (장비 로그), Full Snapshot 권장 |
| Cursor가 보관 범위보다 오래됨 | `snapshot_required` → Snapshot 재구축 |
| Local DB 손상 | Snapshot 재구축 (판매 데이터와 분리된 Master DB 파일) |
| `schema_version` 불일치 | 다운로드 중단, 앱 업데이트 안내 |
| 장비 인증 실패 / Store 비활성 / 429 | 기존 Master로 판매 계속 (오프라인 유예 정책 별도), Retry-After 존중 |

## 8. 미결 사항 (승인 필요)

1. **Store Portal의 Tax / Category I~III 쓰기**: 현재 Store 로그인이 생성·수정·삭제 가능. Sync는 Central
   Authority라 영향은 없지만 권한 정책 결정 필요 (MD는 3.1절 "Allow Store to Manage Merchant MD"로 결정됨).
2. **KeyMap**: POS 버튼 / 화면 배치 Master의 데이터 모델과 관리 화면 (현재 없음).
3. **Modifier**: 선택형 Modifier Group / Option / 가격 Master 필요 여부 (현재 Detail 자유 문구).
4. **가격 변경 이력**: AuditMixin의 마지막 수정 정보로 충분한지, 이력 테이블이 필요한지.
5. **`tb_store_sync_status`** (현재 미사용 모델) 를 Store별 동기화 상태 기록에 쓸지.
6. **변경 기록 보관 기간**, 운영 중 status 확인 주기, 이미지 동기화 방식 (URL + 버전, 용량 제한).
7. Store Server 도입 시 Cursor 중계 / 다중 POS 동시 다운로드 부하.

결정됨: 세금 = Category III Tax 1~3만 (3절). Store별 가격 / POS 노출 = 3.1절. 판매 중지 = 범위 제외 (Product
Policy → Block Sales, Recall Service에서 향후).

## 9. 단계별 구현 (승인 후)

| 단계 | 내용 | DB |
|---|---|---|
| P2 | MD + Default Detail 저장을 한 트랜잭션으로 (`default_detail`, 기존 요청 호환) | 없음 |
| SO | Store Override (3.1절): 테이블 2개, 공통 서비스 / 적용 계산 함수, Merchant / Store API와 화면, "Allow Store to Manage Merchant MD" Option | Migration |
| P3 | `tb_master_change_log` + 공통 `record_master_change` (서비스 쓰기 함수 + Store Override 연결) | Migration |
| P4 | 세금 / 적용 가격 / POS 노출 계산 공통 함수를 Sync Projection에 연결 | 없음 |
| S2 | Device Token (Activation Security) | Migration |
| P5 | Sync API (status / changes / snapshot), Scope 테스트, Rate Limit 정책 | P3 의존 |
| P6 | POS Local SQLite Sync Engine (POS 앱 저장소) | POS |

테스트 원칙: 다른 Merchant / Store 장비로 어떤 Entity도 섞이지 않음, 삭제 전달, 일괄 변경 전달, Cursor 이어받기,
멱등 재적용, Snapshot 후 변경분 연결, 판매 중 적용 금지, 비활성 Store 403, 429, Store A Override가 B / C에
내려가지 않음, $0.00 / NULL 구분, Override 삭제 (기본값 복귀) 전달.
