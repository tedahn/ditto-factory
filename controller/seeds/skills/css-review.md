---
name: CSS Code Review
description: Use when reviewing CSS or styling code for quality, accessibility, and maintainability
---

# CSS Code Review

## When to Use
- Reviewing PRs that modify CSS, SCSS, Tailwind, or styled-components
- Auditing a component's styling for responsiveness and accessibility
- Refactoring legacy CSS for maintainability

## Instructions

1. **Naming conventions**: Verify class names follow project conventions (BEM, utility-first, or CSS modules). Names should describe purpose, not appearance (use `.alert-banner` not `.red-box`).

2. **Accessibility checks**:
   - Ensure color contrast meets WCAG AA (4.5:1 for normal text, 3:1 for large text)
   - Verify interactive elements have visible focus styles (`:focus-visible`)
   - Check that no information is conveyed by color alone
   - Ensure touch targets are at least 44x44px on mobile

3. **Responsive design**:
   - Use relative units (`rem`, `em`, `%`, `vw`) over fixed `px` for font sizes and layout
   - Verify breakpoints cover mobile (320px+), tablet (768px+), desktop (1024px+)
   - Test that content does not overflow or get hidden at any viewport width
   - Prefer `min-width` media queries (mobile-first)

4. **Maintainability**:
   - Avoid `!important` unless overriding third-party styles
   - Keep specificity low: prefer classes over IDs, avoid deep nesting (max 3 levels)
   - Extract repeated values into CSS custom properties or design tokens
   - Remove dead/unused CSS rules

5. **Performance**:
   - Avoid expensive selectors (universal `*`, deep descendant chains)
   - Use `will-change` sparingly and only when animation jank is measured
   - Prefer `transform`/`opacity` for animations over layout-triggering properties

## Checklist
- [ ] Class names are semantic and follow project conventions
- [ ] Color contrast meets WCAG AA minimums
- [ ] Focus styles are visible on all interactive elements
- [ ] Layout works at 320px, 768px, and 1024px+ viewports
- [ ] No `!important` without justification
- [ ] CSS custom properties used for repeated values
- [ ] No unused or dead CSS rules
