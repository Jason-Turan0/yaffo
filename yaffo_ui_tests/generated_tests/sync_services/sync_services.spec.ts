import { expect, test, type Page } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { resolve } from 'node:path';

const createdSlugs: string[] = [];
const createdSyncSlugs: string[] = [];

test.describe.configure({ mode: 'serial' });

async function createService(page: Page, name: string): Promise<string> {
  await page.goto('/utilities/automations');
  await page.locator('#new-automation-button').click();
  const modal = page.locator('#newAutomationModal');
  await expect(modal).toHaveClass(/active/);
  await modal.locator('input[name="kind"][value="service"]').check();
  await modal.locator('#new-automation-name').fill(name);
  await modal.locator('button[type="submit"]').click();
  await expect(page).toHaveURL(/\/utilities\/automations\/services\/[^/]+$/);
  await expect(page.locator('.page-header h1')).toHaveText(name);
  const slug = new URL(page.url()).pathname.split('/').pop()!;
  createdSlugs.push(slug);
  return slug;
}

async function deleteService(page: Page, slug: string): Promise<void> {
  await page.goto(`/utilities/automations/services/${slug}`);
  const deleteButton = page.locator('[data-confirm-delete] button[type="submit"]');
  await expect(deleteButton).toBeEnabled();
  await deleteButton.click();
  const dialog = page.locator('#global-confirm-dialog.active');
  await expect(dialog).toBeVisible();
  await expect(dialog).toContainText('Delete this service?');
  await page.locator('#confirm-dialog-confirm').click();
  await expect(page).toHaveURL(/\/utilities\/automations(?:\/[^/]+)?$/);
  await expect(page.locator(`#automations-nav a[href="/utilities/automations/services/${slug}"]`)).toHaveCount(0);
}

function runFlickrFixture(slug: string, ...args: string[]): string {
  return execFileSync('python', ['-m', 'yaffo_ui_tests.scripts.seed_flickr_service', slug, ...args], {
    cwd: resolve(process.cwd(), '..'), env: process.env, encoding: 'utf8', stdio: 'pipe',
  });
}

async function createFlickrSync(page: Page, serviceName: string, name: string): Promise<string> {
  await page.goto('/utilities/automations');
  await page.locator('#new-automation-button').click();
  const modal = page.locator('#newAutomationModal');
  await modal.locator('input[name="kind"][value="send"]').check();
  const choices = modal.locator('[data-sync-services="send"]');
  await choices.locator('label.choice-card').filter({ hasText: serviceName }).locator('input').check();
  await modal.locator('#new-automation-name').fill(name);
  await modal.locator('button[type="submit"]').click();
  await expect(page).toHaveURL(/\/utilities\/automations\/[^/]+$/);
  await expect(page.locator('.page-header h1')).toContainText(name);
  const slug = new URL(page.url()).pathname.split('/').pop()!;
  createdSyncSlugs.push(slug);
  return slug;
}

async function deleteSync(page: Page, slug: string): Promise<void> {
  await page.goto(`/utilities/automations/${slug}`);
  await page.locator('#delete-automation-button').click();
  const dialog = page.locator('#global-confirm-dialog.active');
  await expect(dialog).toBeVisible();
  await page.locator('#confirm-dialog-confirm').click();
  await expect(page).toHaveURL(/\/utilities\/automations(?:\/[^/]+)?$/);
}

test.afterAll(async ({ browser }) => {
  const baseURL = process.env.BASE_URL || 'http://127.0.0.1:5001';
  const context = await browser.newContext({ baseURL });
  for (const slug of createdSyncSlugs.reverse()) {
    await context.request.post(`/utilities/automations/${slug}/delete`).catch(() => {});
  }
  for (const slug of createdSlugs.reverse()) {
    await context.request.post(`/utilities/automations/services/${slug}/delete`).catch(() => {});
  }
  await context.close();
});

test('new_service_flow_creates_an_unpublished_draft', async ({ page }) => {
  const name = `UI Service Draft ${Date.now()}`;
  const slug = await createService(page, name);
  try {
    await expect(page.locator('.page-header')).toContainText('Draft');
    await expect(page.locator('#automations-nav')).toContainText('Services');
    await expect(page.locator('#automations-nav a').filter({ hasText: name })).toContainText('Draft');

    const tabs = page.locator('.service-tabs .service-tab');
    await expect(tabs).toHaveText(['Overview', 'Account', 'Access', 'Code', 'Test']);
    await expect(page.locator('#service-publish')).toHaveCount(0);
    await expect(page.locator('#service-chat-messages .chat-message')).toHaveCount(0);

    await page.getByRole('link', { name: 'Account' }).click();
    await expect(page.locator('.service-panel')).toContainText('Appears once the builder knows how this service signs in.');
    await page.getByRole('link', { name: 'Access' }).click();
    await expect(page.locator('.service-panel')).toContainText('No web calls yet.');
    await page.getByRole('link', { name: 'Code' }).click();
    await expect(page.locator('.service-panel')).toContainText('No code yet.');
    await page.getByRole('link', { name: 'Test' }).click();
    await expect(page.locator('.service-panel')).toContainText('Nothing to test yet.');

    await deleteService(page, slug);
  } finally {
    await page.request.post(`/utilities/automations/services/${slug}/delete`).catch(() => {});
  }
});

