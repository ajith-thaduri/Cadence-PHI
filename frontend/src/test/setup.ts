import '@testing-library/jest-dom/vitest';

// Mantine relies on these browser APIs, which jsdom does not provide.
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }),
});
class RO {
  observe() {}
  unobserve() {}
  disconnect() {}
}
window.ResizeObserver = RO as unknown as typeof ResizeObserver;
