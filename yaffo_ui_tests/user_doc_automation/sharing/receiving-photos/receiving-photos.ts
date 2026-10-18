import {expect} from '@playwright/test';
import {defineWalkthrough, docsFixturePath} from '../../_support';
import {navigate, docsOrigins, pairDocs, revokeDocsShares} from '../../_support/doc-fixtures';
import {remoteGallery, cleanupSharing} from '../../_support/sharing-fixtures';

const viewport = {width: 1400, height: 1000};

export default defineWalkthrough({
    page: 'sharing/receiving-photos',
    shots: {
        'download-directory.webp': {
            viewport, goto: '/sharing/settings', clip: '#remote-download-directory-panel',
            setup: async page => {
                const {peer} = docsOrigins(page);
                await navigate(page, `${peer}/sharing/settings`);
                await page.locator('#shared-download-dir').fill(docsFixturePath('peer', 'Downloaded Photos'));
            },
        },
        'select-shared-photos.webp': {
            viewport, goto: '/sharing/settings', clip: '.photo-gallery',
            rows: {grid: '.remote-photo-grid', item: '.remote-photo-card', count: 1},
            setup: async page => { await remoteGallery(page, true); }, teardown: cleanupSharing,
        },
        'completed-transfer.webp': {
            viewport, goto: '/sharing/settings', clip: '.photo-gallery',
            ignoreRegions: ['.transfer-progress-meta'],
            setup: async page => {
                await remoteGallery(page, true);
                await page.locator('#pull-selected-button').click();
                await expect(page.locator('.transfer-batch[data-state="completed"]')).toBeVisible({timeout: 30000});
            }, teardown: cleanupSharing,
        },
        'remote-gallery.webp': {
            viewport, goto: '/sharing/settings', clip: '.main-container-layout',
            annotations: {'remote-filters': '#filter-form', 'remote-selection': '#remote-selection', 'remote-photos': '.remote-photo-grid'},
            setup: async page => { await remoteGallery(page); }, teardown: cleanupSharing,
        },
    },
    flows: async ({page}) => {
        await navigate(page, '/sharing/settings');
        const state = await pairDocs(page);
        // Visit the remote gallery on the primary too, so server-side dependency
        // observation includes templates otherwise rendered only by the peer.
        const b = await page.context().newPage();
        try {
            await navigate(b, `${state.peer}/sharing/devices/${state.idA}`);
            await b.locator('.share-grant-form button[type=submit]').click();
            await expect(b.locator('.notification.visible')).toContainText('Share grant added');
            await navigate(page, `${state.primary}/sharing/settings`);
            const row = page.locator('#sharing-sidebar-shared-with-me .sharing-share-nav-item').filter({hasText: 'Family Photos'});
            await expect(row).toBeVisible({timeout: 20000});
            await row.locator('a').click();
            await expect(page.locator('.remote-photo-grid')).toBeVisible();
        } finally {
            await revokeDocsShares(b, state.peer, 'Family Photos');
            await b.close();
        }
    },
});
