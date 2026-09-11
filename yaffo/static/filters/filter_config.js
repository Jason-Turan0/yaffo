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

    // HTML drag events are not consistently emitted by touch browsers. A drag
    // that starts on the handle uses Pointer Events instead; checkbox taps and
    // page scrolling elsewhere in the modal keep their normal behavior.
    /** @type {number | null} */
    let touchPointerId = null;

    list.addEventListener('pointerdown', (e) => {
        if (e.pointerType === 'mouse' || !e.isPrimary || !(e.target instanceof Element)) return;
        const handle = e.target.closest('.filter-config-handle');
        const row = handle?.closest('.filter-config-row');
        if (!(handle instanceof HTMLElement) || !(row instanceof HTMLElement)) return;

        e.preventDefault();
        dragged = row;
        touchPointerId = e.pointerId;
        dragged.classList.add('dragging');
        try {
            // Capture on the list, not the handle: re-inserting the dragged row
            // can make Chrome release capture held by one of that row's children.
            list.setPointerCapture(e.pointerId);
        } catch {
            // Synthetic pointer events used by tests do not create capturable
            // pointers, but real touch and pen input does.
        }
    });

    list.addEventListener('pointermove', (e) => {
        if (e.pointerId !== touchPointerId || !dragged) return;
        e.preventDefault();
        const after = rowAfterPoint(e.clientY);
        if (after == null) list.appendChild(dragged);
        else list.insertBefore(dragged, after);
    });

    const finishTouchDrag = (/** @type {PointerEvent} */ e) => {
        if (e.pointerId !== touchPointerId) return;
        if (dragged) dragged.classList.remove('dragging');
        if (list.hasPointerCapture(e.pointerId)) {
            list.releasePointerCapture(e.pointerId);
        }
        dragged = null;
        touchPointerId = null;
    };

    list.addEventListener('pointerup', finishTouchDrag);
    list.addEventListener('pointercancel', finishTouchDrag);
    list.addEventListener('lostpointercapture', finishTouchDrag);

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
            const res = await fetch(config.buildUrl('save_filter_layout', {}), {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ items }),
            });
            if (!res.ok) throw new Error('save failed');
            window.location.reload();
        } catch {
            window.notification.error(i18n.t('media:filters.saveFailed'));
        }
    });
};
