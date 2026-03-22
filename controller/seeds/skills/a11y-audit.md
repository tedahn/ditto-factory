---
name: Accessibility Audit
description: Use when auditing a page or component for WCAG 2.1 AA compliance
---

# Accessibility Audit

## When to Use
- Building or reviewing UI components
- Preparing for accessibility compliance review
- Fixing reported accessibility issues
- Adding new interactive elements or forms

## Instructions

1. **Semantic HTML**: Verify the page uses proper semantic elements (`nav`, `main`, `article`, `section`, `header`, `footer`). Headings must follow a logical hierarchy (h1 > h2 > h3) without skipping levels.

2. **Keyboard navigation**:
   - Tab through the entire page: every interactive element must be reachable
   - Tab order must follow visual reading order
   - Custom widgets need proper keyboard handlers (Enter/Space for buttons, Arrow keys for menus)
   - Focus must never get trapped (except in modals with proper escape handling)

3. **ARIA usage**:
   - Prefer native HTML elements over ARIA (`<button>` over `<div role="button">`)
   - Every ARIA role must have required properties (e.g., `role="slider"` needs `aria-valuenow`)
   - Use `aria-label` or `aria-labelledby` for elements without visible text labels
   - Dynamic content updates need `aria-live="polite"` or `aria-live="assertive"`

4. **Images and media**:
   - All `<img>` elements need meaningful `alt` text or `alt=""` for decorative images
   - Videos need captions; audio needs transcripts
   - Icons used as buttons need accessible names

5. **Forms**:
   - Every input has an associated `<label>` (use `htmlFor`/`for` attribute)
   - Error messages are programmatically associated with inputs (`aria-describedby`)
   - Required fields are indicated with `aria-required="true"` and visual indicator

6. **Color and contrast**:
   - Text contrast ratio: 4.5:1 minimum (3:1 for large text 18px+ bold or 24px+)
   - UI component contrast: 3:1 against adjacent colors
   - Information not conveyed by color alone

## Checklist
- [ ] Page uses semantic HTML elements correctly
- [ ] All interactive elements are keyboard accessible
- [ ] Tab order matches visual reading order
- [ ] ARIA roles have all required properties
- [ ] All images have appropriate alt text
- [ ] All form inputs have associated labels
- [ ] Color contrast meets WCAG AA ratios
- [ ] Dynamic content changes announced to screen readers
