import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    // Browser modules touch the DOM directly (document.getElementById, etc.).
    environment: 'jsdom',
    // Expose describe/it/expect/vi globally so test files (and the shared setup)
    // don't need to import them.
    globals: true,
    // Provides the implicit globals every yaffo/static module assumes exist
    // (window.APP_CONFIG, window.notification). See tests_js/support/setup.js.
    setupFiles: ['./tests_js/support/setup.js'],
    include: ['tests_js/**/*.test.js'],
    coverage: {
      provider: 'v8',
      include: ['yaffo/static/**/*.js'],
      // Vendored libraries (htmx/OpenLayers/gridstack/i18next) are not ours to test.
      exclude: ['yaffo/static/vendor/**'],
      reporter: ['text', 'html', 'lcov'],
      reportsDirectory: 'tests_js/coverage',
      // Add a floor once Phase 1 lands, e.g. thresholds: { lines: 60 }.
    },
  },
});
