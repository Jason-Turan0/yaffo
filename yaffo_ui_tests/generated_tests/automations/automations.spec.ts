import { test, expect, Page, Locator } from '@playwright/test';

type DryRunAction = {
  name: string;
  summary: string;
  args?: unknown[];
};

type DryRunPayload = {
  success: boolean;
  code_source: string;
  context: { media_item_ids?: number[] };
  actions: DryRunAction[];
  error?: string;
  value?: unknown;
};

const CUSTOM_AUTOMATION_NAME = `UI Test Automation ${Date.now()}`;

// Shared across the serial suite: the slug of the custom automation created in the
// first test and deleted in the last one.
let customSlug: string | null = null;

test.describe.configure({ mode: 'serial', timeout: 60_000 });

async function openAutomation(page: Page, slug: string): Promise<void> {
  await page.goto(`/utilities/automations/${slug}`);
  await expect(page.locator('.page-header')).toBeVisible();
}

function automationsSidebar(page: Page): Locator {
  return page.locator('nav.utilities-sidebar').filter({
    has: page.locator('h2', { hasText: 'Automations' }),
  });
}

function enableToggleButton(page: Page): Locator {
  // The template renders the label with surrounding whitespace, so anchor loosely.
  return page.locator('.automation-actions button').filter({ hasText: /^\s*(Enable|Disable)\s*$/ }).first();
}

// Click the Enable/Disable toggle and follow the HX-Refresh reload through: wait for
// the POST to actually fire, for the label to flip, and for the reloaded document to
// reach 'load' — htmx re-binds on DOMContentLoaded, so clicking again earlier can hit
// an unbound button and silently do nothing.
async function toggleEnabledTo(page: Page, expectedLabel: string): Promise<void> {
  await Promise.all([
    page.waitForResponse(response =>
      response.url().includes('/enabled') && response.request().method() === 'POST'),
    enableToggleButton(page).click(),
  ]);
  await expect(enableToggleButton(page)).toHaveText(expectedLabel, { timeout: 15_000 });
  await page.waitForLoadState('load');
}

function triggerAddArea(page: Page): Locator {
  return page.locator('#automation-triggers .automation-trigger-add');
}

function triggerRows(page: Page, kind: 'Schedule' | 'Event'): Locator {
  return page.locator('.automation-trigger-row').filter({
    has: page.locator('.automation-trigger-kind', { hasText: kind }),
  });
}

// Create the shared custom automation through the sidebar's "New automation" modal
// if this suite hasn't already; returns its slug.
async function ensureCustomAutomation(page: Page): Promise<string> {
  if (customSlug) return customSlug;
  await page.goto('/utilities/automations');
  await page.locator('#new-automation-button').click();
  const modal = page.locator('#newAutomationModal');
  await expect(modal).toHaveClass(/active/);
  await modal.locator('#new-automation-name').fill(CUSTOM_AUTOMATION_NAME);
  await modal.locator('button[type="submit"]').click();
  await expect(page.locator('.page-header')).toContainText(CUSTOM_AUTOMATION_NAME);
  customSlug = new URL(page.url()).pathname.split('/').pop()!;
  return customSlug;
}

async function readAssignFacesThreshold(page: Page): Promise<string> {
  await openAutomation(page, 'auto_assign_faces');
  await page.locator('#configure-automation-button').click();
  const modal = page.locator('#configureAutomationModal');
  await expect(modal).toHaveClass(/active/);
  const value = await modal.locator('#config-threshold').inputValue();
  await modal.locator('.modal-actions [name="cancel"]').click();
  await expect(modal).not.toHaveClass(/active/);
  return value;
}

