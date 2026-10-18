import {expect, type Page} from '@playwright/test';
import {docsFixturePath} from './index';
import {navigate, pairDocs, revokeDocsShares, type PairedDocs} from './doc-fixtures';

const states = new WeakMap<Page, PairedDocs>();
const sharedAlbum = "Maya's Big Game";

export async function grantAlbum(page: Page): Promise<PairedDocs> {
    const state = await pairDocs(page);
    states.set(page, state);
    await navigate(page, `${state.primary}/albums`);
    await page.locator('.album-tile').filter({hasText: sharedAlbum}).click();
    await page.waitForURL(/\/albums\/\d+$/);
    await page.locator('html[data-app-ready]').waitFor({state: 'attached'});
    await page.locator('#share-album-button').click();
    await page.locator(`#shareAlbumModal input[value="${state.idB}"]`).check();
    await page.locator('#shareAlbumModal button[type=submit]').click();
    await expect(page.locator('#shareAlbumModal')).not.toBeVisible();
    return state;
}

export async function remoteGallery(page: Page, select = false): Promise<void> {
    const state = await grantAlbum(page);
    await navigate(page, `${state.peer}/sharing/settings`);
    await page.locator('#shared-download-dir').fill(docsFixturePath('peer', 'Downloaded Photos'));
    await Promise.all([
        page.waitForResponse(response => response.url().endsWith('/sharing/download-directory') && response.request().method() === 'POST'),
        page.locator('.share-download-dir-form button[type=submit]').click(),
    ]);
    const row = page.locator('#sharing-sidebar-shared-with-me .sharing-share-nav-item').filter({hasText: sharedAlbum});
    await expect(row).toBeVisible({timeout: 20000});
    await row.locator('a').click();
    await page.locator('html[data-app-ready]').waitFor({state: 'attached'});
    await expect(page.locator('.remote-photo-card')).toHaveCount(3);
    await expect(page.locator('.remote-photo-card img.preview-pending')).toHaveCount(0, {timeout: 30000});
    if (select) await page.locator('.remote-photo-card').first().click();
}

export async function cleanupSharing(page: Page): Promise<void> {
    const state = states.get(page);
    if (!state) return;
    // Batches are in-memory UI records; Dismiss removes the record, never the copy.
    await navigate(page, `${state.peer}/sharing/devices/${state.idA}`);
    const dismiss = page.locator('.transfer-batch button').filter({hasText: 'Dismiss'});
    while (await dismiss.count()) {
        await dismiss.first().click();
        await expect(page.locator('.transfer-batch')).toHaveCount(0);
    }
    await revokeDocsShares(page, state.primary, sharedAlbum);
    states.delete(page);
}

