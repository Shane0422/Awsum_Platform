# Awsum Platform

## Landing page routes

- `GET /` → platform landing page (`platform_home.html`)
- `GET /customer/{account_code}` → customer-branded landing page (`customer_home.html`)
  - Account matching currently uses a normalized form of `tb_account.c_account_name`.
  - If no matching account is found, a clean `customer_not_found.html` page is returned with HTTP 404.

## Auth flow

Existing auth flow is unchanged:
- `POST /auth/login`
- `POST /auth/register`

Both landing templates keep the current modal-based login/register forms with Store ID + Email + Password login.
