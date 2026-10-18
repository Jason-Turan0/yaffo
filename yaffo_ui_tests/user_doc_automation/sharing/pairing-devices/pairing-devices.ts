import {expect} from '@playwright/test';
import {defineWalkthrough} from '../../_support';
import {navigate, docsOrigins, pairDocs, pairingCode} from '../../_support/doc-fixtures';

const viewport = {width: 1400, height: 1000};

export default defineWalkthrough({
    page: 'sharing/pairing-devices',
    shots: {
        'pairing-code.webp': {
            viewport, goto: '/sharing/settings', clip: '#devices-section .sharing-panel:last-child',
            ignoreRegions: ['.pairing-code-qr', '.pairing-code-text', '.pairing-code-expiry'],
            setup: async page => { await pairingCode(page); },
        },
        'enter-code.webp': {
            viewport, goto: '/sharing/settings', clip: '.pairing-form:has(.accept-pairing-form)',
            ignoreRegions: ['.accept-pairing-form textarea'],
            setup: async page => {
                const {peer} = docsOrigins(page);
                const code = await pairingCode(page);
                await navigate(page, `${peer}/sharing/settings`);
                await page.locator('.accept-pairing-form textarea').fill(code);
            },
        },
        'paired-device.webp': {
            viewport, goto: '/sharing/settings', clip: '.sharing-content',
            rows: {grid: '#device-panel', item: '.sharing-panel', count: 2},
            ignoreRegions: ['.device-identity'],
            setup: async page => {
                const {primary, idB} = await pairDocs(page);
                await navigate(page, `${primary}/sharing/devices/${idB}`);
            },
        },
    },
    flows: async ({page}) => {
        await navigate(page, '/sharing/settings');
        const {primary, idB} = await pairDocs(page);
        await navigate(page, `${primary}/sharing/devices/${idB}`);
        await page.locator('[data-sharing-confirm="revoke"]').click();
        await page.locator('#confirm-dialog-confirm').click();
        await expect(page.locator('.page-header')).toContainText('Revoked');
        await page.locator('[data-sharing-confirm="delete"]').click();
        await page.locator('#confirm-dialog-confirm').click();
        await page.waitForURL('**/sharing/settings');
        await pairDocs(page);
    },
});
