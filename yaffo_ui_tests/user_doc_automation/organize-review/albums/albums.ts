import {expect} from '@playwright/test';
import {defineWalkthrough} from '../../_support';
import {navigate, BEACH_ALBUM, BEACH_DESCRIPTION, createAlbum, deleteAlbum, pairDocs} from '../../_support/doc-fixtures';

const viewport = {width: 1400, height: 680};

export default defineWalkthrough({
    page: 'organize-review/albums',
    shots: {
        'album-overview.webp': {
            viewport, goto: '/albums', clip: '.albums-container',
            rows: {grid: '.album-tiles', item: '.album-tile', count: 1},
            annotations: {'album-list': '.albums-sidebar', 'album-tiles': '.album-tiles'},
        },
        'new-album.webp': {
            viewport, goto: '/albums', clip: '#newAlbumModal .modal-content', clipPadding: 0,
            setup: async page => {
                await page.locator('#new-album-button').click();
                await page.locator('#new-album-name').fill(BEACH_ALBUM);
                await page.locator('#new-album-description').fill(BEACH_DESCRIPTION);
            },
        },
        'empty-album.webp': {
            viewport, goto: '/albums', clip: '.albums-content',
            setup: async page => { await createAlbum(page); }, teardown: deleteAlbum,
        },
        'select-photos.webp': {
            viewport: {width: 1400, height: 1200}, goto: '/albums', clip: '.albums-add-content',
            rows: {grid: '#add-grid', item: '.photo-card', count: 2},
            setup: async page => {
                const path = await createAlbum(page);
                await navigate(page, `${path}/add?year=2021&page-size=25`);
                await page.locator('#add-grid .photo-card').nth(0).click();
                await page.locator('#add-grid .photo-card').nth(1).click();
            }, teardown: deleteAlbum,
        },
        'filled-album.webp': {
            viewport, goto: '/albums', clip: '.albums-content',
            rows: {grid: '#album-grid', item: '.photo-card', count: 1},
            setup: async page => { await createAlbum(page, true); }, teardown: deleteAlbum,
        },
        'edit-members.webp': {
            viewport, goto: '/albums', clip: '.albums-content',
            rows: {grid: '#album-grid', item: '.photo-card', count: 1},
            annotations: {'member-selection': '#album-selection', 'album-cover': '.album-cover-chip', 'member-grid': '#album-grid'},
            setup: async page => {
                const path = await createAlbum(page, true);
                await navigate(page, `${path}?edit=1`);
                await page.locator('#album-grid .photo-card').first().click();
                await page.locator('#set-cover-button').click();
                await page.waitForURL(/\/albums\/\d+$/);
                await navigate(page, `${path}?edit=1`);
                await page.locator('#album-grid .photo-card').last().click();
            }, teardown: deleteAlbum,
        },
        'album-details.webp': {
            viewport, goto: '/albums', clip: '#editAlbumModal .modal-content', clipPadding: 0,
            annotations: {'album-name': '#edit-album-name', 'album-description': '#edit-album-description'},
            setup: async page => {
                await createAlbum(page, true);
                await page.locator('#edit-album-details-button').click();
            }, teardown: deleteAlbum,
        },
        'share-album.webp': {
            viewport, goto: '/albums', clip: '#shareAlbumModal .modal-content', clipPadding: 0,
            annotations: {'paired-devices': '.share-device-list', 'share-access': '.share-album-hint'},
            setup: async page => {
                const {idB} = await pairDocs(page);
                await createAlbum(page, true);
                await page.locator('#share-album-button').click();
                await page.locator(`#shareAlbumModal input[value="${idB}"]`).check();
            }, teardown: deleteAlbum,
        },
    },
    flows: async ({page}) => {
        await navigate(page, '/albums');
        const path = await createAlbum(page, true);
        try {
            await navigate(page, `${path}?edit=1`);
            const cards = page.locator('#album-grid .photo-card');
            const first = await cards.first().getAttribute('data-select-id');
            const last = await cards.last().getAttribute('data-select-id');
            const source = await cards.last().boundingBox();
            const target = await cards.first().boundingBox();
            if (!source || !target) throw new Error('Album members are not visible');
            await page.mouse.move(source.x + source.width / 2, source.y + source.height / 2);
            await page.mouse.down();
            await page.mouse.move(target.x + 10, target.y + target.height / 2, {steps: 15});
            await page.mouse.up();
            await navigate(page, `${path}?edit=1&select_id=${last}`);
            await page.locator('#set-cover-button').click();
            await page.waitForURL(/\/albums\/\d+$/);
            await expect(page.locator('.album-cover-chip')).toBeVisible();
            await navigate(page, `${path}?edit=1&select_id=${first}`);
            await page.locator('#remove-from-album-button').click();
            await page.locator('#confirm-dialog-confirm').click();
            // "Remove from album" is a form POST that navigates back to the album in
            // edit mode. The modal buttons only answer once that document's app
            // bootstrap has run (it sets html[data-app-ready] after attaching them),
            // so re-enter through navigate() instead of clicking into a page whose
            // listeners are not attached yet — that left the Edit details modal
            // rendered but never opened.
            await page.waitForURL(/\/albums\/\d+\?edit=1$/);
            await expect(page.locator('#album-grid .photo-card')).toHaveCount(1);
            await navigate(page, `${path}?edit=1`);
            await page.locator('#edit-album-details-button').click();
            await expect(page.locator('#editAlbumModal')).toHaveClass(/active/);
            await page.locator('#edit-album-description').fill('A hand-picked collection');
            await page.locator('#editAlbumModal button[type=submit]').click();
            // Same reason: wait out the details POST rather than racing the reload.
            await page.waitForURL(/\/albums\/\d+$/);
            await navigate(page, path);
            await page.locator('#delete-album-button').click();
            await page.locator('#confirm-dialog-confirm').click();
            await page.waitForURL(/\/albums$/);
        } catch (error) {
            await deleteAlbum(page);
            throw error;
        }
    },
});
