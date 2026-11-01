# AWSUM project standards

- Before adding or changing any monetary input or monetary presentation, read
  [AWSUM MONEY INPUT STANDARD](docs/AWSUM_MONEY_INPUT_STANDARD.md).
  Every current/future monetary field MUST reuse `AwsumMoneyInput` and the common
  formatter. Never create screen-specific money inputs/masks/formatters.
  Presentation is `#,##0.00`; DB/API values stay comma-free numeric/decimal.
  Percentages, quantities, IDs, codes and display order are not money.
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
- Do not introduce an architecture conflicting with these authoritative
  standards without explicit approval.
