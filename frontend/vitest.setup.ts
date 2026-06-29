import '@testing-library/jest-dom/vitest'

// jsdom does not implement scrollIntoView. Components that call it from a mount
// effect (e.g. keyboard-navigable popups like SkillSearchMenu) would otherwise
// throw during render in tests. Provide a no-op so those components mount.
if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = () => {}
}
