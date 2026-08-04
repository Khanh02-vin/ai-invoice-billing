# Design System — Invoice & Billing

Nguồn chuẩn cho mọi UI. AI/agent giữ giao diện nhất quán bằng cách dùng tokens dưới đây, không tự đặt màu/font mới.

## Nguyên tắc

- **Dark-first** — toàn bộ app tối (hợp react-bits Aurora/SpotlightCard)
- Màu accent xanh dương duy nhất; trạng thái dùng màu ngữ nghĩa cố định
- Spacing theo thang 4px; radius cố định 6/10/16px

## 1. Màu (Color)

| Token | Giá trị | Dùng cho |
|-------|---------|----------|
| `--bg` | `#0f1117` | Nền chính |
| `--surface` | `#161a22` | Card, bảng |
| `--surface-2` | `#1d222d` | Input, cell hover |
| `--border` | `#262c38` | Viền |
| `--text` | `#e2e8f0` | Chữ chính |
| `--text-dim` | `#94a3b8` | Chữ phụ, label |
| `--accent` | `#2563eb` | Nút, link, active (AA ≥ 4.5:1 với chữ trắng) |
| `--accent-hover` | `#1d4ed8` | Hover nút |

### Ngữ nghĩa (status/trạng thái)

| Token | Giá trị | Ý nghĩa |
|-------|---------|---------|
| `--success` | `#22c55e` | paid, ok |
| `--warning` | `#eab308` | unpaid |
| `--danger` | `#ef4444` | overdue, xóa |
| `--danger-hover` | `#dc2626` | hover nút xóa |
| `--neutral` | `#64748b` | cancelled, disabled |

## 2. Typography

| Token | Giá trị |
|-------|---------|
| Font | `-apple-system, "Segoe UI", Roboto, sans-serif` |
| `--fs-xs` | 12px (badge, th, label) |
| `--fs-sm` | 12px (= xs, alias) |
| `--fs-md` | 15px (body, bảng, nút) |
| `--fs-lg` | 24px (header) |
| `--fs-xl` | 28px (số thống kê) |

> Thang 4 size, ratio ≥ 1.25 giữa các bước (WCAG AA + impeccable flat-type-hierarchy).

## 3. Spacing (thang 4px)

`--s-1: 4px` · `--s-2: 8px` · `--s-3: 12px` · `--s-4: 16px` · `--s-5: 20px` · `--s-6: 24px` · `--s-8: 32px`

## 4. Radius & Shadow

| Token | Giá trị |
|-------|---------|
| `--r-sm` | 6px (input, nút) |
| `--r-md` | 10px (card) |
| `--r-lg` | 16px (card spotlight) |
| Shadow card | `0 1px 3px rgba(0,0,0,.3)` |

## 5. Component specs

### Card
`bg: --surface` · `radius: --r-md` · `padding: --s-5` · `border: 1px solid --border`

### Button
- Chính: `bg --accent` → hover `--accent-hover` · `radius --r-sm` · padding `8px 12px`
- Phụ: transparent + border
- Danger: `bg --danger`
- Nhỏ: padding `4px 10px`, `--fs-sm`

### Badge (trạng thái hóa đơn)
| Status | Nền | Chữ |
|--------|-----|-----|
| paid | `--success` 15% | `--success` |
| unpaid | `--warning` 15% | `--warning` |
| overdue | `--danger` 15% | `--danger` |
| cancelled | `--neutral` 15% | `--neutral` |

### Table
Header: `--fs-xs` uppercase, `--text-dim` · Row: border-bottom `--border` · Hover: `--surface-2`

### Input
`bg --surface-2` · `border --border` · focus `border --accent` · `--fs-md`

## 6. Layout

- Header: `bg #0b0e13` (đậm hơn bg), padding `14px 24px`
- Main: max-width 960px, margin 24px auto
- Grid stats: `repeat(auto-fit, minmax(130px, 1fr))`, gap `--s-3`

## 7. Implement trong code

Tokens = CSS variables trong `:root` (frontend/src/styles.css). Component dùng `var(--token)` — không hardcode màu.

```
Thay đổi màu toàn app → sửa 1 chỗ trong :root
```