test('builder_chat_shows_progress_and_transcript', async ({ page }) => {
  const name = `UI Service Chat ${Date.now()}`;
  const slug = await createService(page, name);
  try {
    const prompt = 'Send selected albums to Flickr as sets';
    let generating = false;
    await page.route(`**/utilities/automations/services/${slug}/chat`, route => {
      generating = true;
      return route.fulfill({
        status: 202,
        contentType: 'application/json',
        body: JSON.stringify({ slug }),
      });
    });
    await page.route(`**/utilities/automations/services/${slug}/status`, route => route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        slug,
        status: generating ? 'IN_PROGRESS' : 'READY',
        started_at: generating ? new Date().toISOString() : null,
        working_code: null,
        published_code: null,
        messages: generating ? [
          { id: 1, type: 'user', content: prompt },
          { id: 2, type: 'assistant', content: 'I am preparing the Flickr sets adapter.' },
        ] : [],
      }),
    }));

    await page.locator('#service-chat-message').fill(prompt);
    await page.locator('#service-chat-form button[type="submit"]').click();
    await expect(page.locator('#service-chat-status')).toBeVisible();
    await expect(page.locator('#service-chat-message')).toBeDisabled();
    await expect(page.locator('#service-chat-messages .chat-message-user')).toContainText(prompt);
    await expect(page.locator('#service-chat-messages .chat-message-assistant'))
      .toContainText('preparing the Flickr sets adapter');

    // End the intercepted run after the busy state and transcript have rendered.
    generating = false;
    await expect(page.locator('#service-chat-status')).toBeHidden({ timeout: 20_000 });
    await expect(page).toHaveURL(new RegExp(`/utilities/automations/services/${slug}$`));
    await expect(page.locator('#service-chat-message')).toBeEnabled();
  } finally {
    await page.request.post(`/utilities/automations/services/${slug}/delete`).catch(() => {});
  }
});

test('generated_adapter_overview_is_saved_but_unpublished_service_is_not_a_send_target', async ({ page }) => {
  const name = `UI Service Generated ${Date.now()}`;
  const slug = await createService(page, name);
  try {
    const prompt = 'Send selected albums to Flickr as sets';
    let generating = false;
    await page.route(`**/utilities/automations/services/${slug}/chat`, async route => {
      generating = true;
      // Mock the model request but persist the checked-in, statically checked adapter
      // fixture so completion reloads the real service page with a working draft.
      runFlickrFixture(slug);
      await route.fulfill({
        status: 202,
        contentType: 'application/json',
        body: JSON.stringify({ slug }),
      });
    });
    await page.route(`**/utilities/automations/services/${slug}/status`, route => route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        slug,
        status: generating ? 'IN_PROGRESS' : 'READY',
        started_at: generating ? new Date().toISOString() : null,
        working_code: null,
        published_code: null,
        messages: generating ? [
          { id: 1, type: 'user', content: prompt },
          { id: 2, type: 'assistant', content: 'I have prepared the Flickr sets adapter.' },
        ] : [],
      }),
    }));

    await page.locator('#service-chat-message').fill(prompt);
    await page.locator('#service-chat-form button[type="submit"]').click();
    await expect(page.locator('#service-chat-status')).toBeVisible();
    await expect(page.locator('#service-chat-messages .chat-message-assistant'))
      .toContainText('prepared the Flickr sets adapter');
    generating = false;
    await expect(page.locator('#service-chat-status')).toBeHidden({ timeout: 20_000 });
    await expect(page).toHaveURL(new RegExp(`/utilities/automations/services/${slug}$`));

    const overview = page.locator('.service-facts');
    await expect(overview).toContainText('Sends photos');
    await expect(overview).toContainText('Service sign-in page');
    await expect(overview).toContainText('Flickr set');
    await expect(overview).toContainText('Recover');
    await expect(page.locator('.page-header')).toContainText('Draft');

    await page.goto('/utilities/automations');
    await page.locator('#new-automation-button').click();
    const modal = page.locator('#newAutomationModal');
    await modal.locator('input[name="kind"][value="send"]').check();
    const sendServices = modal.locator('[data-sync-services="send"] .choice-card-title');
    expect(await sendServices.allTextContents()).not.toContain(name);
  } finally {
    await page.request.post(`/utilities/automations/services/${slug}/delete`).catch(() => {});
  }
});

