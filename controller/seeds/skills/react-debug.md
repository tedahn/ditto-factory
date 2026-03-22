---
name: React Debugging
description: Use when debugging React component rendering, hook issues, or state management problems
---

# React Debugging

## When to Use
- Component renders unexpectedly or fails to re-render
- Hook errors (rules of hooks violations, stale closures)
- State updates not reflecting in the UI
- Performance issues from excessive re-renders

## Instructions

1. **Identify the symptom**: Check the browser console for React warnings or errors. Look for "Cannot update a component while rendering a different component" or "Rendered more hooks than during the previous render."

2. **Trace the render cycle**: Add temporary `console.log` at the top of the component body (before hooks) to see render frequency. Use `React.Profiler` or React DevTools Profiler to identify unnecessary renders.

3. **Debug hooks**:
   - Verify all hooks are called unconditionally (no hooks inside if/for/early returns)
   - Check `useEffect` dependency arrays for missing or extra dependencies
   - Look for stale closures: if a callback references state but the dep array is empty, the value is stale
   - Verify `useMemo`/`useCallback` deps match the values actually used

4. **Debug state flow**:
   - Trace where state is initialized and where `setState` is called
   - Check if state is being mutated directly instead of creating new references
   - For context consumers, verify the provider is above the consumer in the tree

5. **Fix and verify**: After applying the fix, confirm the component renders the expected number of times and produces correct output.

## Checklist
- [ ] Console is free of React warnings and errors
- [ ] All hooks follow rules of hooks (no conditional calls)
- [ ] useEffect dependency arrays are complete and correct
- [ ] State updates use immutable patterns (spread, map, filter)
- [ ] No stale closures in callbacks or effects
- [ ] Component does not re-render excessively (check with Profiler)
