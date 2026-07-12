// @ts-check

/** Album add screen: the results grid is always selecting (selecting is the whole
 *  point of the screen). The selection lives on the URL — `select=all` plus any
 *  `exclude_id`, or `select_id` per ticked card — so the "Add photos" form simply
 *  posts to that same querystring and the server reads what it rendered. */
document.addEventListener('yaffo:app-init-complete', (event) => {
    const app = event.detail.app;

    const addButton = document.getElementById('add-to-album-button');
    const form = /** @type {HTMLFormElement | null} */ (document.getElementById('add-to-album-form'));
    if (!addButton || !form) return;

    const selection = app.COMPONENTS.selectionBar?.init({
        grid: '#add-grid',
        bar: '#add-selection',
        totalCount: Number(addButton.dataset.matchCount || 0),
    });
    if (!selection) return;

    addButton.addEventListener('click', () => {
        const state = selection.getState();
        if (!state.all && state.ids.length === 0) return;
        form.submit();  // the selection is already on the action's querystring
    });
});
