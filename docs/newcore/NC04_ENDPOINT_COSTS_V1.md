# NC-04 Binance USD-M endpoint costs v1

**Table version:** `binance-usdm-v1-2026-11-03`

**Retrieved:** 2026-11-03

**Scope:** NC-04 account-worker calls only; unlisted routes refuse before dial.

Official primary sources:

- [USD-M REST Trade API](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/trade)
- [USD-M REST Account API](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/account)
- [USD-M General Info](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/general-info)

| Worker operation | Method and exact route | IP/request weight | Order count 10s | Order count 1m |
|---|---|---:|---:|---:|
| `SUBMIT_MARKET` classic | `POST /fapi/v1/order` | 0 | 1 | 1 |
| `SUBMIT_STOP` algo | `POST /fapi/v1/algoOrder` | 0 | 1 | 1 |
| `CANCEL` classic | `DELETE /fapi/v1/order` | 1 | not stated | not stated |
| `CANCEL` algo | `DELETE /fapi/v1/algoOrder` | 1 | not stated | not stated |
| `QUERY` classic | `GET /fapi/v1/order` | 1 | not stated | not stated |
| `QUERY` algo | `GET /fapi/v1/algoOrder` | 1 | not stated | not stated |
| `POSITIONS` | `GET /fapi/v2/positionRisk` | 5 | not stated | not stated |
| symbol `OPEN_ORDERS` | `GET /fapi/v1/openOrders` | 1 | not stated | not stated |
| `FILLS` | `GET /fapi/v1/userTrades` | 5 | not stated | not stated |

No-symbol `GET /fapi/v1/openOrders` costs 40 but is forbidden by NC-04.  As a
conservative Codex policy rather than a Binance fact, cancellation reserves one
local token in each order-count window.  Dynamic request-weight limits come from
`/fapi/v1/exchangeInfo`; account order limits come from signed
`GET /fapi/v1/rateLimit/order` (weight 1).  Examples are never defaults.
NC-04 accepts these values only through the typed raw-response loader pinned to
the official Market Data and Account documentation. It requires exactly one
`REQUEST_WEIGHT`/`MINUTE`/`1` row plus exactly one `ORDERS` row for each of
`SECOND`/`10` and `MINUTE`/`1`; missing, duplicate or non-integer limits refuse
worker construction as `limits_unknown`. Raw row fields use exact JSON types;
in particular, booleans never satisfy integer `intervalNum` or `limit` fields.
`AccountBudget` accepts and reparses only these two raw, source-pinned payloads.
Derived ceilings remain private module values; legacy supplied-ceiling and
public-helper paths refuse rather than treating a caller value as authentic.

`Retry-After` is optional evidence, not a current USD-M documentation guarantee.
Missing or malformed required limits/evidence remain unknown, never zero.
