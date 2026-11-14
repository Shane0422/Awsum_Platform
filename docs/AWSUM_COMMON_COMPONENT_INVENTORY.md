# AWSUM Common Component Inventory

Recorded 2026-11-14 (base commit `cbdb362`, Common Framework P0). This is an inventory of what already exists, so
new work reuses it instead of re-creating it. It does not add or change any architecture: the authoritative
standards stay [Money](AWSUM_MONEY_INPUT_STANDARD.md), [Date](AWSUM_DATE_INPUT_STANDARD.md),
[Architecture](AWSUM_PROJECT_ARCHITECTURE.md), [Storage](AWSUM_STORAGE_STANDARD.md) and
[Installation](AWSUM_INSTALLATION_GUIDE.md). How to build a new form with these parts:
[AWSUM Form Development Standard](AWSUM_FORM_DEVELOPMENT_STANDARD.md).

Rule: **One Function, One Common Implementation, Multiple Scopes.** If a part below covers the need, use it; if it
almost covers it, extend it (with approval); never write a screen-specific copy.

## 1. Browser components (`static/js`, `static/css`, `static/components`)

| Area | Global / file | Public API | Load | Used by |
|---|---|---|---|---|
| Date | `AwsumDateInput` - `static/js/date.js`, `static/css/awsum-date.css` | `format, formatDateTime, parse, isValidIso, validate, today, mask, bind, bindAll, setRules, setValue, getValue, check, open, close`. Markup: `<input type="text" data-awsum-date data-date-label=".." [data-date-required="true"]>` then `bindAll(container)`. Screen `MM-DD-YYYY`, API ISO | `platform_base.html` | Master Group (all), Employee, Child Grid |
| Money | `AwsumMoneyInput` - `static/js/money.js` | `format, normalize, toMinorUnits, fromMinorUnits, minorUnitsOf, bind, bindAll, refreshAll, setValue, getValue, validate, destroy`. Markup `data-awsum-money` | `platform_base`, `dashboard_base`, `workspace_base` | MD, Common Maintenance (CURRENCY), Platform License / Subscription / Contract / Pricing Plan |
| Money keypad | `AwsumMoneyKeypad` - `static/js/money-keypad.js`, `awsum-money-keypad.css` | `<button class="input-group-text" data-money-keypad>$</button>` next to a money input (delegated, no binding) | same three bases | MD pricing |
| Barcode | `AwsumBarcodeInput` - `static/js/barcode-input.js` | `bind, bindAll, destroy` (scanner Enter completes the code) | `platform_base` | MD / MD Detail |
| Code / decimal | `AwsumCodeInput` - `static/js/code-input.js` | `<input data-awsum-code>` (upper case) or `data-awsum-decimal data-decimal-scale=".."`; `bindAll(container)` | `platform_base` | Location, Item Kind, Common Maintenance, Category III |
| Option group | `AwsumOptionGroup` - `static/js/option-group.js`, `awsum-option-group.css` | `html, bind, bindAll, getValue, setValue, setOptionLabel, setInherited, setRequired, setDisabled` (radios / checkboxes, inherit / override) | `platform_base` | MD, Category III, Location |
| Address / ZIP | `AwsumAddress` - `static/js/awsum-address.js` | `US_COUNTRY_NAME, isUsCountry, lookupZip(apiBase, zip), autoFillFromZip(opts), bindZipAutoFill(opts)`; opts = field ids + `apiBase` + callbacks `onAddressChange / onFilled / afterFilled` | per template | Agency, Merchant, Store (Platform + Merchant Portal), Employee |
| Map | `AwsumMap` - `static/js/awsum-map.js` | `create(container, {fields, configUrl})` -> `show, hide, reset, setAddress, openExternal, isVisible`; static `formatAddress, isComplete`. Config `GET {apiBase-root}/map-config` | per template | Employee ADDRESS |
| Confirm | `AwsumConfirm` - `static/js/confirm-modal.js`, `awsum-confirm-modal.css` | `await AwsumConfirm.show({title, message, variant, details, choices, value, okText, cancelText})` -> `{confirmed, choice}` | per template | Master Group (delete, deactivate, Category III scope), Employee |
| Toast | `AwsumToast` - `static/js/awsum-toast.js`, `awsum-toast.css` | `success, info, warning, error, done(action, label), message(action, label), flash, clear` | all three bases | every screen |
| Lookup | `AwsumLookup` - `static/js/lookup-modal.js`, `awsum-lookup.css` | `await AwsumLookup.open({title, subtitle, fixed, searchPlaceholder, filters, columns, fetch})`; `fetch(params)` -> `{items, total, page, pages}` | per template | MD / Category III location pickers |
| Page actions | `AwsumPageActions` - `static/js/page-actions.js` | `mount(el, {mode, actions:[{id, modes, pin, menu, danger, permission}]})` -> `setMode, setPermissions, setEnabled, refresh, layout`; adopts the existing buttons | `master_group.html`, `management_layout.html` | Master Group (all entities) |
| Master-detail tree | `AwsumMasterDetail` - `static/js/master-detail.js` | `treeHtml, treePathKeys, expandTree, toggleTree, fitWorkspace` | `platform_base`, `master_management` | Master Group trees, Platform Agency / Merchant |
| Images | `AwsumImages` - built bundle `static/components/image-uploader/image-uploader.js` + `.css` (source `shared/image-components/src`) | `createUploader(target, opts), createAvatar, updateCurrentAvatar, bindImageInput, adjustImage` | `platform_base` | MD, Employee, Users, Agency / Merchant / Store logos |
| Reports / print | `AwsumReports` - `static/js/report-print.js` (+ `static/css/report-print.css`) | `printHtml, printTable, spreadsheetStyle` | injected by `GlobalReportMiddleware` (`backend/services/reporting.py`) | Platform Admin client export, Sessions |
| Email preview | `AwsumEmailPreview`, `AwsumStoreDevices` - `static/js/store_devices_panel.js` | registration email preview; Store device panel | `master_management.html` | Platform Agency / Merchant / Store |
| Simple form submit | `static/js/management-form.js` | `<form data-awsum-form data-method=..>`: JSON submit, error box, Toast flash, reload | `agency/portal.html` | Agency Portal |
| Session | `static/js/web-session.js` | wraps `window.fetch` (session-expired redirect via `x-awsum-login`) | bases | every screen |

