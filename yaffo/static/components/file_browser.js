// @ts-check

window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};
window.PHOTO_ORGANIZER.COMPONENTS = window.PHOTO_ORGANIZER.COMPONENTS || {};

/**
 * Wires every "Browse" button (`.file-browser-btn`, sat next to a `.file-browser-input`
 * inside a `.file-browser-input-group`) to the in-app folder picker. Uses one delegated
 * click listener so it also covers inputs added later by HTMX swaps (e.g. the
 * remove-duplicates form) with no re-initialization.
 *
 * Set `data-mode="file"` or `data-mode="any"` on any ancestor to pick a file or a
 * file/folder path. On selection the input's value is set and a bubbling `change`
 * event is dispatched, so HTMX triggers (hx-trigger="change") on the input fire as
 * if the user typed the path.
 */
window.PHOTO_ORGANIZER.COMPONENTS.fileBrowser = {
    init: () => {
        if (document.documentElement.dataset.fileBrowserReady === '1') return;
        document.documentElement.dataset.fileBrowserReady = '1';

        document.addEventListener('click', async (event) => {
            if (!(event.target instanceof Element)) return;
            const btn = event.target.closest('.file-browser-btn');
            if (!btn) return;
            const group = btn.closest('.file-browser-input-group') || btn.closest('.file-browser-group');
            const input = group && group.querySelector('.file-browser-input');
            if (!(input instanceof HTMLInputElement)) return;

            const modeEl = btn.closest('[data-mode]');
            const mode = /** @type {FolderPickerMode} */ ((modeEl instanceof HTMLElement && modeEl.dataset.mode) || 'folder');
            const path = await window.PHOTO_ORGANIZER.pickFolder({ mode, startPath: input.value || null });
            if (path) {
                input.value = path;
                input.dispatchEvent(new Event('change', { bubbles: true }));
            }
        });
    },
};