async function saveAssignFacesThreshold(page: Page, threshold: string): Promise<void> {
  await openAutomation(page, 'auto_assign_faces');
  await page.locator('#configure-automation-button').click();
  const modal = page.locator('#configureAutomationModal');
  await expect(modal).toHaveClass(/active/);
  await modal.locator('#config-threshold').fill(threshold);
  await Promise.all([
    page.waitForResponse(response =>
      response.url().includes('/utilities/automations/auto_assign_faces/config')
      && response.request().method() === 'POST'),
    modal.locator('button[type="submit"]').click(),
  ]);
  await expect(page).toHaveURL(/\/utilities\/automations\/auto_assign_faces$/);
}

// Pick the folder the picker opens on (the sandbox's first media dir). Used by the
// dry-run Test control, which scopes the run to a picked file/folder.
async function pickCurrentFolder(page: Page): Promise<void> {
  const picker = page.locator('#folder-picker-modal');
  await expect(picker).toHaveClass(/active/);
  await expect(page.locator('#folder-picker-path')).not.toHaveText('');
  await page.locator('#folder-picker-select').click();
  await expect(picker).not.toHaveClass(/active/);
}

test.describe('Automations', () => {
  test.afterAll(async ({ browser }) => {
    if (!customSlug) return;
    const baseURL = process.env.BASE_URL || 'http://127.0.0.1:5001';
    const context = await browser.newContext({ baseURL });
    await context.request.post(`/utilities/automations/${customSlug}/delete`).catch(() => {});
    await context.close();
  });

  test('automations_list_shows_system_and_custom', async ({ page }) => {
    const slug = await ensureCustomAutomation(page);

    // Sidebar groups system and custom automations under their own headings.
    const nav = automationsSidebar(page);
    const systemList = nav.locator('h3:has-text("System") + ul.panel-nav');
    await expect(systemList.locator('a').filter({ hasText: 'File sync' })).toBeVisible();
    await expect(systemList.locator('a').filter({ hasText: 'Auto-assign faces' })).toBeVisible();
    const customList = nav.locator('h3:has-text("Custom") + ul.panel-nav');
    await expect(customList.locator('a').filter({ hasText: CUSTOM_AUTOMATION_NAME })).toBeVisible();

    // Selecting the custom automation shows its details and the custom-only actions.
    await openAutomation(page, slug);
    await expect(page.locator('.page-header')).toContainText(CUSTOM_AUTOMATION_NAME);
    await expect(page.locator('.page-header')).toContainText(/Edit details|No description yet/);
    await expect(page.getByRole('link', { name: 'Edit', exact: true })).toBeVisible();
    await expect(page.locator('#edit-automation-button')).toBeVisible();
    await expect(page.locator('#delete-automation-button')).toBeVisible();
    await expect(page.getByRole('link', { name: 'Edit triggers' })).toBeVisible();

    // A system automation shows its description but no Edit/Delete controls.
    await openAutomation(page, 'file_sync');
    await expect(page.locator('.page-header')).toContainText('File sync');
    await expect(page.locator('.page-header')).toContainText('Reconcile the photo index');
    await expect(page.getByRole('link', { name: 'Edit', exact: true })).toHaveCount(0);
    await expect(page.locator('#edit-automation-button')).toHaveCount(0);
    await expect(page.locator('#delete-automation-button')).toHaveCount(0);
    await expect(page.getByRole('link', { name: 'Edit triggers' })).toBeVisible();

    // Note: the "No automation selected" empty state only renders when the database
    // holds zero automations; the seeded system automations make it unreachable here.
  });

  test('automations_enable_disable_toggle', async ({ page }) => {
    // geotag_from_neighbors ships disabled and its only trigger is an event, so
    // briefly enabling it cannot start a run.
    await openAutomation(page, 'geotag_from_neighbors');
    const before = (await enableToggleButton(page).textContent())!.trim();
    const after = before === 'Enable' ? 'Disable' : 'Enable';

    // The toggle posts and the server answers with HX-Refresh, reloading the page.
    await toggleEnabledTo(page, after);
    await toggleEnabledTo(page, before);
  });

  test('automations_edit_schedule_trigger', async ({ page }) => {
    const slug = await ensureCustomAutomation(page);
    await page.goto(`/utilities/automations/${slug}/triggers/edit`);
    await expect(page.locator('#automation-triggers')).toBeVisible();

    const area = triggerAddArea(page);
    await page.locator('.js-add-schedule').click();
    await expect(area).toHaveClass(/adding-schedule/);

    // The cron builder renders and describes the default preset in plain language.
    await expect(area.locator('.cron-preview')).toHaveText('Every hour');
    const save = area.locator('.js-save-schedule');
    await expect(save).toBeEnabled();

    // The advanced cron field is server-validated; Save stays disabled while invalid.
    await area.locator('.cron-mode').selectOption('custom');
    await area.locator('.cron-cadence').selectOption('advanced');
    await area.locator('.cron-raw').fill('definitely not cron');
    await expect(save).toBeDisabled({ timeout: 10_000 });
    await expect(area.locator('.schedule-editor-error')).toBeVisible();

    await area.locator('.cron-raw').fill('*/30 * * * *');
    await expect(area.locator('.cron-preview')).toHaveText('Every 30 minutes');
    await expect(save).toBeEnabled({ timeout: 10_000 });
    await expect(area.locator('.schedule-editor-error')).toBeHidden();

    // Saving re-renders the trigger list with the new schedule, described in English.
    await save.click();
    const row = triggerRows(page, 'Schedule');
    await expect(row).toHaveCount(1);
    await expect(row.locator('.automation-trigger-desc')).toHaveText('Every 30 minutes');

    // Cleanup: remove the schedule trigger that was added.
    await row.locator('.btn-danger').click();
    await expect(page.locator('.automation-trigger-row')).toHaveCount(0);
    await expect(page.locator('#automation-triggers .no-data')).toBeVisible();
  });

  test('automations_add_event_trigger', async ({ page }) => {
    const slug = await ensureCustomAutomation(page);
    await page.goto(`/utilities/automations/${slug}/triggers/edit`);
    await expect(page.locator('#automation-triggers')).toBeVisible();

    // Only one trigger panel is open at a time: an open panel replaces the add
    // buttons, and opening the event panel leaves the schedule panel closed.
    const area = triggerAddArea(page);
    await page.locator('.js-add-schedule').click();
    await expect(area).toHaveClass(/adding-schedule/);
    await expect(area.locator('.js-add-event')).toBeHidden();
    await area.locator('.schedule-editor .js-cancel').click();
    await expect(area).not.toHaveClass(/adding-schedule/);

    await page.locator('.js-add-event').click();
    await expect(area).toHaveClass(/adding-event/);
    await expect(area).not.toHaveClass(/adding-schedule/);
    await expect(area.locator('.schedule-editor')).toBeHidden();

    await area.locator('#new-event-type').selectOption('media_imported');
    await area.getByRole('button', { name: 'Add event' }).click();

    const row = triggerRows(page, 'Event');
    await expect(row).toHaveCount(1);
    await expect(row.locator('.automation-trigger-event')).toHaveText('Media imported');

    // Cleanup: remove the event trigger that was added.
    await row.locator('.btn-danger').click();
    await expect(page.locator('.automation-trigger-row')).toHaveCount(0);
  });

  test('automations_run_now', async ({ page }) => {
    // tag-recent-imports (seeded custom automation with published code) has a
    // schedule trigger. Note: file_sync
    // is a poor target here — it records no run Job when the index is already in
    // sync. The run is enqueued (202) and lands in Run history once the taskq
    // worker records its Job (the section self-polls every 5s).
    await openAutomation(page, 'tag-recent-imports');
    // Run history is capped at the 10 most recent jobs, so a count comparison
    // saturates on a long-lived environment; detect the new run as a content
    // change of the history section instead.
    const runList = page.locator('#automation-runs');
    const historyBefore = (await runList.innerText()).trim();
    await page.locator('.js-run-files').click();
    await Promise.all([
      page.waitForResponse(response =>
        response.url().includes('/utilities/automations/tag-recent-imports/run') && response.status() === 202),
      pickCurrentFolder(page),
    ]);
    await expect(page.locator('.notification.visible')).toContainText(/Run started/i);
    await expect.poll(async () => (await runList.innerText()).trim(), { timeout: 60_000 })
      .not.toBe(historyBefore);
    await expect(page.locator('#automation-runs .automation-run-row').first()).toBeVisible();
  });

  test('automations_test_dry_run', async ({ page }) => {
    // The sandbox seeds tag-new-arrivals: a custom automation with published code
    // and a working draft, so the editor renders the code toggle and Test controls.
    await page.goto('/utilities/automations/tag-new-arrivals/edit');
    await expect(page.locator('.js-code-toggle.active')).toHaveAttribute('data-version', 'working');

    // Real dry-run over the photos under the picked folder: intercepted host-API
    // actions are rendered, nothing changes.
    await page.locator('#automation-test-button').click();
    await pickCurrentFolder(page);
    const result = page.locator('#automation-test-result');
    await expect(result).toBeVisible({ timeout: 15_000 });
    await expect(result).not.toHaveClass(/is-error/);
    await expect(result.locator('.automation-test-meta').first()).toContainText('Testing on');
    await expect(result).toContainText(/Ran working code · \d+ photos?/);
    await expect(result).toContainText(/Actions \(\d+\)/);
    await expect(result.locator('tr').filter({ hasText: /Tag \d+ photo/ })).toHaveCount(1);

    // Per-action details are hidden until "Show details" is toggled.
    await expect(result.locator('.test-action-detail').first()).toBeHidden();
    await result.locator('.automation-test-toggle input').check();
    await expect(result).toHaveClass(/show-details/);
    await expect(result.locator('.test-action-detail').first()).toContainText('tag_media_items(');

    // The sandbox has no AI key for a run that returns a value or fails, so
    // simulate one client-side to verify grouped actions, the captured return
    // value, and the error rendering.
    const failedPayload: DryRunPayload = {
      success: false,
      code_source: 'working',
      context: { media_item_ids: [1, 2, 3] },
      actions: [
        { name: 'tag_media_items', summary: 'Tag photo 1 with "beach"', args: [[1], 'beach'] },
        { name: 'tag_media_items', summary: 'Tag photo 2 with "beach"', args: [[2], 'beach'] },
        { name: 'tag_media_items', summary: 'Tag photo 3 with "beach"', args: [[3], 'beach'] },
      ],
      value: { tagged: 3 },
      error: 'RuntimeError: boom',
    };
    await page.route('**/utilities/automations/tag-new-arrivals/test-files', route => route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(failedPayload),
    }));
    await page.locator('#automation-test-button').click();
    await pickCurrentFolder(page);

    await expect(result).toHaveClass(/is-error/);
    await expect(result.locator('.automation-test-error')).toContainText('RuntimeError: boom');

    // A run of the same action collapses into one row with a count and per-item list.
    const groupedRow = result.locator('tr').filter({ hasText: 'Tag media items' });
    await expect(groupedRow.locator('.automation-test-count')).toHaveText('× 3');
    await expect(groupedRow.locator('.automation-test-group li')).toHaveCount(3);

    // The captured return value shows behind "Show details".
    await result.locator('.automation-test-toggle input').check();
    await expect(result.locator('.automation-test-output')).toContainText('"tagged": 3');
  });

  test('automations_configure_system_automation', async ({ page }) => {
    const original = await readAssignFacesThreshold(page);
    const updated = original === '50' ? '55' : '50';

    try {
      // The configure modal opens with the automation's declared config fields.
      await openAutomation(page, 'auto_assign_faces');
      await page.locator('#configure-automation-button').click();
      const modal = page.locator('#configureAutomationModal');
      await expect(modal).toHaveClass(/active/);
      await expect(modal).toContainText('Match threshold');
      await expect(modal.locator('#config-threshold')).toBeVisible();
      await expect(modal.locator('#config-assign_multiple_matches')).toBeAttached();
      await modal.locator('.modal-actions [name="cancel"]').click();
      await expect(modal).not.toHaveClass(/active/);

      // Saving persists the new value; reopening the modal reflects it.
      await saveAssignFacesThreshold(page, updated);
      expect(await readAssignFacesThreshold(page)).toBe(updated);
    } finally {
      await saveAssignFacesThreshold(page, original);
    }
  });

  test('automations_create_custom_automation', async ({ page }) => {
    // The sandbox has no AI API key, so the chat and status endpoints are
    // intercepted client-side to simulate the assistant's generation run.
    const slug = await ensureCustomAutomation(page);
    const userPrompt = 'Tag every beach photo with "beach"';
    const generatedCode = 'def run(host, context):\n    host.tag_media_items(context.media_item_ids, "beach")\n';
    let statusCalls = 0;

    await page.route(`**/utilities/automations/${slug}/chat`, route => route.fulfill({
      status: 202,
      contentType: 'application/json',
      body: JSON.stringify({ slug }),
    }));
    await page.route(`**/utilities/automations/${slug}/status`, route => {
      statusCalls += 1;
      const running = statusCalls < 3;
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          slug,
          status: running ? 'IN_PROGRESS' : 'READY',
          started_at: new Date().toISOString(),
          working_code: running ? null : generatedCode,
          published_code: null,
          messages: [
            { type: 'user', content: userPrompt },
            { type: 'assistant', content: 'I wrote an automation that tags beach photos with "beach".' },
          ],
        }),
      });
    });

    await page.goto(`/utilities/automations/${slug}/edit`);
    await expect(page.locator('#automation-chat')).toBeVisible();

    // The new automation appears in the sidebar as a custom automation.
    const customList = automationsSidebar(page).locator('h3:has-text("Custom") + ul.panel-nav');
    await expect(customList.locator('a').filter({ hasText: CUSTOM_AUTOMATION_NAME })).toBeVisible();

    const reloaded = page.waitForEvent('load', { timeout: 20_000 });
    await page.locator('#automation-chat-message').fill(userPrompt);
    await page.locator('#automation-chat-form button[type="submit"]').click();

    // While generating: busy status bar, locked input, and the polled transcript
    // showing the conversation.
    await expect(page.locator('#automation-chat-status')).toBeVisible();
    await expect(page.locator('#automation-chat-message')).toBeDisabled();
    await expect(page.locator('#automation-chat-messages .chat-message-user')).toContainText('beach photo');
    await expect(page.locator('#automation-chat-messages .chat-message-assistant'))
      .toContainText('tags beach photos');

    // A finished generation reloads the editor to show the published code/draft
    // (here the server state is unchanged, so the editor simply re-renders).
    await reloaded;
    await expect(page.locator('.automation-edit-screen')).toBeVisible();
    await expect(page.locator('.automation-code-section')).toBeVisible();
  });

  test('automations_delete_custom_automation', async ({ page }) => {
    const slug = await ensureCustomAutomation(page);

    // System automations offer no delete action.
    await openAutomation(page, 'file_sync');
    await expect(page.locator('#delete-automation-button')).toHaveCount(0);

    // Deleting a custom automation asks for confirmation first.
    await openAutomation(page, slug);
    await page.locator('#delete-automation-button').click();
    const dialog = page.locator('#global-confirm-dialog');
    await expect(dialog).toHaveClass(/active/);
    await expect(dialog).toContainText(CUSTOM_AUTOMATION_NAME);

    await Promise.all([
      page.waitForURL(/\/utilities\/automations\//),
      page.locator('#confirm-dialog-confirm').click(),
    ]);

    // After confirming, the automation is gone from the sidebar.
    const nav = automationsSidebar(page);
    await expect(nav.locator('a').filter({ hasText: CUSTOM_AUTOMATION_NAME })).toHaveCount(0);
    customSlug = null;
  });
});
