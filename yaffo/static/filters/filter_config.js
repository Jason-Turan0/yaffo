// @ts-check

window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};
window.PHOTO_ORGANIZER.filters = window.PHOTO_ORGANIZER.filters || {};

// Configure-filters modal: drag-and-drop reorder, show/hide checkboxes, reset to
// defaults, and save (POST the layout, then reload so the sidebar re-renders).
/**
 * @param {I18nService} i18n
 * @param {AppConfig} config
 */
window.PHOTO_ORGANIZER.filters.initConfig = (i18n, config) => {
    const trigger = document.getElementById('configure-filters-btn');
    const list = document.getElementById('filter-config-list');
    if (!trigger || !list) return;  // sidebar without the configurable layout

    const modal = window.PHOTO_ORGANIZER.COMPONENTS.modal.init('configureFiltersModal');
    const formElement = modal.formElement;
    if (!formElement) return;
    const resetBtn = document.getElementById('filter-config-reset');

    trigger.addEventListener('click', () => modal.open());

    // --- drag & drop reorder ---
    /** @type {HTMLElement | null} */
    let dragged = null;

    /**
     * @param {number} y
     * @returns {HTMLElement | null}
     */
    const rowAfterPoint = (y) => {
        const rows = /** @type {HTMLElement[]} */ ([...list.querySelectorAll('.filter-config-row:not(.dragging)')]);
        /** @type {{ offset: number, row: HTMLElement | null }} */
        const initialClosest = { offset: Number.NEGATIVE_INFINITY, row: null };
        return rows.reduce((closest, row) => {
            const box = row.getBoundingClientRect();
            const offset = y - box.top - box.height / 2;
            return offset < 0 && offset > closest.offset ? { offset, row } : closest;
        }, initialClosest).row;
    };

    list.addEventListener('dragstart', (e) => {
        if (!(e.target instanceof Element)) return;
        dragged = e.target.closest('.filter-config-row');
        if (dragged) dragged.classList.add('dragging');
    });
    list.addEventListener('dragend', () => {
        if (dragged) dragged.classList.remove('dragging');
        dragged = null;
    });
    list.addEventListener('dragover', (e) => {
        if (!dragged) return;
        e.preventDefault();
        const after = rowAfterPoint(e.clientY);
        if (after == null) list.appendChild(dragged);
        else list.insertBefore(dragged, after);
    });

    // --- reset to defaults: registry order, all visible ---
    resetBtn?.addEventListener('click', () => {
        /** @type {Record<string, HTMLElement>} */
        const byKey = {};
        list.querySelectorAll('.filter-config-row').forEach((row) => {
            if (!(row instanceof HTMLElement) || !row.dataset.key) return;
            byKey[row.dataset.key] = row;
        });
        (list.dataset.defaultKeys || '').split(',').filter(Boolean).forEach((key) => {
            const row = byKey[key];
            if (!row) return;
            const toggle = row.querySelector('.filter-config-toggle');
            if (toggle instanceof HTMLInputElement) toggle.checked = true;
            list.appendChild(row);  // re-append in default order
        });
    });

    // --- save: post the layout in DOM order, reload on success ---
    formElement.addEventListener('submit', async (e) => {
        e.preventDefault();
        /** @type {{ key: string, visible: boolean }[]} */
        const items = [];
        list.querySelectorAll('.filter-config-row').forEach((row) => {
            if (!(row instanceof HTMLElement) || !row.dataset.key) return;
            const toggle = row.querySelector('.filter-config-toggle');
            if (!(toggle instanceof HTMLInputElement)) return;
            items.push({
                key: row.dataset.key,
                visible: toggle.checked,
            });
        });
        try {
            // The layout is saved per page; the modal partial stamps which one.
            const page = list.dataset.page || 'home';
            const res = await fetch(config.buildUrl('save_filter_layout', { page }), {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ items }),
            });
            if (!res.ok) throw new Error('save failed');
            window.location.reload();
        } catch (err) {
            window.notification.error(i18n.t('media:filters.saveFailed'));
        }
    });
};
