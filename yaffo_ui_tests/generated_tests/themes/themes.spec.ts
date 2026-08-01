import { test, expect, Page } from '@playwright/test';
import { injectReadyThemeDraft } from '../_support/theme-draft';

const UNIQ = Date.now();
const CREATE_LABEL = `SpecTestTheme-${UNIQ}`;
const RENAME_SOURCE_LABEL = `SpecTestRenameMe-${UNIQ}`;
const RENAMED_LABEL = `SpecTestRenamed-${UNIQ}`;
const DRAFT_LABEL = `SpecTestDraft-${UNIQ}`;
const DELETE_LABEL = `SpecTestDelete-${UNIQ}`;

// The suite mutates global theme state (default theme, custom theme records), so
// it runs serially and restores what it changes.
test.describe.configure({ mode: 'serial', timeout: 120_000 });

let sandboxDbPath: string;
const createdSlugs = new Map<string, string>(); // label -> slug

async function readSandboxDbPath(page: Page): Promise<string> {
  await page.goto('/settings');
  return (await page.locator('.system-path-item')
    .filter({ has: page.getByText('Database Path:', { exact: true }) })
    .locator('code').first().textContent())!.trim();
}

function customThemesNav(page: Page) {
  return page.locator('.themes-sidebar h3:has-text("Custom") + ul.panel-nav');
}

function systemThemesNav(page: Page) {
  return page.locator('.themes-sidebar h3:has-text("System") + ul.panel-nav');
}

// Create a custom theme through the sidebar modal and record its slug (taken
// from the redirect URL).
async function createTheme(page: Page, label: string): Promise<string> {
  await page.goto('/themes');
  await page.locator('#new-theme-button').click();
  const modal = page.locator('#newThemeModal');
  await expect(modal).toHaveClass(/active/);
  await modal.locator('#new-theme-label').fill(label);
  await modal.locator('button[type="submit"]').click();
  await expect(page.locator('.page-header')).toContainText(label);
  const slug = new URL(page.url()).pathname.split('/').pop()!;
  createdSlugs.set(label, slug);
  return slug;
}

