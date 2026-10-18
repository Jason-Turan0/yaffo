import {expect} from '@playwright/test';
import {defineWalkthrough, docsFixturePath} from '../../_support';
import {navigate, pairDocs, revokeDocsShares} from '../../_support/doc-fixtures';
import {grantAlbum, cleanupSharing} from '../../_support/sharing-fixtures';

const viewport = {width: 1400, height: 1000};

export default defineWalkthrough({
    page: 'sharing/sharing-folders-albums',
    shots: {
        'share-scope.webp': {
            viewport, goto: '/sharing/settings', clip: '#device-panel .sharing-panel:last-child',
            annotations: {'share-type': '.share-grant-type-controls', 'shared-location': '.share-grant-panel[data-share-type-panel="media_dir"]', 'grant-access': '.share-grant-actions'},
            setup: async page => {
                const {primary, idB} = await pairDocs(page);
                await navigate(page, `${primary}/sharing/devices/${idB}`);
            },
        },
        'shared-with-others.webp': {
            viewport, goto: '/sharing/settings', clip: '#sharing-sidebar',
            annotations: {'outbound-shares': '#sharing-sidebar > .sharing-shares-nav', 'incoming-shares': '#sharing-sidebar-shared-with-me'},
            setup: async page => {
                const state = await grantAlbum(page);
                await navigate(page, `${state.primary}/sharing/devices/${state.idB}`);
                await expect(page.locator('#sharing-sidebar-shared-with-me[hx-get]')).toHaveCount(0);
            }, teardown: cleanupSharing,
        },
    },
    flows: async ({page}) => {
        await navigate(page, '/sharing/settings');
        const {primary, idB} = await pairDocs(page);
        await navigate(page, `${primary}/sharing/devices/${idB}`);
        await page.locator('.share-grant-form button[type=submit]').click();
        await expect(page.locator('.notification.visible')).toContainText('Share grant added');
        await revokeDocsShares(page, primary, 'Family Photos');
        await navigate(page, `${primary}/sharing/devices/${idB}`);
        await page.locator('#share-scope-type + .searchable-select-wrapper .searchable-select-display').click();
        await page.locator('#share-scope-type + .searchable-select-wrapper .searchable-select-option').filter({hasText: /^Folder$/}).click();
        await page.locator('#share-folder-path').fill(docsFixturePath('Family Photos', '2015_chicago_baby_trip'));
        await page.locator('.share-grant-form button[type=submit]').click();
        await expect(page.locator('.notification.visible')).toContainText('Share grant added');
        await revokeDocsShares(page, primary, '2015_chicago_baby_trip');
    },
});
