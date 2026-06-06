window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};
window.PHOTO_ORGANIZER.initPageGrid = (pageId, config) => {
    const grid = GridStack.init({
        column: 12,
        cellHeight: 80,
        margin: 8,
        float: true,
        handle: '.block-header',
        columnOpts: { breakpoints: [{ w: 768, c: 1 }] }
    });

    const persistLayout = async () => {
        const layout = grid.save(false).map((node) => ({
            id: Number(node.id),
            x: node.x,
            y: node.y,
            w: node.w,
            h: node.h
        }));
        await fetch(config.buildUrl('pages_layout', { page_id: pageId }), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ layout })
        });
    };

    const wireDelete = (el) => {
        const button = el.querySelector('.block-delete');
        if (!button) return;
        button.addEventListener('click', async (event) => {
            event.stopPropagation();
            const confirmed = await window.PHOTO_ORGANIZER.confirmDialog({
                title: 'Delete Block',
                message: 'Remove this block?',
                confirmText: 'Delete',
                confirmClass: 'btn-danger'
            });
            if (!confirmed) return;
            await fetch(
                config.buildUrl('pages_delete_block', { page_id: pageId, block_id: button.dataset.blockId }),
                { method: 'POST' }
            );
            grid.removeWidget(el);
        });
    };

    const addBlock = async () => {
        const response = await fetch(config.buildUrl('pages_add_block', { page_id: pageId }), { method: 'POST' });
        const html = await response.text();
        const wrapper = document.createElement('div');
        wrapper.innerHTML = html.trim();
        const el = wrapper.firstElementChild;
        grid.addWidget(el);
        wireDelete(el);
    };

    grid.on('change', persistLayout);

    const addButton = document.getElementById('add-block-button');
    if (addButton) {
        addButton.addEventListener('click', addBlock);
    }

    grid.engine.nodes.forEach((node) => wireDelete(node.el));

    return { grid };
};