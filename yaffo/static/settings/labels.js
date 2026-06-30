window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};
window.PHOTO_ORGANIZER.settings = window.PHOTO_ORGANIZER.settings || {};

// Live client-side filter for the Photo Labels list. Delegated on document so it
// keeps working after #labels-section is re-rendered by HTMX (create/delete) — the
// fresh input just starts empty.
window.PHOTO_ORGANIZER.settings.initLabelFilter = () => {
    document.addEventListener('input', (event) => {
        if (event.target.id !== 'label-filter') return;
        const section = event.target.closest('#labels-section');
        if (!section) return;

        const query = event.target.value.trim().toLowerCase();
        let anyVisible = false;
        section.querySelectorAll('.label-chip').forEach((chip) => {
            const name = (chip.querySelector('.label-chip-name')?.textContent || '').toLowerCase();
            const prompt = (chip.querySelector('.label-chip-info')?.dataset.tooltip || '').toLowerCase();
            const match = !query || name.includes(query) || prompt.includes(query);
            chip.hidden = !match;
            if (match) anyVisible = true;
        });

        const empty = section.querySelector('.label-filter-empty');
        if (empty) empty.hidden = anyVisible || !query;
    });
};