## 2. Layout parts (`templates`, `static/css`)

| Part | Location | Use |
|---|---|---|
| Page header + actions slot | Macro `page_header(title, subtitle, title_id, header_id, sticky, actions_id)` in `templates/platform/components/management_layout.html` | `{% call page_header(..., actions_id='mgPageActions') %}` then `AwsumPageActions.mount` |
| Surface / table card | Macros `surface(kind, id, classes)`, `data_table(columns, rows)` (same file) | list and form panels |
| Split layout (Equal Height standard) | `static/css/awsum-master-detail.css` v=3: `.mg-md-workspace`, `.mg-md-list-panel`, `.mg-md-form-panel`, `.mg-md-form-header`, `.mg-md-pager`, `.mg-md-fit` | list left / form right, list scrolls inside, both columns equal height, stacked below 992px |
| Page frame (tabbed form body) | `.mg-md-pageframe` markup in `static/js/master_group.js` (MD and Employee tabs) + Bootstrap Tab | one grid cell for every pane, no height jump between tabs |
| List skin | `static/css/awsum-list.css` (`.awsum-list`, `.awsum-admin`, filter / table cards) | every management list |
| Form modal | Macro `form_modal(id, title_id, cancel_id, delete_id, save_id, left_actions)` in `components/form_modal.html`; `static/css/platform-modal.css` (`pm-*` classes: `pm-section`, `pm-req`, `pm-lbl-icon`) | modal forms, section heads |
| Header / navigation | Macro `header(...)` in `components/awsum_header.html`; menu data from `AppScope.navigation()` | every portal page |
| Simple editor macro | Macros `fields(inputs, values, prefix)`, `editor(...)` in `components/management_form.html` | Agency Portal |

## 3. Backend services (`backend/services`, `backend/utils`, `backend/routers`)

