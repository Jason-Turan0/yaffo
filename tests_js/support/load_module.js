import path from 'node:path';
import { pathToFileURL } from 'node:url';

// Resolve against the repo root (Vitest's working directory).
const STATIC_DIR = path.join(process.cwd(), 'yaffo', 'static');

let reloadCounter = 0;

/**
 * Load a yaffo/static module into the current jsdom global.
 *
 * The app's modules attach to `window.PHOTO_ORGANIZER` as a side effect of
 * evaluation (no ESM exports). We import the source through Vitest's pipeline —
 * rather than eval'ing the raw string — so V8 coverage attributes execution to
 * the real file. A cache-busting query forces a fresh evaluation on every call,
 * restoring the per-test isolation a one-shot `import` would lose.
 *
 * @param {string} relPath - path under yaffo/static, e.g. "utils.js".
 * @returns {Promise<object>} window.PHOTO_ORGANIZER after evaluation.
 */
export async function loadModule(relPath) {
  const fileUrl = pathToFileURL(path.join(STATIC_DIR, relPath)).href;
  await import(/* @vite-ignore */ `${fileUrl}?reload=${reloadCounter++}`);
  return window.PHOTO_ORGANIZER;
}
