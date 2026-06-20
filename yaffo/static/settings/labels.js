window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};

// Live client-side filter for the Photo Labels list. Delegated on document so it
// keeps working after #labels-section is re-rendered by HTMX (create/delete) — the
// fresh input just starts empty.
window.PHOTO_ORGANIZER.initLabelFilter = () => {
    document.addEventListener('input', (event) => {
        if (event.target.id !== 'label-filter') return;
        const section = event.target.closest('#labels-section');
        if (!section) return;

        const query = event.target.value.trim().toLowerCase();
        let anyVisible = false;
        section.querySelectorAll('.label-row').forEach((row) => {
            const name = (row.querySelector('.label-name')?.textContent || '').toLowerCase();
            const prompt = (row.querySelector('.label-prompt')?.textContent || '').toLowerCase();
            const match = !query || name.includes(query) || prompt.includes(query);
            row.hidden = !match;
            if (match) anyVisible = true;
        });

        const empty = section.querySelector('.label-filter-empty');
        if (empty) empty.hidden = anyVisible || !query;
    });
};
