import '@testing-library/jest-dom/vitest';

import { cleanup } from '@testing-library/react';
import { afterEach } from 'vitest';

import { useAppStore } from '@/stores';

afterEach(() => {
  cleanup();
  // One store per module; reset it so tests cannot leak client state into each other.
  useAppStore.setState(useAppStore.getInitialState(), true);
  sessionStorage.clear();
});

// jsdom lacks these browser APIs; Mantine uses them for color scheme and layout.
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: (query: string) => ({
    // As in browsers, an empty query matches everything (mantine-datatable relies on
    // this for columns without a visibleMediaQuery); real media features don't match.
    matches: query.trim() === '',
    media: query,
    onchange: null,
    addListener: () => undefined,
    removeListener: () => undefined,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    dispatchEvent: () => false,
  }),
});

class ResizeObserverStub {
  observe() {
    /* no-op: jsdom has no layout */
  }
  unobserve() {
    /* no-op */
  }
  disconnect() {
    /* no-op */
  }
}
window.ResizeObserver = ResizeObserverStub;
window.HTMLElement.prototype.scrollIntoView = () => undefined;
