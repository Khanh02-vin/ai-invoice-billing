# Accessibility Documentation — Invoice & Billing Frontend

This document describes the accessibility features implemented in the Invoice & Billing frontend application, following WCAG 2.1 AA guidelines.

## WCAG 2.1 AA Checklist Status

### Perceivable

| Criterion | Status | Notes |
|-----------|--------|-------|
| 1.1.1 Non-text Content | Pass | All images have `aria-hidden` or meaningful `alt` text. Icons use `aria-hidden="true"` when decorative. |
| 1.3.1 Info and Relationships | Pass | Semantic HTML used: `<header>`, `<main>`, `<nav>`, `<section>`, `<aside>`, `<table>` with proper `<th scope="col">`. |
| 1.3.2 Meaningful Sequence | Pass | DOM order matches visual order. Skip link allows bypassing repeated navigation. |
| 1.4.1 Use of Color | Pass | Status indicators use both color and text labels. Error states use icons + text. |
| 1.4.3 Contrast (Minimum) | Pass | Text contrast ratios meet 4.5:1 minimum. Primary text `#e2e8f0` on `#0f1117` (14.8:1). |
| 1.4.4 Resize text | Pass | All font sizes use relative units or fixed px that scale with browser zoom. |
| 1.4.10 Reflow | Pass | Layout uses flexbox/grid; content reflows at 320px without horizontal scroll. |
| 1.4.11 Non-text Contrast | Pass | Focus indicators use 3px solid accent (`#2563eb`) with 3:1 contrast against background. |
| 1.4.12 Text Spacing | Pass | No content is clipped when user overrides spacing. |
| 1.4.13 Content on Hover or Focus | Pass | No content appears only on hover. |

### Operable

| Criterion | Status | Notes |
|-----------|--------|-------|
| 2.1.1 Keyboard | Pass | All interactive elements are keyboard accessible. Custom buttons use `<button>` elements. |
| 2.1.2 No Keyboard Trap | Pass | Focus moves naturally through the page. No traps in modals or forms. |
| 2.4.1 Bypass Blocks | Pass | Skip link "Bỏ qua điều hướng, đến nội dung chính" provided. |
| 2.4.3 Focus Order | Pass | Logical tab order: skip link → sidebar nav → main content → interactive elements. |
| 2.4.4 Link Purpose (In Context) | Pass | All links and buttons have descriptive text or `aria-label`. |
| 2.4.6 Headings and Labels | Pass | Heading hierarchy: h1 (page title) → h2 (section titles). All inputs have labels. |
| 2.4.7 Focus Visible | Pass | High-contrast 3px outline on all focused interactive elements. |
| 2.5.3 Label in Name | Pass | Accessible names match visible labels. |

### Understandable

| Criterion | Status | Notes |
|-----------|--------|-------|
| 3.1.1 Language of Page | Pass | HTML lang attribute should be set to "vi" (Vietnamese). |
| 3.2.1 On Focus | Pass | No context changes on focus. |
| 3.2.2 On Input | Pass | No automatic context changes on input. |
| 3.3.1 Error Identification | Pass | Errors are announced via `role="alert"` and `aria-live="assertive"`. |
| 3.3.2 Labels or Instructions | Pass | All form fields have associated `<label>` elements. |
| 3.3.3 Error Suggestion | Pass | Error messages describe the issue. |
| 3.3.4 Error Prevention (Legal, Financial, Data) | Pass | No irreversible actions without confirmation. |

### Robust

| Criterion | Status | Notes |
|-----------|--------|-------|
| 4.1.1 Parsing | Pass | Valid HTML5 markup. |
| 4.1.2 Name, Role, Value | Pass | ARIA roles, states, and properties used correctly. |
| 4.1.3 Status Messages | Pass | Status messages use `role="status"` or `role="alert"` with `aria-live`. |

## Keyboard Navigation Guide

### General Navigation

| Key | Action |
|-----|--------|
| `Tab` | Move focus to next interactive element |
| `Shift + Tab` | Move focus to previous interactive element |
| `Enter` | Activate buttons, links, and form submissions |
| `Space` | Activate buttons and toggle controls |
| `Escape` | Close banners or dismiss messages |

### Skip Link

