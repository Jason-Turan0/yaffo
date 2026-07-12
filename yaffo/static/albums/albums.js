// @ts-check

/** Albums tab: the New album / Edit details modals, the delete confirm, and the
 *  album's edit mode (selection bar + drag-to-reorder).
 *
 *  Confirm copy comes from data-* attributes (translated server-side) rather than
 *  the JS i18n bundle, so the strings live next to the markup that shows them. */

/**
 * @param {HTMLFormElement} form
 * @param {string} name
 * @param {string} value
 */
const addField = (form, name, value) => {
    const input = document.createElement('input');
    input.type = 'hidden';
    input.name = name;
    input.value = value;
    form.appendChild(input);
};

/**
 * Drag-to-reorder inside edit mode. The grid moves the card immediately and posts
 * the new order; the server is the record, the DOM is already correct, so the
 * response is a 204 and nothing re-renders.
 * @param {HTMLElement} grid
 */
const initReorder = (grid) => {
    const url = grid.dataset.reorderUrl;
    if (!url) return;
    /** @type {HTMLElement | null} */
    let dragged = null;

    grid.querySelectorAll('[data-select-id]').forEach((card) => {
        if (!(card instanceof HTMLElement)) return;
        card.draggable = true;
        card.addEventListener('dragstart', () => { dragged = card; card.classList.add('is-dragging'); });
        card.addEventListener('dragend', () => {
            card.classList.remove('is-dragging');
            dragged = null;
            void postOrder();
        });
        card.addEventListener('dragover', (event) => {
            event.preventDefault();  // without this the drop is rejected
            if (!dragged || dragged === card) return;
            const cards = Array.from(grid.querySelectorAll('[data-select-id]'));
            const from = cards.indexOf(dragged);
            const to = cards.indexOf(card);
            grid.insertBefore(dragged, from < to ? card.nextSibling : card);
        });
    });

    const postOrder = async () => {
        const body = new FormData();
        grid.querySelectorAll('[data-select-id]').forEach((card) => {
            if (card instanceof HTMLElement && card.dataset.selectId) {
                body.append('media_item_id', card.dataset.selectId);
            }
        });
        await fetch(url, { method: 'POST', body });
    };
};

document.addEventListener('yaffo:app-init-complete', (event) => {
    const app = event.detail.app;

    const newButton = document.getElementById('new-album-button');
    if (newButton) {
        const newModal = app.COMPONENTS.modal.init('newAlbumModal');
        newButton.addEventListener('click', newModal.open);
    }

    const editDetailsButton = document.getElementById('edit-album-details-button');
    if (editDetailsButton) {
        const editModal = app.COMPONENTS.modal.init('editAlbumModal');
        editDetailsButton.addEventListener('click', editModal.open);
    }

    const deleteButton = document.getElementById('delete-album-button');
    if (deleteButton) {
        deleteButton.addEventListener('click', async () => {
            const confirmed = await window.PHOTO_ORGANIZER.confirmDialog({
                title: deleteButton.dataset.confirmTitle,
                message: deleteButton.dataset.confirmMessage,
                confirmText: deleteButton.dataset.confirmText,
                confirmClass: 'btn-danger'
            });
            if (confirmed) {
                const form = /** @type {HTMLFormElement | null} */ (
                    document.getElementById('delete-album-form')
                );
                form?.submit();
            }
        });
    }

    // ---- edit mode (?edit=1 — the server renders the grid already selecting) ----
    const grid = document.getElementById('album-grid');
    if (!(grid instanceof HTMLElement) || grid.dataset.editing !== '1') return;

    const coverButton = document.getElementById('set-cover-button');
    const removeButton = document.getElementById('remove-from-album-button');

    const selection = app.COMPONENTS.selectionBar?.init({
        grid: '#album-grid',
        bar: '#album-selection',
        totalCount: Number(grid.dataset.memberCount || 0),
        onChange: (state) => {
            // A cover is one photo, so the action is only meaningful on exactly one.
            if (coverButton instanceof HTMLButtonElement) {
                coverButton.disabled = state.all || state.ids.length !== 1;
            }
        },
    });
    if (!selection) return;

    initReorder(grid);

    coverButton?.addEventListener('click', () => {
        const state = selection.getState();
        if (state.all || state.ids.length !== 1) return;
        const form = /** @type {HTMLFormElement | null} */ (document.getElementById('set-cover-form'));
        if (!form) return;
        // One photo, not a bulk selection: this one posts its id in the body.
        addField(form, 'media_item_id', state.ids[0]);
        form.submit();
    });

    removeButton?.addEventListener('click', async () => {
        const state = selection.getState();
        if (!state.all && state.ids.length === 0) return;
        const confirmed = await window.PHOTO_ORGANIZER.confirmDialog({
            title: removeButton.dataset.confirmTitle,
            message: removeButton.dataset.confirmMessage,
            confirmText: removeButton.dataset.confirmText,
            confirmClass: 'btn-danger'
        });
        if (!confirmed) return;
        const form = /** @type {HTMLFormElement | null} */ (
            document.getElementById('remove-from-album-form')
        );
        // The selection is already on the form's action querystring (kept in step by
        // the selection bar), so there is nothing to build here.
        form?.submit();
    });
});
