# AWSUM DATE INPUT STANDARD

THIS DOCUMENT IS THE AUTHORITATIVE AWSUM DATE INPUT STANDARD (approved 2026-11-13).

Before adding or changing any date input, date display, date export or date
message, developers and coding agents MUST read this document. Do not introduce
a conflicting or screen-specific implementation without explicit approval.

## 1. Permanent rule

| Layer | Format | Example |
|---|---|---|
| Screen input and display | **`MM-DD-YYYY`** | `04-22-1961` |
| Screen date-time display | **`MM-DD-YYYY hh:mm AM/PM`** | `11-13-2026 09:06 PM` |
| Export (CSV / reports) and server messages | `MM-DD-YYYY` | `Start Date must be on or after the Hire Date (11-01-2026).` |
| API (request and response) | ISO `YYYY-MM-DD` | `1961-04-22` |
| Database | `DATE` (date-time: `TIMESTAMP`) | — |

The API and DB contract never changes with the screen format.

## 2. One common implementation

| Side | Component | Use it for |
|---|---|---|
| Browser | `static/js/date.js` → `window.AwsumDateInput`, styles `static/css/awsum-date.css` (both loaded once by `templates/platform/platform_base.html`) | every date input, picker, date display and date-time display |
| Server | `backend/services/dates.py` → `format_date()`, `parse_date()`, `DATE_FORMAT` | every date parse of a request value, every date in exports and messages |

Never write a local date mask, regular expression, `toLocaleDateString`,
`strftime("%m/%d/%Y")`, native `<input type="date">` or a page-specific date
picker. If the common component lacks something, extend the common component.

## 3. Browser API (`AwsumDateInput`)

- `format(iso)` → `MM-DD-YYYY` (`''` for empty / not a date).
- `formatDateTime(stored, empty)` → `MM-DD-YYYY hh:mm AM/PM`, read from the stored
  text exactly (no time-zone conversion).
- `parse(text)` → ISO, `''` (empty) or `null` (not a real date). Accepts
  `MM-DD-YYYY`, `M-D-YYYY`, `MMDDYYYY`, ISO and the earlier `MM/DD/YYYY`.
- `validate(value, rules)` → `''` or the message. Rules: `label`, `required`,
  `min`, `max` (`'today'` allowed), `minMessage`, `maxMessage`.
- `bind(input, rules)` / `bindAll(container)`: turns a text input into the date
  input (mask, calendar button, picker that respects min / max, blur check with
  the error in the field's `.invalid-feedback`). `bindAll` binds every
  `input[data-awsum-date]`; rules come from `data-date-label`,
  `data-date-required`, `data-date-min`, `data-date-max`, `data-date-min-message`,
  `data-date-max-message`.
- `setValue(input, iso)` shows the value; `getValue(input)` returns ISO for the API
  (the typed text when it is not a date, so the server refuses it with its message).
- `check(input)` validates in place; `setRules(input, rules)` updates rules.

Markup (any screen):

```html
<input class="form-control" name="birth_date" type="text" data-awsum-date
       data-date-label="Birthday" data-date-max="today"
       data-date-max-message="Birthday cannot be in the future.">
<div class="invalid-feedback"></div>
```

## 4. Server API (`backend.services.dates`)

- `parse_date(value)` → `date` or `None` (empty); raises `ValueError` otherwise.
  Accepts ISO (also with a time part), `MM-DD-YYYY` and `MM/DD/YYYY`.
- `format_date(value)` → `MM-DD-YYYY` (`''` for `None`).
- The error message for an invalid date is `Enter {Label} as MM-DD-YYYY.`

## 5. Screens using the standard

| Screen | Fields |
|---|---|
| Employee Master (`static/js/master_group.js`) | Birthday, Hire Date, Assignment Start, Termination Date; audit times; Employee export |
| Employee HISTORY / BACKGROUND | Record Date, Work Experience Start / End, Certification Issued / Expiration (Common Child Grid) |

Not moved yet (keep their current behaviour until their own approved step):
`templates/platform/master_management.html` (its own `MM/DD/YYYY` picker, the origin
of the common picker), `templates/platform/platform_stores.html` (native inputs) and
the Store License screen output (`backend/routers/platform_store_license.py`).
Moving a screen = `data-awsum-date` + `AwsumDateInput.bindAll()` + server
`parse_date` / `format_date`; then delete its local code.

## 6. Tests

`tools/test_date.py` (server helpers + runs `tools/test_date.cjs` with node) and
the Employee screen / API tests in `tools/test_employee_master.py`.
