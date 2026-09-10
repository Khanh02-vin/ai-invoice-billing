# Accessibility Test Plan — Invoice & Billing Frontend

This document outlines manual accessibility test procedures for the Invoice & Billing frontend application. Automated testing with axe-core requires a browser environment, so these tests should be performed manually or integrated into a CI pipeline with a headless browser.

## Manual Test Procedures

### 1. Keyboard Navigation Tests

#### 1.1 Skip Link
- [ ] Press `Tab` on page load — skip link should appear
- [ ] Press `Enter` on skip link — focus should move to main content
- [ ] Skip link should be visually hidden until focused

#### 1.2 Login Page
- [ ] Tab through all form fields in logical order
- [ ] Submit form with `Enter` key
- [ ] Toggle between login/register with `Enter` or `Space`
- [ ] On error, focus should move to error message
- [ ] Error message should be readable when focused

#### 1.3 Dashboard Navigation
- [ ] Tab through sidebar navigation items
- [ ] Activate nav items with `Enter` or `Space`
- [ ] Tab to search input, type query, results update
- [ ] Tab through filter chips, activate with `Enter` or `Space`
- [ ] Tab through pagination controls

#### 1.4 Settings Sections
- [ ] Tab to collapsible section headers
- [ ] Press `Enter` or `Space` to expand/collapse
- [ ] Focus should remain on header after toggle
- [ ] Content should be accessible when expanded

### 2. Screen Reader Tests

#### 2.1 Login Page (NVDA/VoiceOver)
- [ ] Page title is announced
- [ ] Headings are announced in correct hierarchy
- [ ] Input labels are announced when focused
- [ ] Required state is announced
- [ ] Error messages are announced immediately
- [ ] Loading state is announced

#### 2.2 Dashboard
- [ ] "Tổng quan" heading announced as h1
- [ ] Stats section labeled correctly
- [ ] Table headers associated with cells
- [ ] Row count announced
- [ ] Status changes announced via live region

#### 2.3 Settings
- [ ] Section headings announced as h2
- [ ] Expand/collapse state announced
- [ ] Team table headers announced
- [ ] Billing info structure announced

### 3. Visual Accessibility Tests

#### 3.1 Focus Indicators
- [ ] All interactive elements have visible focus indicator
- [ ] Focus indicator is high contrast (3px solid accent)
- [ ] Focus indicator is not removed by `outline: none` without replacement

#### 3.2 Color Contrast
- [ ] Primary text contrast ≥ 4.5:1
- [ ] Large text contrast ≥ 3:1
- [ ] UI component contrast ≥ 3:1
- [ ] Focus indicator contrast ≥ 3:1

#### 3.3 Text Resizing
- [ ] Zoom to 200% — no content clipped
- [ ] Zoom to 400% — no horizontal scroll (except tables)
- [ ] Text spacing override — no content lost

#### 3.4 Reduced Motion
- [ ] Enable `prefers-reduced-motion` — animations disabled
- [ ] Spinner animation disabled
- [ ] Transition effects disabled

### 4. Form Accessibility Tests

#### 4.1 Labels
- [ ] All inputs have associated `<label>`
- [ ] Labels are visible and descriptive
- [ ] Required fields indicated visually and programmatically

#### 4.2 Error States
- [ ] `aria-invalid="true"` on invalid fields
- [ ] `aria-describedby` points to error message
- [ ] Error messages are descriptive
- [ ] Focus moves to error on submission failure

#### 4.3 Help Text
- [ ] Help text associated via `aria-describedby`
- [ ] Instructions announced by screen readers

## axe-core Rules to Check

When automated testing is available, verify the following axe-core rules:

### Critical (Must Fix)
- `color-contrast` — Elements must have sufficient color contrast
- `duplicate-id-active` — IDs of active elements must be unique
- `duplicate-id-aria` — IDs used in ARIA and labels must be unique
- `button-name` — Buttons must have discernible text
- `link-name` — Links must have discernible text
- `image-alt` — Images must have alternate text
- `input-button-name` — Input buttons must have discernible text
- `label` — Form elements must have labels
- `aria-input-field-name` — ARIA input fields must have an accessible name
- `aria-toggle-field-name` — ARIA toggle fields must have an accessible name

### Serious (Should Fix)
- `heading-order` — Heading levels should only increase by one
- `list` — `<li>` elements must be contained in `<ul>` or `<ol>`
- `listitem` — `<li>` elements must be contained in a `<ul>` or `<ol>`
- `region` — All page content should be contained by landmarks
- `aria-required-attr` — Required ARIA attributes must be provided
- `aria-required-children` — Certain ARIA roles must contain particular children
- `aria-required-parent` — Certain ARIA roles must be contained by particular parents
- `aria-roles` — ARIA roles used must conform to valid values
- `aria-valid-attr-value` — ARIA attributes must conform to valid values
- `focus-order-semantics` — Focusable elements should have an interactive role
- `no-focusable-content` — Focusable content should have an interactive role
- `bypass` — Page must have a way to bypass repeated blocks
- `landmark-one-main` — Page should have one main landmark
- `landmark-unique` — Landmarks should be unique
- `page-has-heading-one` — Page should contain a level-one heading