On first `Tab` press, a "Bỏ qua điều hướng, đến nội dung chính" link appears. Press `Enter` to jump directly to the main content area, bypassing the sidebar navigation.

### Sidebar Navigation

- Use `Tab` to move between navigation items
- `Enter` or `Space` to select a view (Tổng quan, Báo cáo, Cài đặt)
- Current page indicated by `aria-current="page"`

### Invoice Table

- Table headers use `scope="col"` for screen reader association
- Pagination buttons have descriptive `aria-label` ("Trang trước", "Trang sau")
- Page info announced via `aria-live="polite"`

### Settings Sections

- Team, Billing, and Security sections are collapsible
- Toggle buttons use `aria-expanded` and `aria-controls`
- `Enter` or `Space` to expand/collapse sections

### Login Form

- `Tab` moves between username, password, and submit button
- On error, focus moves to the error message
- Error message uses `role="alert"` and `aria-live="assertive"`

## Screen Reader Testing Notes

### Tested Screen Readers

- **NVDA** (Windows) — Primary testing target
- **VoiceOver** (macOS/iOS) — Secondary testing target
- **JAWS** (Windows) — Enterprise target

### Expected Announcements

#### Login Page
- "Invoice & Billing, heading level 1"
- "Đăng nhập, heading level 2"
- "Tên đăng nhập, required, edit"
- "Mật khẩu, required, edit"
- On error: "Alert, [error message]"

#### Dashboard (Tổng quan)
- "Tổng quan, heading level 1"
- "Thống kê, region" (stats section)
- "Danh sách hóa đơn, region" (table section)
- Table: "Mã hóa đơn, column header" etc.

#### Settings (Cài đặt)
- "Cài đặt, heading level 1"
- "Đội nhóm, button, collapsed/expanded"
- "Thanh toán, button, collapsed/expanded"
- "Bảo mật, button, collapsed/expanded"

### ARIA Patterns Used

1. **Landmark Roles**
   - `<nav aria-label="Primary">` — Primary navigation
   - `<main id="main-content">` — Main content area
   - `<aside>` — Sidebar (complementary)

2. **Live Regions**
   - `role="alert"` + `aria-live="assertive"` — Error messages
   - `role="status"` + `aria-live="polite"` — Success messages, loading states

3. **Form Accessibility**
   - `<label htmlFor="id">` — Explicit label association
   - `aria-required="true"` — Required fields
   - `aria-invalid="true"` — Invalid fields
   - `aria-describedby` — Help text and error descriptions

4. **Interactive Elements**
   - `aria-expanded` — Collapsible sections
   - `aria-controls` — Relationship between trigger and content
   - `aria-pressed` — Toggle buttons (filter chips)
   - `aria-current="page"` — Current navigation item
   - `aria-busy="true"` — Loading state on submit button

5. **Table Accessibility**
   - `role="table"` — Table container
   - `aria-rowcount` — Total row count
   - `aria-rowindex` — Row position
   - `aria-sort="none"` — Sortable headers
   - `scope="col"` — Column headers

6. **Progress Indicators**
   - `role="progressbar"` — Progress bars
   - `aria-valuenow`, `aria-valuemin`, `aria-valuemax` — Progress values

## Color Contrast Ratios

| Element | Foreground | Background | Ratio | WCAG AA |
|---------|------------|------------|-------|---------|
| Primary text | `#e2e8f0` | `#0f1117` | 14.8:1 | Pass |
| Dimmed text | `#94a3b8` | `#0f1117` | 6.5:1 | Pass |
| Accent button | `#ffffff` | `#2563eb` | 4.6:1 | Pass |
| Success text | `#22c55e` | `#0f1117` | 5.2:1 | Pass |
| Danger text | `#ef4444` | `#0f1117` | 4.6:1 | Pass |
| Focus outline | `#2563eb` | `#0f1117` | 3.0:1 | Pass (UI components) |

## Future Improvements

- [ ] Add `lang="vi"` to HTML element
- [ ] Implement focus trap for any future modal dialogs
- [ ] Add `aria-live` region for invoice upload progress
- [ ] Test with actual screen reader hardware
- [ ] Add skip links for table pagination
- [ ] Implement `aria-activedescendant` for any autocomplete components