test('builder_restores_the_prompt_when_generation_cannot_start', async ({ page }) => {
  const name = `UI Service No Key ${Date.now()}`;
  const slug = await createService(page, name);
  try {
    const prompt = 'Import albums from my photo service';
    await page.route(`**/utilities/automations/services/${slug}/chat`, route => route.fulfill({
      status: 400,
      contentType: 'application/json',
      body: JSON.stringify({ error: 'No API key configured. Add your provider API key in Settings → AI Generation.' }),
    }));
    await page.locator('#service-chat-message').fill(prompt);
    await page.locator('#service-chat-form button[type="submit"]').click();
    await expect(page.locator('#app-notification .notification-message'))
      .toContainText('No API key configured');
    await expect(page.locator('#service-chat-message')).toHaveValue(prompt);
    await expect(page.locator('#service-chat-message')).toBeEnabled();
    await expect(page.locator('#service-chat-status')).toBeHidden();
  } finally {
    await page.request.post(`/utilities/automations/services/${slug}/delete`).catch(() => {});
  }
});

test('builder_can_approve_only_selected_documentation_domains', async ({ page }) => {
  const name = `UI Service Permission ${Date.now()}`;
  const slug = await createService(page, name);
  try {
    const prompt = 'Build an adapter using the provider API documentation';
    let generating = false;
    let approved = false;
    await page.route(`**/utilities/automations/services/${slug}/chat`, route => {
      generating = true;
      return route.fulfill({
        status: 202,
        contentType: 'application/json',
        body: JSON.stringify({ slug }),
      });
    });
    await page.route(`**/utilities/automations/services/${slug}/status`, route => route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        slug,
        status: generating ? 'IN_PROGRESS' : 'READY',
        started_at: generating ? new Date().toISOString() : null,
        working_code: null,
        published_code: null,
        messages: generating ? [
          { id: 1, type: 'user', content: prompt },
          {
            id: 42,
            type: 'permission',
            content: JSON.stringify({
              kind: 'docs',
              status: approved ? 'partial' : 'pending',
              domains: { 'flickr.com': ['https://flickr.com/services/api/'], 'api.flickr.com': ['https://api.flickr.com/services/rest/'] },
              allowed: approved ? ['flickr.com'] : [],
              denied: approved ? ['api.flickr.com'] : [],
            }),
          },
        ] : [],
      }),
    }));
    await page.route(`**/utilities/automations/services/${slug}/permissions/42`, async route => {
      expect(route.request().method()).toBe('POST');
      expect(JSON.parse(route.request().postData() || '{}')).toEqual({
        decision: 'allow',
        domains: ['flickr.com'],
      });
      approved = true;
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ started: false }),
      });
    });

    await page.locator('#service-chat-message').fill(prompt);
    await page.locator('#service-chat-form button[type="submit"]').click();
    const permission = page.locator('.service-permission');
    await expect(permission).toContainText('flickr.com');
    await expect(permission).toContainText('api.flickr.com');
    const privateDomain = permission.getByRole('checkbox', { name: /api\.flickr\.com/ });
    await expect(privateDomain).toBeChecked();
    await privateDomain.uncheck();
    await permission.getByRole('button', { name: 'Allow selected' }).click();
    await expect(permission).toHaveClass(/is-answered/);
    await expect(permission).toContainText('Allowed: flickr.com');
    await expect(permission).toContainText('api.flickr.com');
  } finally {
    await page.request.post(`/utilities/automations/services/${slug}/delete`).catch(() => {});
  }
});