| Area | Location | Reuse |
|---|---|---|
| Scope / permission | `backend/services/app_navigation.py`: `resolve_app_scope(request, db, user)` -> `AppScope` (`require(action, permission, label)`, `store_read_only`, `require_store_access`, `feature_on`, `feature_states`, `navigation`); `MASTER_PERMISSION`, `STORE_MANAGEMENT_PERMISSION`, `TENANT_ROLES`; `PortalPermission` in `agency_permissions.py` | every Merchant / Store API: resolve once, then `scope.require("read"/"create"/"update"/"delete"/"export")` |
| Platform admin check | `require_platform_admin(user)` in `backend/routers/platform_store.py` | Platform-only APIs |
| Features (fail-closed) | `backend/services/features.py`: `resolve_features, feature_enabled, load_feature_snapshot, entitlement_exempt`; Store item features `store_item_features.py` | feature-gated fields and APIs |
| Master Group registry + CRUD | `backend/services/master_group.py`: `ENTITIES` (model, pk, code, name, prefix, `store_scoped`, `group_scoped`, `manual_code`, `merchant_only`, `custom`), `list_items, create_item, update_item, delete_item, lookup_options, export_table, serialize, is_in_use` | new Master: register here; rules in `_validate_payload` or a `custom` service |
| Validation helpers | same file: `_text, _decimal, _bounded_decimal, _int, _id, _bool, _validate_common, _stamp`; errors `FieldErrors(message, errors)` -> HTTP 422 `{detail, errors:{field: message}}`, `Conflict`, `NotFound` | already reused by `employees.py` and `employee_records.py` |
| Master Group routes | `backend/routers/master_group.py`: pages `/{platform,merchant,store}/master-group/{slug}` (`PAGES`, `RECORD_TITLES`), API `/{scope}/api/master-group/{entity}` (list, options, export, create, update, delete) with `_admin` scope check; entity sets `PLATFORM_ONLY_ENTITIES`, `MERCHANT_ONLY_ENTITIES`, `STORE_VIEW_ONLY_ENTITIES`, `SUPER_ADMIN_ENTITIES` | a new Master gets the whole route set by registration |
| Employee | `backend/services/employees.py` (custom entity: numbering, status, terminate / reactivate, assignments, POS PIN, Portal access), `employee_records.py` (HISTORY / BACKGROUND kinds, `record_status_event`) | example of a `custom` entity and of child records |
| Dates | `backend/services/dates.py`: `format_date`, `parse_date`, `DATE_FORMAT` | API ISO, messages / export `MM-DD-YYYY` |
| Money | `backend/services/money.py`: `format_money` | export / messages |
| Barcode | `backend/services/barcode.py`: `normalize_barcode, find_barcode_owner, barcode_conflict` | barcode uniqueness |
| Location codes | `backend/services/location_code.py`, `store_locations.py` | code normalization |
| ZIP | `backend/utils/zip_lookup.py`: `lookup_zip` (routes `GET /platform/zip-lookup/{zip}` in `platform_store.py`, `GET {API}/zip-lookup/{zip}` in `merchant_store.py`) | address autofill |
| Map | `backend/services/maps.py`: `map_client_config()`; route `GET /merchant/api/map-config` (`merchant_store.py`) | Embed key only |
| Images | `backend/services/images.py` (`read_image_upload, image_url, logo_url, authorize_images`), `image_service.py` (`commit_entity_image`, locks) | every image save |
| Reports | `backend/services/reporting.py` (`report_context, table_report_html, print_assets`, `GlobalReportMiddleware`), `report_pdf.py` | print / PDF |
| Common codes | `master_group.py`: `common_groups, common_code_query, initialize_merchant_common_codes`, `COMMON_MAINTENANCE_GROUPS` | option lists (Unit, Color, Job Grade / Title / Skill) |
| Business codes | `backend/services/business_code.py` | registration codes |
| Paths / storage | `backend/services/paths.py`, `storage_layout.py`, `resource_storage.py` | read the storage standard first |

## 4. Form engines (current state)

