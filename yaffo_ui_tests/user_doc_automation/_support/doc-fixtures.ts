import {expect, type Page} from '@playwright/test';

export const BEACH_ALBUM = 'Beach weekend';
export const BEACH_DESCRIPTION = 'Our July trip to the Gulf';

export async function csrf(page: Page): Promise<string> {
    return page.evaluate(() => (window as unknown as {APP_CONFIG: {csrfToken: string}}).APP_CONFIG.csrfToken);
}

export async function createAlbum(page: Page, fill = false): Promise<string> {
    await navigate(page, '/albums');
    await page.locator('#new-album-button').click();
    await page.locator('#new-album-name').fill(BEACH_ALBUM);
    await page.locator('#new-album-description').fill(BEACH_DESCRIPTION);
    await page.locator('#newAlbumModal button[type=submit]').click();
    await page.waitForURL(/\/albums\/\d+$/);
    const path = new URL(page.url()).pathname;
    pageState.set(page, path);
    if (fill) {
        await navigate(page, `${path}/add?year=2021&page-size=25`);
        await page.locator('#add-grid .photo-card').nth(0).click();
        await page.locator('#add-grid .photo-card').nth(1).click();
        await page.locator('#add-to-album-button').click();
        await expect(page.locator('.add-photos-confirmation')).toContainText('2 photos added');
        await navigate(page, path);
    }
    return path;
}

const pageState = new WeakMap<Page, string>();
export async function deleteAlbum(page: Page): Promise<void> {
    const path = pageState.get(page);
    if (!path) return;
    // Cleanup uses the real endpoint so a modal left open by a shot cannot block it.
    const response = await page.request.post(`${path}/delete`, {form: {csrf_token: await csrf(page)}});
    expect(response.ok()).toBeTruthy();
    pageState.delete(page);
}

export interface PairedDocs {
    primary: string;
    peer: string;
    idA: string;
    idB: string;
}

export function docsOrigins(page: Page): {primary: string; peer: string} {
    const primary = new URL(page.url()).origin;
    const peer = new URL(primary);
    peer.port = String(Number(peer.port) + 1);
    return {primary, peer: peer.origin};
}

export async function pairingCode(page: Page): Promise<string> {
    await page.locator('html[data-app-ready]').waitFor({state: 'attached'});
    await page.getByRole('button', {name: 'Generate pairing code', exact: true}).click();
    const field = page.locator('#pairing-code-box .pairing-code-text');
    await expect(field).toBeVisible();
    return field.inputValue();
}

export async function pairDocs(page: Page): Promise<PairedDocs> {
    const origins = docsOrigins(page);
    const a = await page.context().newPage();
    const b = await page.context().newPage();
    try {
        await navigate(a, `${origins.primary}/sharing/settings`);
        await navigate(b, `${origins.peer}/sharing/settings`);
        const idA = (await a.locator('.device-status-strip code.device-id').first().innerText()).trim();
        const idB = (await b.locator('.device-status-strip code.device-id').first().innerText()).trim();
        // Refresh trust if a previous run stopped after revoking the demonstration peer.
        await b.locator('.accept-pairing-form textarea').fill(await pairingCode(a));
        await b.locator('.accept-pairing-form button[type=submit]').click();
        await expect(b.locator('.notification.visible')).toContainText('Paired with', {timeout: 30000});
        for (const [tab, origin, id, name] of [
            [a, origins.primary, idB, 'Family laptop'],
            [b, origins.peer, idA, 'Photo library'],
        ] as const) {
            await navigate(tab, `${origin}/sharing/devices/${id}`);
            await tab.locator('#device-display-name').fill(name);
            await tab.locator('.device-rename-form button').click();
            await expect(tab.locator('.page-header h1')).toContainText(name);
        }
        return {...origins, idA, idB};
    } finally {
        await a.close();
        await b.close();
    }
}

export async function revokeDocsShares(page: Page, primary: string, label: string): Promise<void> {
    await navigate(page, `${primary}/sharing/settings`);
    const rows = page.locator('.sharing-share-nav-item').filter({hasText: label});
    while (await rows.count()) {
        await rows.first().locator('button').click();
        await page.locator('#confirm-dialog-confirm').click();
        await expect(rows).toHaveCount(0);
    }
}

export async function navigate(page: Page, url: string): Promise<void> {
    await page.goto(url);
    // Modal and confirmation handlers attach after the asynchronous app bootstrap.
    await page.locator('html[data-app-ready]').waitFor({state: 'attached'});
}