### Moderate (Should Fix)
- `color-contrast-enhanced` — Elements should have enhanced color contrast
- `link-in-text-block` — Links must be distinguishable from surrounding text
- `meta-viewport` — Zooming and scaling must not be disabled
- `object-alt` — `<object>` elements must have alternate text
- `role-img` — Elements with role="img" must have alternate text
- `svg-img` — SVG elements with an img role must have alternate text
- `table-duplicate-name` — Tables should not have the same summary and caption
- `table-fake-caption` — Tables with a caption must contain the caption
- `td-has-header` <td> in a <table> larger than 3x3 must have an associated table header
- `td-headers-attr` — Table cells that use the headers attribute must only refer to other cells in the same table
- `th-has-data-cells` — Table headers in a data table must refer to data cells

### Minor (Consider Fixing)
- `avoid-inline-spacing` — Inline styles for letter-spacing and word-spacing must be avoided
- `css-orientation-lock` — Display must not be locked to a specific orientation
- `definition-list` — `<dl>` elements must only contain properly-ordered `<dt>` and `<dd>` groups
- `dlitem` — Definition list items must be wrapped in `<dt>` and `<dd>` elements
- `html-xml-lang-mismatch` — HTML elements with valid lang and xml:lang must have the same base language
- `identical-links-same-purpose` — Links with the same name must have the same purpose
- `label-content-name-mismatch` — Elements labeled by their content must have their visible text as part of their accessible name
- `landmark-banner-is-top-level` — Banner landmark should be at top level
- `landmark-complementary-is-top-level` — Aside should be contained in a complementary landmark
| `landmark-contentinfo-is-top-level` — Contentinfo landmark should be at top level
- `landmark-main-is-top-level` — Main landmark should be at top level
- `landmark-no-duplicate-banner` — Document should not have more than one banner landmark
- `landmark-no-duplicate-contentinfo` — Document should not have more than one contentinfo landmark
- `meta-refresh` — `<meta http-equiv="refresh">` must not be used
- `p-as-heading` — Bold, italic text shouldn't be used to style headings
- `select-name` — Select elements must have accessible names

## Verification Checklist

### Images
- [ ] All `<img>` elements have `alt` attribute
- [ ] Decorative images have `alt=""` or `aria-hidden="true"`
- [ ] Informative images have descriptive `alt` text
- [ ] SVG icons use `aria-hidden="true"` when decorative

### Inputs
- [ ] All `<input>` elements have associated `<label>`
- [ ] All `<select>` elements have associated `<label>`
- [ ] All `<textarea>` elements have associated `<label>`
- [ ] Labels use `htmlFor` or wrap the input
- [ ] Required fields indicated with `aria-required="true"`
- [ ] Error states use `aria-invalid="true"`

### Color Contrast
- [ ] Body text: ≥ 4.5:1
- [ ] Large text (≥18px or ≥14px bold): ≥ 3:1
- [ ] UI components: ≥ 3:1
- [ ] Focus indicators: ≥ 3:1

### ARIA
- [ ] All ARIA roles are valid
- [ ] All ARIA attributes are valid for the role
- [ ] Required ARIA properties are present
- [ ] ARIA states reflect current component state
- [ ] Live regions used for dynamic content

### Headings
- [ ] Only one `<h1>` per page
- [ ] Heading levels don't skip (h1 → h2 → h3)
- [ ] Headings describe content sections

### Tables
- [ ] `<table>` has `role="table"` if needed
- [ ] Headers use `<th>` with `scope="col"` or `scope="row"`
- [ ] Complex tables have `aria-rowcount` and `aria-rowindex`
- [ ] Caption or `aria-label` provided

### Forms
- [ ] `<form>` has `aria-label` or `aria-labelledby` if multiple forms
- [ ] Submit button has discernible text
- [ ] Error messages associated with inputs via `aria-describedby`
- [ ] Fieldsets used for groups of related inputs

## Test Environment

### Browsers
- Chrome 120+
- Firefox 121+
- Safari 17+
- Edge 120+

### Screen Readers
- NVDA 2023.3+ (Windows)
- JAWS 2024+ (Windows)
- VoiceOver (macOS 14+, iOS 17+)
- TalkBack (Android 14+)

### Tools
- axe DevTools browser extension
- WAVE Web Accessibility Evaluator
- Colour Contrast Analyser (CCA)
- Browser DevTools Accessibility Inspector

## Running Automated Tests (Future)

When a browser test runner is available, integrate axe-core:

```javascript
// Example using @axe-core/webdriverjs with Playwright
const { chromium } = require('playwright');
const AxeBuilder = require('@axe-core/webdriverjs');

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage();
  await page.goto('http://localhost:5173');

  const results = await new AxeBuilder(page)
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
    .analyze();

  console.log(results.violations);
  await browser.close();
})();
```

## Test Results Template

| Date | Tester | Browser | Screen Reader | Result | Notes |
|------|--------|---------|---------------|--------|-------|
| | | | | | |
