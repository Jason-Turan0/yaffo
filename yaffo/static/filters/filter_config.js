window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};

// Configure-filters modal: drag-and-drop reorder, show/hide checkboxes, reset to
// defaults, and save (POST the layout, then reload so the sidebar re-renders).
window.PHOTO_ORGANIZER.initFilterConfig = (config) => {
    const trigger = document.getElementById('configure-filters-btn');
    const list = document.getElementById('filter-config-list');
    if (!trigger || !list) return;  // sidebar without the configurable layout

    const modal = window.PHOTO_ORGANIZER.COMPONENTS.modal.init('configureFiltersModal');
    const resetBtn = document.getElementById('filter-config-reset');

    trigger.addEventListener('click', () => modal.open());

    // --- drag & drop reorder ---
    let dragged = null;

    const rowAfterPoint = (y) => {
        const rows = [...list.querySelectorAll('.filter-config-row:not(.dragging)')];
        return rows.reduce((closest, row) => {
            const box = row.getBoundingClientRect();
            const offset = y - box.top - box.height / 2;
            return offset < 0 && offset > closest.offset ? { offset, row } : closest;
        }, { offset: Number.NEGATIVE_INFINITY, row: null }).row;
    };

    list.addEventListener('dragstart', (e) => {
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
    resetBtn.addEventListener('click', () => {
        const byKey = {};
        list.querySelectorAll('.filter-config-row').forEach((row) => { byKey[row.dataset.key] = row; });
        (list.dataset.defaultKeys || '').split(',').filter(Boolean).forEach((key) => {
            const row = byKey[key];
            if (!row) return;
            row.querySelector('.filter-config-toggle').checked = true;
            list.appendChild(row);  // re-append in default order
        });
    });

    // --- save: post the layout in DOM order, reload on success ---
    modal.formElement.addEventListener('submit', async (e) => {
        e.preventDefault();
        const items = [...list.querySelectorAll('.filter-config-row')].map((row) => ({
            key: row.dataset.key,
            visible: row.querySelector('.filter-config-toggle').checked,
        }));
        try {
            const res = await fetch(config.urls.save_home_filters, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ items }),
            });
            if (!res.ok) throw new Error('save failed');
            window.location.reload();
        } catch (err) {
            notification.error('Could not save filter settings');
        }
    });
};
