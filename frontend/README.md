# frontend

Reserved for AWSUM **client applications only**:

```text
frontend/
├── pos/                 # POS app (future)
├── kiosk/               # KIOSK app (future)
└── customer-ordering/   # Customer Ordering app (future)
```

Rules:

- Do not put Central Server pages, server images, or built-in Platform assets here.
  - Server HTML: `templates/`
  - Server web assets and built-in images: `static/` (`static/images/platform/`)
  - Shared backend-owned image components: `shared/image-components/`
- Each client app keeps its own `package.json`, build output, and `node_modules`.
- Read `docs/AWSUM_PROJECT_ARCHITECTURE.md` before adding anything here.
