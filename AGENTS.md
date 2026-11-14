# AWSUM project standards

- Before adding or changing any monetary input or monetary presentation, read
  [AWSUM MONEY INPUT STANDARD](docs/AWSUM_MONEY_INPUT_STANDARD.md).
  Every current/future monetary field MUST reuse `AwsumMoneyInput` and the common
  formatter. Never create screen-specific money inputs/masks/formatters.
  Presentation is `#,##0.00`; DB/API values stay comma-free numeric/decimal.
  Percentages, quantities, IDs, codes and display order are not money.
- Before adding or changing any date input, date display, date export or date
  message, read [AWSUM DATE INPUT STANDARD](docs/AWSUM_DATE_INPUT_STANDARD.md).
  Screens use `MM-DD-YYYY` (date-time `MM-DD-YYYY hh:mm AM/PM`) through
  `AwsumDateInput` (`static/js/date.js`) and `backend/services/dates.py`;
  API stays ISO `YYYY-MM-DD`, DB stays DATE. Never create native date inputs,
  screen-specific date masks, pickers or formatters.
- Before path, storage, filesystem, images, uploads, owner-path, project folder,
  `frontend`/`static`/`templates`/`shared` placement, installation, migration or
  backup/restore changes, read all three:
  [AWSUM PROJECT ARCHITECTURE](docs/AWSUM_PROJECT_ARCHITECTURE.md),
  [AWSUM STORAGE STANDARD](docs/AWSUM_STORAGE_STANDARD.md) and
  [AWSUM INSTALLATION GUIDE](docs/AWSUM_INSTALLATION_GUIDE.md).
- Folder roles: `backend` = server logic, `templates` = server HTML, root
  `static` = server web assets and built-in images (`static/images/platform`),
  `shared/image-components` = common image component source, `frontend` = POS /
  KIOSK / Customer Ordering client apps only, `Awsum_Data` = runtime data.
  Never create `backend/static` or put server assets in `frontend`.
- Before Store Server, Offline Operation, Sync, Local DB, Device Role or Device
  License work, read [AWSUM STORE SERVER ARCHITECTURE](docs/AWSUM_STORE_SERVER_ARCHITECTURE.md).
- Before adding or changing any management form, list, Master or its API, read
  [AWSUM FORM DEVELOPMENT STANDARD](docs/AWSUM_FORM_DEVELOPMENT_STANDARD.md) and
  reuse the parts in [AWSUM COMMON COMPONENT INVENTORY](docs/AWSUM_COMMON_COMPONENT_INVENTORY.md).
  New Merchant / Store Masters use the Master Group engine; never create a new
  form engine or a screen-specific copy of a common function.
- Do not introduce an architecture conflicting with these authoritative
  standards without explicit approval.
