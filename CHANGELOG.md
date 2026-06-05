# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project aims to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Webhook deliveries audit: every outbound webhook call is recorded as a
  `WatchActionDelivery` (one row per HTTP attempt) with its state, HTTP status,
  method, item count, redacted target host, dedup key, and the exact request
  body sent (no headers). View it with `magpie watch action deliveries
  <action_id>` or `GET /v1/actions/<action_id>/deliveries`. Each run links to
  the call that carried it via `delivery_id`.
- Webhook actions support an HTTP `method` of `POST`, `PUT`, or `PATCH`
  (default `POST`).

### Changed

- The webhook payload is now one self-describing shape for both instant and
  digest delivery:
  `{watch: {id, name}, action_id, delivery, window, items: [{key, score, source: {label, kind}, item}]}`
  (instant is a one-item batch with `window` null). Each item now carries the
  source it came from and the upstream semantic-filter score. This REPLACES the
  previous instant `{action_id, item}` and digest `{action_id, items: [{key,
  item}]}` shapes; receivers must adopt the unified shape.

### Fixed

- Digest webhooks no longer re-POST a batch that already landed when a worker
  crashes after the POST but before recording completion (delivery-level dedup
  on the request key).