test('flickr_test_account_oauth_and_adapter_test_use_the_local_fake', async ({ page }) => {
  const fakeUrl = process.env.FAKE_FLICKR_URL;
  expect(fakeUrl, 'test:spec starts scripts/fake_flickr.py for this suite').toBeTruthy();
  const name = `UI Flickr Service ${Date.now()}`;
  const slug = await createService(page, name);
  try {
    runFlickrFixture(slug);

    await page.getByRole('link', { name: 'Account' }).click();
    await expect(page.locator('#service-return-address')).toHaveValue(/127\.0\.0\.1/);
    await page.locator('#service-client-id').fill('flickr-app-key');
    await page.locator('#service-client-secret').fill('flickr-app-secret');
    await page.getByRole('button', { name: 'Save credentials' }).click();
    await expect(page.getByRole('status')).toContainText('App credentials saved');
    await expect(page.locator('#service-client-secret')).toHaveValue('');
    await expect(page.locator('#service-client-id')).toHaveValue('');

    await page.getByRole('button', { name: /^Sign in to / }).click();
    await expect(page).toHaveURL(new RegExp(`^${fakeUrl!.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}/services/oauth/authorize`));
    await page.locator('#approve').click();
    await expect(page).toHaveURL(new RegExp(`/utilities/automations/services/${slug}\\?tab=account`));
    await expect(page.locator('.service-account-row')).toContainText('jturan');
    await expect(page.locator('.service-account-row')).toContainText('Signed in');

    runFlickrFixture(slug, '--request-live-reads');
    await page.reload();
    const livePermission = page.locator('.service-permission');
    await expect(livePermission).toContainText('read-only calls');
    await livePermission.getByRole('button', { name: 'Allow', exact: true }).click();
    await expect(livePermission).toHaveClass(/is-answered/);
    const builderFinished = page.waitForEvent('framenavigated', {
      predicate: frame => frame === page.mainFrame(),
    });
    runFlickrFixture(slug, '--ready');
    await builderFinished;
    await page.waitForLoadState('domcontentloaded');
    await expect(page.locator('.service-permission')).toContainText('Live read-only tests allowed');

    await page.getByRole('link', { name: 'Access' }).click();
    const access = page.locator('.service-endpoint-rows');
    await expect(access).toContainText('https://api.flickr.com/services/rest');
    await expect(access).toContainText('https://up.flickr.com/services/upload/');
    await page.locator('#service-publish').click();
    const publishDialog = page.locator('#servicePublishModal.active');
    await expect(publishDialog).toContainText('changes things');
    await expect(publishDialog).toContainText('reads');
    await publishDialog.locator('button[type="submit"]').click();
    await expect(page.locator('.page-header')).toContainText('Published');
    await expect(page.locator('#service-publish')).toHaveCount(0);

    await page.getByRole('link', { name: 'Test' }).click();
    await expect(page.locator('#service-option-set')).toBeVisible();
    await page.locator('#service-test').click();
    const result = page.locator('#service-test-result');
    await expect(result).toContainText('Recorded', { timeout: 15_000 });
    await expect(result).toContainText('Read · 200');
    await expect(result).toContainText('https://up.flickr.com/services/upload/');

    const fakeState = await page.request.get(`${fakeUrl}/_admin/state`).then(response => response.json());
    expect(fakeState.counters.calls).toBe(2); // OAuth login plus the Test's live photoset read.
    expect(fakeState.counters.uploads || 0).toBe(0);
    expect(fakeState.counters.sets_created || 0).toBe(0);
    await page.getByRole('link', { name: 'Account' }).click();
    await expect(page.locator('#service-client-secret')).toHaveValue('');
    await expect(page.locator('.service-account-row').first()).not.toContainText('flickr-app-secret');
    await expect(page.locator('.service-account-row').filter({ hasText: 'Live reads allowed' })).toBeVisible();

    const syncSlug = await createFlickrSync(page, name, `UI Flickr Sync ${Date.now()}`);
    execFileSync('python', ['-m', 'yaffo_ui_tests.scripts.seed_flickr_sync', syncSlug], {
      cwd: resolve(process.cwd(), '..'), env: process.env, stdio: 'pipe',
    });
    await page.goto(`/utilities/automations/${syncSlug}`);
    await expect(page.locator('#sync-run-now')).toBeVisible();

    await page.goto(`/utilities/automations/services/${slug}?tab=overview`);
    await expect(page.locator('.service-facts')).toContainText('1 sync');
    await expect(page.locator('[data-confirm-delete] button[type="submit"]')).toBeDisabled();

    runFlickrFixture(slug, '--wider');
    await page.goto(`/utilities/automations/services/${slug}?tab=web`);
    await expect(page.locator('.service-endpoint-rows')).toContainText('/services/rest/extra');
    await page.locator('#service-publish').click();
    const republishDialog = page.locator('#servicePublishModal.active');
    await expect(republishDialog).toContainText('1 sync uses this service');
    await expect(republishDialog).toContainText('/services/rest/extra');
    await republishDialog.locator('button[type="submit"]').click();
    await expect(page.locator('.page-header')).toContainText('Published');

    await page.goto(`/utilities/automations/${syncSlug}`);
    await expect(page.locator('#sync-enable-review')).toBeVisible();
    await expect(page.locator('#sync-run-now')).toHaveCount(0);
    await page.locator('#sync-enable-review').click();
    const accessReview = page.locator('#syncEnableModal.active');
    await expect(accessReview).toContainText('Turn on this sync?');
    await expect(accessReview.getByRole('button', { name: 'Enable' })).toBeVisible();

    await deleteSync(page, syncSlug);
    await page.goto(`/utilities/automations/services/${slug}`);
    await expect(page.locator('[data-confirm-delete] button[type="submit"]')).toBeEnabled();
    await deleteService(page, slug);
  } finally {
    await page.request.post(`/utilities/automations/services/${slug}/delete`).catch(() => {});
  }
});