// Delete a custom theme through the browser UI (so the CSRF token embedded in
// the page is included).  If the theme is already gone the attempt is silently
// ignored.
async function deleteThemeViaBrowser(page: Page, slug: string): Promise<void> {
  try {
    await page.goto(`/themes/${slug}`);
    await page.locator('#delete-theme-button').click();
    const dialog = page.locator('#global-confirm-dialog');
    await expect(dialog).toHaveClass(/active/);
    await Promise.all([
      page.waitForURL(/\/themes\//),
      page.locator('#confirm-dialog-confirm').click(),
    ]);
  } catch {
    // Theme may not exist or already be deleted — that's fine.
  }
}

// Inject a READY working draft into a custom theme's ApplicationSettings row —
// exactly the state a finished generation leaves. The sandbox has no AI key,
// so this is the only way to reach the publish/discard UI. The privileged DB
// write lives in the reviewed _support helper (generated tests may not run
// subprocesses themselves).
function injectThemeDraft(slug: string, marker: string): void {
  injectReadyThemeDraft(sandboxDbPath, slug, marker);
}

async function publishedTokensCss(page: Page, slug: string): Promise<string> {
  const response = await page.request.get(`/themes/${slug}/preview.css`).catch(() => null);
  if (response && response.ok()) return response.text();
  // Fallback: the page links the preview CSS; fetch whatever URL it advertises.
  await page.goto(`/themes/${slug}`);
  const href = await page.locator('link[rel="stylesheet"][href*="preview"], link[rel="stylesheet"][href*="theme"]').first().getAttribute('href');
  return (await page.request.get(href!)).text();
}

test.describe('Themes', () => {
  test.afterAll(async ({ browser }) => {
    const baseURL = process.env.BASE_URL || 'http://127.0.0.1:5001';
    const context = await browser.newContext({ baseURL });
    const page = await context.newPage();
    for (const slug of Array.from(createdSlugs.values())) {
      await deleteThemeViaBrowser(page, slug);
    }
    await context.close();
  });

  test('themes_list_shows_system_and_custom', async ({ page }) => {
    sandboxDbPath = await readSandboxDbPath(page);

    await page.goto('/themes');
    await expect(page.locator('.themes-sidebar h2')).toHaveText('Themes');

    // Built-in themes are grouped under System; the seeded custom theme under Custom.
    await expect(systemThemesNav(page).locator('a').first()).toBeVisible();
    expect(await systemThemesNav(page).locator('a').count()).toBeGreaterThan(1);
    await expect(customThemesNav(page).locator('a').filter({ hasText: 'Test Ocean' })).toBeVisible();

    // Exactly one theme is marked as the default.
    await expect(page.locator('.themes-sidebar .theme-nav-default')).toHaveCount(1);
  });

  test('themes_create_new_theme', async ({ page }) => {
    const slug = await createTheme(page, CREATE_LABEL);
    await expect(customThemesNav(page).locator('a').filter({ hasText: CREATE_LABEL })).toBeVisible();

    // The sandbox has no AI key: simulate the generation agent client-side. The
    // status flips to READY after two in-progress polls, which reloads the page.
    let statusCalls = 0;
    await page.route(`**/themes/${slug}/chat`, route => route.fulfill({
      status: 202, contentType: 'application/json', body: JSON.stringify({ slug }),
    }));
    await page.route(`**/themes/${slug}/status`, route => {
      statusCalls += 1;
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          slug,
          status: statusCalls < 3 ? 'IN_PROGRESS' : 'READY',
          started_at: new Date().toISOString(),
          messages: [
            { type: 'user', content: 'a dark forest green theme' },
            { type: 'assistant', content: 'I designed a dark forest green look for this theme.' },
          ],
        }),
      });
    });

    await page.goto(`/themes/${slug}`);
    const reloaded = page.waitForEvent('load', { timeout: 20_000 });
    await page.locator('#theme-chat-message').fill('a dark forest green theme');
    await page.locator('#theme-chat-form button[type="submit"]').click();

    // While generating: busy bar, locked input, transcript, and no error toast.
    await expect(page.locator('#theme-chat-status')).toBeVisible();
    await expect(page.locator('#theme-chat-message')).toBeDisabled();
    await expect(page.locator('#theme-chat-messages .chat-message-user')).toContainText('forest green');
    await expect(page.locator('#theme-chat-messages .chat-message-assistant')).toContainText('designed');
    await expect(page.locator('.notification.visible')).toHaveCount(0);

    // A finished generation reloads the page. (With the agent simulated, the
    // server has no real draft afterwards — the draft-preview panel is exercised
    // in themes_publish_or_discard_draft via an injected draft instead.)
    await reloaded;
    await expect(page.locator('.page-header')).toContainText(CREATE_LABEL);

    await deleteThemeViaBrowser(page, slug);
  });

  test('themes_rename_theme', async ({ page }) => {
    const slug = await createTheme(page, RENAME_SOURCE_LABEL);

    await page.locator('#rename-theme-button').click();
    const modal = page.locator('#renameThemeModal');
    await expect(modal).toHaveClass(/active/);
    // Pre-populated with the current name.
    await expect(modal.locator('#rename-theme-label')).toHaveValue(RENAME_SOURCE_LABEL);

    await modal.locator('#rename-theme-label').fill(RENAMED_LABEL);
    await modal.locator('button[type="submit"]').click();

    // The redirect lands on the re-derived slug and the sidebar shows the new name.
    await expect(page.locator('.page-header')).toContainText(RENAMED_LABEL);
    await expect(customThemesNav(page).locator('a').filter({ hasText: RENAMED_LABEL })).toBeVisible();
    await expect(customThemesNav(page).locator('a').filter({ hasText: RENAME_SOURCE_LABEL })).toHaveCount(0);
    createdSlugs.set(RENAMED_LABEL, new URL(page.url()).pathname.split('/').pop()!);

    await deleteThemeViaBrowser(page, createdSlugs.get(RENAMED_LABEL)!);
  });

  test('themes_publish_or_discard_draft', async ({ page }) => {
    const slug = await createTheme(page, DRAFT_LABEL);

    // Publish: inject a READY draft (what a finished generation leaves), then save it.
    injectThemeDraft(slug, '#111111');
    await page.goto(`/themes/${slug}`);
    const draftPanel = page.locator('.theme-draft');
    await expect(draftPanel).toContainText('unpublished design');
    await draftPanel.getByRole('button', { name: 'Save draft' }).click();
    // Publishing answers HX-Refresh; the reloaded page has no draft panel and the
    // published CSS now carries the draft's tokens.
    await expect(page.locator('.theme-draft')).toHaveCount(0, { timeout: 15_000 });
    expect(await publishedTokensCss(page, slug)).toContain('#111111');

    // Discard: a second draft is dropped and the published CSS stays as-is.
    injectThemeDraft(slug, '#222222');
    await page.goto(`/themes/${slug}`);
    await expect(page.locator('.theme-draft')).toBeVisible();
    await page.locator('.theme-draft').getByRole('button', { name: 'Discard' }).click();
    await expect(page.locator('.theme-draft')).toHaveCount(0, { timeout: 15_000 });
    const css = await publishedTokensCss(page, slug);
    expect(css).toContain('#111111');
    expect(css).not.toContain('#222222');

    await deleteThemeViaBrowser(page, slug);
  });

  test('themes_set_default_theme', async ({ page }) => {
    await page.goto('/themes');
    const originalDefault = new URL(page.url()).pathname.split('/').pop()!;

    const slug = await createTheme(page, `SpecTestDefault-${UNIQ}`);
    try {
      await page.getByRole('button', { name: 'Make default' }).click();
      // HX-Refresh reloads; the sidebar default marker moves to this theme.
      await expect(
        customThemesNav(page).locator('li').filter({ hasText: `SpecTestDefault-${UNIQ}` })
          .locator('.theme-nav-default')).toBeVisible({ timeout: 15_000 });

      // The default theme is applied app-wide on the next page load.
      await page.goto('/people');
      await expect(page.locator('html')).toHaveAttribute('data-theme', slug);
    } finally {
      // Restore the original default via the browser UI so the CSRF token embedded
      // in the page is included.  Navigate to the original-default theme page,
      // click its "Make default" button, and wait for the HX-Refresh reload.
      await page.goto(`/themes/${originalDefault}`);
      await page.getByRole('button', { name: 'Make default' }).click();
      await expect(page.locator('.themes-sidebar h2')).toHaveText('Themes', { timeout: 15_000 });
      await deleteThemeViaBrowser(page, slug);
    }
    await page.goto('/people');
    await expect(page.locator('html')).toHaveAttribute('data-theme', originalDefault);
  });

  test('themes_delete_custom_theme', async ({ page }) => {
    const slug = await createTheme(page, DELETE_LABEL);

    await page.locator('#delete-theme-button').click();
    const dialog = page.locator('#global-confirm-dialog');
    await expect(dialog).toHaveClass(/active/);
    await expect(dialog).toContainText(DELETE_LABEL);
    await Promise.all([
      page.waitForURL(/\/themes\//),
      page.locator('#confirm-dialog-confirm').click(),
    ]);
    await expect(customThemesNav(page).locator('a').filter({ hasText: DELETE_LABEL })).toHaveCount(0);
    createdSlugs.delete(DELETE_LABEL);

    // System themes offer no rename/delete actions.
    const systemSlug = (await systemThemesNav(page).locator('a').first().getAttribute('href'))!.split('/').pop()!;
    await page.goto(`/themes/${systemSlug}`);
    await expect(page.locator('#delete-theme-button')).toHaveCount(0);
    await expect(page.locator('#rename-theme-button')).toHaveCount(0);
  });
});