| | A. Platform engine | B. Master Group engine | C. Stand-alone pages | D. Jinja macro form |
|---|---|---|---|---|
| Files | `templates/platform/master_management.html` (8,970 lines, inline JS) + `store_devices_panel.js` | `static/js/master_group.js` (2,572 lines) + `templates/platform/master_group.html` + `services/master_group.py` + `routers/master_group.py` | `templates/platform/platform_stores.html`, `merchants_tree.html` | `components/management_form.html` + `static/js/management-form.js` |
| Screens | Platform: Agency, Agency Type, Business Type, Merchant, Store, Role, License, Subscription, Contract, Pricing Plan, Payment Method, Invoice, User, Session, Device Category / Type. Merchant Portal Stores (`routers/merchant_store.py` renders the same template with `merchant_store_policy`) | Platform / Merchant / Store: Tax, Category I-III, MD Master, Item Kind (Platform), Location, Common Maintenance; Merchant: Employee | `/platform/stores`, Merchants Tree | Agency Portal |
| Field definition | Declarative `masterConfig[module].fields` (`name, label, type, required, readonly, colClass, icon, options, help, default, placeholder`, `type:'section'`) rendered by `createField / renderForm` | `field()/input()/select()/textarea()` helpers, per-entity `switch` in `formHtml()`; `mdFormHtml()` and `employeeFormHtml()` hand-written. Child Grid `fields` declarative | hand-written HTML | macro `inputs` |
| Fill / collect | `populateForm / getFormData` (config-driven) | `fillForm` (per field) / `collect()` (generic over `[name]`, money, date, option groups) | per page | `FormData` |
| List | `headers` + per-module `getRowValues`; client sort; Store server pager | `COLUMNS[entity]` + server `q/sort/direction/page/size` | per page | none |
| Validation / errors | client `validateXxxForm` + HTTP 400 text (`parseApiError`) | server `FieldErrors` 422 field map -> `showFieldErrors` + `showTabOf` | text | text |
| Permission | Platform Admin; `merchant_store_policy` + `applyMerchantStoreAccess` | `AppScope` + row `read_only / can_edit / can_delete` | Platform Admin | Agency Portal permission |
| Buttons | own `updateActionButtonsState` | `AwsumPageActions` | own | form submit |
| Date | own picker `bindUsDatePickers` (`MM/DD/YYYY`, not the standard) | `AwsumDateInput` | native `type="date"` (not the standard) | - |
| Confirm | own `showCenteredConfirm` (`appConfirmModal`) | `AwsumConfirm` (+2 `window.confirm`) | `window.confirm` | - |
| Export | client `exportCsv / exportExcel / exportPdf` (`AwsumReports`) | server `/{entity}/export` (`export_table`) | - | - |
| Dirty check | `snapshotFormState / isFormDirty` | none | none | none |

Direction (Nova, 2026-11-14): new Merchant / Store Masters are built on **B**; A is not rewritten; A's declarative
field definition and the Child Grid's configuration style are the model for B's form definition.

## 5. Duplicate implementations (same function, more than one implementation)

| # | Function | Implementations | Standard to keep | Notes |
|---|---|---|---|---|
| 1 | Date input / picker | `AwsumDateInput`; A `formatDateForDisplay ... bindUsDatePickers` (~300 lines, slash format); `platform_stores.html` native `type="date"` x3 | `AwsumDateInput` | A and C violate the Date standard (open item) |
| 2 | Confirm dialog | `AwsumConfirm`; A `showCenteredConfirm`; `window.confirm` in `master_group.js` (MD Detail delete, list delete) and `platform_stores.html` | `AwsumConfirm` (built as the shared copy of A's modal) | |
| 3 | Field definition / rendering | A `masterConfig` + `createField`; B `field()` + switch; Child Grid `fields` + `control()`; D macro `fields` | B `field()` + one definition format (P1) | four formats |
| 4 | Tabs | Employee tabs, MD tabs (`.mg-md-pageframe`), A `createMerchantTabs`, `merchantStoreModal` tabs, `jumpToStoreErrorTab` | to be decided (P1 / P2: promote the B tab markup) | no common Tab part |
| 5 | Error contract | B 422 `{detail, errors}`; A 400 `{detail: text}` + client validators; D text | B `FieldErrors` | A cannot point to a field |
| 6 | List sort / paging | B server paging; A client `getSortedMasterItems` + Store `setupStoreGridControls`; `store_devices_panel.js` own sort | B server contract | |
| 7 | Export | B server `export_table`; A client `exportCsv / exportExcel / exportPdf` | keep both for now; new Masters use B | |
| 8 | Button state | `AwsumPageActions`; A `updateActionButtonsState / updateListCentricActionButtons`; C own | `AwsumPageActions` | |
| 9 | Store form | A `store` module; A `merchantStoreModal` (`populateMerchantStoreEditor / collectMerchantStorePayload / saveMerchantLinkedStore`); `platform_stores.html` | A `store` module (one form, Platform + Merchant scopes) | consolidation is P4, needs a decision |
| 10 | In-form message | B `showMessage`, A `showMasterModalError`, Child Grid `message` | - | small |
| 11 | Boolean parse (backend) | `master_group._bool`, `employees._flag` | `_bool` | small |
| 12 | Address field set | field list repeated in A (Agency, Merchant, Store) and B (Employee) | behaviour is common (`AwsumAddress`); only the definitions repeat | candidate for a shared definition |

Not duplicated (already common): ZIP lookup (A delegates to `AwsumAddress`), money input, images, toast,
master-detail tree, split layout CSS, scope / feature checks.

## 6. Not common yet

- Tab / Section component (see 5.4); Loading indicator (no standard); dirty-state check in B; generic Audit Log
  (HISTORY exists for Employee only); field-level permission as a definition attribute (today `can_view_cost`,
  `data-item-feature`); mobile viewport meta (deferred common UI task).
