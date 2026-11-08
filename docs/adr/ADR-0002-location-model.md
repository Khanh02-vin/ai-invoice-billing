# ADR-0002: Location Model — Tách Payment Location / Merchant Location / Purchase Context

- **Status**: Accepted
- **Date**: 2026-11-07

## Decision

Mỗi giao dịch/mua hàng có thể lưu **3 lớp vị trí độc lập**, không phải một trường duy nhất:

1. **PaymentLocation** — nơi thực hiện thanh toán:
   - `payment_lat`, `payment_lon`
   - `payment_captured_at` (ISO 8601 UTC)
   - `payment_accuracy_m` (độ chính xác GPS)
   - `payment_source` (gps | network | fused | manual)
   - `payment_provider` (device gps chip, google fused, etc.)

2. **MerchantLocation** — vị trí cửa hàng:
   - `merchant_poi_id` (nullable)
   - `merchant_name`, `merchant_address`
   - `merchant_lat`, `merchant_lon`
   - `merchant_source` (poi_match | user_input | receipt_address | osm | google)
   - `merchant_confidence` (0.0–1.0)
   - `merchant_verified_by_user` (bool)

3. **PurchaseContext**:
   - `purchase_context_type` (physical | online | remote)
   - `evidence_refs` (JSON list of evidence IDs)
   - `user_confirmed_location` (bool)

## Rationale

- User có thể mở app ở nhà nhưng giao dịch tại cửa hàng → payment ≠ merchant.
- GPS accuracy varies; cần lưu để đánh giá độ tin cậy POI matching.
- 50m radius không đủ; cần kết hợp name/address/time/confidence.
- Map nên ưu tiên merchant_location (personal expense map), payment_location cho trajectory.

## Backward compatibility

- Giữ các cột `location_lat/lon/name/address/captured_at` cũ (deprecated) trong Phase 1.
- Backfill: `location_lat` → `payment_lat` (nếu captured_at có) hoặc `merchant_lat` (nếu không).
- Migration idempotent: thêm cột mới nếu chưa có; không xóa cột cũ.

## Consequences

- DB schema lớn hơn nhưng linh hoạt.
- POI matching logic cần implement ở Phase 3.
- UI cần hỗ trợ user sửa merchant location.
