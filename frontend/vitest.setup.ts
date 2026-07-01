import '@testing-library/jest-dom/vitest'

// jsdom does not implement scrollIntoView. Components that call it from a mount
// effect (e.g. keyboard-navigable popups like SkillSearchMenu) would otherwise
// throw during render in tests. Provide a no-op so those components mount.
if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = () => {}
}

if (!Element.prototype.scrollTo) {
  Element.prototype.scrollTo = () => {}
}

if (!window.matchMedia) {
  window.matchMedia = () =>
    ({
      matches: false,
      media: "",
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }) as MediaQueryList
}

if (!globalThis.ResizeObserver) {
  globalThis.ResizeObserver = class ResizeObserver {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
}
