import { test, expect, Page } from '@playwright/test';
import { injectReadyThemeDraft } from '../_support/theme-draft';
import {
  BASE_URL,
  CONTRACT_WIDTHS,
  VIEWPORTS,
  expectFitsViewport,
  expectNoPageOverflow,
  expectPanelContract,
  expectRouteFits,
  withTouchContext,
} from '../_support/responsive';

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

// Delete a custom theme through the API, including the CSRF token that the
// server requires for unsafe requests.  The sandbox runs with CSRF protection
// enabled; raw `page.request.post` does not go through the browser's fetch
// interceptor (security.js) that normally attaches the token, so we must
// extract it from the page and pass it ourselves.
async function deleteThemeViaApi(page: Page, slug: string): Promise<void> {
  try {
    const csrfToken: string = await page.evaluate(() => (window as any).APP_CONFIG.csrfToken);
    await page.request.post(`/themes/${slug}/delete`, {
      headers: { 'X-CSRF-Token': csrfToken },
    });
  } catch {
    // Best-effort cleanup – ignore failures.
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
    // Navigate first so the page has window.APP_CONFIG.csrfToken available for
    // deleteThemeViaApi (which extracts it via page.evaluate).
    await page.goto('/themes');
    for (const slug of createdSlugs.values()) {
      await deleteThemeViaApi(page, slug);
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

    await deleteThemeViaApi(page, slug);
  });

  test('themes_rename_theme', async ({ page }) => {
    await createTheme(page, RENAME_SOURCE_LABEL);

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

    await deleteThemeViaApi(page, createdSlugs.get(RENAMED_LABEL)!);
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

    await deleteThemeViaApi(page, slug);
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
      // Restore the original default via the API.  The server requires a CSRF
      // token for unsafe requests; extract it from the page (set by base.html)
      // because page.request.post bypasses the browser fetch interceptor.
      const csrfToken: string = await page.evaluate(() => (window as any).APP_CONFIG.csrfToken);
      await page.request.post(`/themes/${originalDefault}/default`, {
        headers: { 'X-CSRF-Token': csrfToken },
      });
      await deleteThemeViaApi(page, slug);
    }
    await page.goto('/people');
    await expect(page.locator('html')).toHaveAttribute('data-theme', originalDefault);
  });

  test('themes_delete_custom_theme', async ({ page }) => {
    await createTheme(page, DELETE_LABEL);

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

  // ---------------------------------------------------------------------------
  // Responsive coverage (P7 — themes). The shared shell contract itself is
  // verified on Home (specs/photo_gallery.yaml); what follows is this page
  // family's own narrow-screen behaviour, including the peer-panel contract for
  // the family as a whole (settings has no sidebar of its own to register).
  // ---------------------------------------------------------------------------
  test.describe('Responsive (P7)', () => {
    const LONG_LABEL = `SpecLongThemeName${'Unbreakable'.repeat(5)}${UNIQ}`;
    const DRAFT_RESPONSIVE_LABEL = `SpecTestDraftResponsive-${UNIQ}`;
    let longSlug: string;
    let draftSlug: string;
    let customSlug: string;
    let systemSlugs: string[] = [];

    test.beforeAll(async ({ browser }) => {
      const context = await browser.newContext({ baseURL: BASE_URL });
      const page = await context.newPage();
      // The group runs on its own fixtures rather than on whatever an earlier test
      // happened to leave behind, so it is runnable with `--grep Responsive`.
      sandboxDbPath = await readSandboxDbPath(page);
      await page.goto('/themes');
      systemSlugs = (await systemThemesNav(page).locator('a').evaluateAll(links =>
        links.map(link => link.getAttribute('href')!.split('/').pop()!)));
      customSlug = (await customThemesNav(page).locator('a').first()
        .getAttribute('href'))!.split('/').pop()!;
      longSlug = await createTheme(page, LONG_LABEL);
      draftSlug = await createTheme(page, DRAFT_RESPONSIVE_LABEL);
      injectThemeDraft(draftSlug, '#333333');
      await context.close();
    });

    test('themes_nav_uses_a_peer_navbar_panel - the theme list collapses into a peer of Menu', async ({ page }) => {
      await expectPanelContract(page, { route: `/themes/${customSlug}`, panelId: 'themes-nav' });

      // Escape belongs to the topmost surface; with no dialog open that is the panel.
      await page.setViewportSize(VIEWPORTS.narrow);
      await page.goto(`/themes/${customSlug}`);
      await page.locator('#themes-nav-toggle').click();
      await expect(page.locator('#themes-nav')).toBeVisible();
      await page.keyboard.press('Escape');
      await expect(page.locator('#themes-nav')).toBeHidden();
    });

    test('themes_pages_fit_every_contract_viewport - index, a system theme and a custom theme all contain themselves', async ({ page }) => {
      for (const route of ['/themes', `/themes/${systemSlugs[0]}`, `/themes/${customSlug}`]) {
        for (const width of CONTRACT_WIDTHS) {
          await page.setViewportSize({ width, height: 800 });
          await expectRouteFits(page, route);
          for (const action of await page.locator('.theme-actions > *').all()) {
            await action.scrollIntoViewIfNeeded();
            const box = (await action.boundingBox())!;
            expect(box.x).toBeGreaterThanOrEqual(-1);
            expect(box.x + box.width).toBeLessThanOrEqual(width + 1);
          }
          if (await page.locator('.theme-conversation').count() > 0) {
            await page.locator('.theme-conversation').scrollIntoViewIfNeeded();
            const card = (await page.locator('.theme-conversation').boundingBox())!;
            expect(card.x + card.width).toBeLessThanOrEqual(width + 1);
          }
        }
      }
    });

    test('theme_screens_stay_contained_in_every_built_in_theme - a skin never changes the narrow layout geometry', async ({ page }) => {
      // routes/themes_page.py renders the page AS the theme being viewed, so this
      // really is each skin's own geometry rather than the active theme's.
      expect(systemSlugs.length).toBeGreaterThan(1);
      for (const slug of systemSlugs) {
        for (const viewport of [VIEWPORTS.minimum, VIEWPORTS.narrow]) {
          await page.setViewportSize(viewport);
          await expectRouteFits(page, `/themes/${slug}`);
          await expect(page.locator('html')).toHaveAttribute('data-theme', slug);
          for (const action of await page.locator('.theme-actions > *').all()) {
            await action.scrollIntoViewIfNeeded();
            const box = (await action.boundingBox())!;
            expect(box.x, `${slug} header action starts left of the viewport`).toBeGreaterThanOrEqual(-1);
            expect(box.x + box.width, `${slug} header action runs past the right edge`)
              .toBeLessThanOrEqual(viewport.width + 1);
          }
        }
      }
    });

    test('themes_long_theme_name_truncates_instead_of_spilling_out_of_the_nav - one unbreakable word cannot overrun the list', async ({ page }) => {
      // Regression: the nav used to put the raw label straight into the <a>,
      // skipping the shared `.panel-nav-label` span that albums, utilities and
      // sharing use, so a long unbreakable name rendered past the edge of the
      // 250px column and out of the mobile panel instead of ellipsising.
      const measure = async () => {
        const link = customThemesNav(page).locator(`a[href$="/themes/${longSlug}"]`);
        await expect(link).toBeVisible();
        return link.evaluate((element) => {
          const label = element.querySelector<HTMLElement>('.panel-nav-label')!;
          // The badge only renders on the *default* theme, and making a spec
          // theme the application default is global state this test has no
          // business changing — so add the same markup the server would emit and
          // measure whether it survives a name long enough to crowd it out.
          if (!element.querySelector('.theme-nav-default')) {
            const marker = document.createElement('span');
            marker.className = 'theme-nav-default';
            marker.textContent = 'default';
            element.append(marker);
          }
          const badge = element.querySelector<HTMLElement>('.theme-nav-default');
          return {
            linkWidth: element.getBoundingClientRect().width,
            labelWidth: label.getBoundingClientRect().width,
            labelScrollWidth: label.scrollWidth,
            textOverflow: getComputedStyle(label).textOverflow,
            badgeOnRow: badge
              ? Math.abs(badge.getBoundingClientRect().top - label.getBoundingClientRect().top) < 8
              : true,
            // The badge must keep its own width: it is the label that gives way.
            badgeClipped: badge
              ? badge.scrollWidth > badge.getBoundingClientRect().width + 1
              : false,
          };
        });
      };

      await page.setViewportSize(VIEWPORTS.desktop);
      await page.goto(`/themes/${longSlug}`);
      const wide = await measure();
      expect(wide.labelWidth).toBeLessThanOrEqual(wide.linkWidth + 1);
      expect(wide.labelScrollWidth, 'the name should be clipped, not laid out full width')
        .toBeGreaterThan(wide.labelWidth);
      expect(wide.textOverflow).toBe('ellipsis');
      expect(wide.badgeOnRow).toBe(true);
      expect(wide.badgeClipped).toBe(false);
      await expectNoPageOverflow(page);

      await page.setViewportSize(VIEWPORTS.narrow);
      await page.goto(`/themes/${longSlug}`);
      await page.locator('#themes-nav-toggle').click();
      await expect(page.locator('#themes-nav')).toBeVisible();
      const narrow = await measure();
      expect(narrow.labelWidth).toBeLessThanOrEqual(narrow.linkWidth + 1);
      expect(narrow.textOverflow).toBe('ellipsis');
      expect(narrow.badgeOnRow).toBe(true);
      expect(narrow.badgeClipped).toBe(false);
      await expectNoPageOverflow(page);
    });

    test('themes_draft_actions_are_reachable_on_a_phone - Save draft and Discard stay usable once the panel appears', async ({ page, browser }) => {
      await page.setViewportSize(VIEWPORTS.desktop);
      await page.goto(`/themes/${draftSlug}`);
      await expect(page.locator('.theme-draft')).toBeVisible();
      expect(await page.locator('.theme-draft-actions').evaluate(element =>
        getComputedStyle(element).flexDirection), 'desktop keeps the two actions on one row').toBe('row');

      for (const viewport of [VIEWPORTS.minimum, VIEWPORTS.narrow, VIEWPORTS.desktop]) {
        await page.setViewportSize(viewport);
        await page.goto(`/themes/${draftSlug}`);
        await expectNoPageOverflow(page);
        const actions = page.locator('.theme-draft-actions > *');
        await expect(actions).toHaveCount(2);
        for (const action of await actions.all()) {
          await action.scrollIntoViewIfNeeded();
          const box = (await action.boundingBox())!;
          expect(box.x).toBeGreaterThanOrEqual(-1);
          expect(box.x + box.width).toBeLessThanOrEqual(viewport.width + 1);
        }
      }

      await page.setViewportSize(VIEWPORTS.minimum);
      await page.goto(`/themes/${draftSlug}`);
      expect(await page.locator('.theme-draft-actions').evaluate(element =>
        getComputedStyle(element).flexDirection), 'the draft actions stack at 320px').toBe('column');

      // The 44px floor is a coarse-pointer guarantee, measured where it applies.
      await withTouchContext(browser, VIEWPORTS.minimum, async (touchPage) => {
        await touchPage.goto(`/themes/${draftSlug}`);
        const undersized = await touchPage.locator('.theme-draft-actions > *').evaluateAll(elements =>
          elements
            .filter(element => element.getBoundingClientRect().height < 44)
            .map(element => `${element.textContent!.trim()} → `
              + `${Math.round(element.getBoundingClientRect().height)}px`));
        expect(undersized, 'draft actions below the 44px touch target').toEqual([]);
      });
    });

    test('themes_generation_chat_fits_and_scrolls_inside_itself - the transcript owns its own overflow', async ({ page, browser }) => {
      const fillTranscript = async (target: Page) => {
        await target.locator('#theme-chat-messages').evaluate((element) => {
          element.replaceChildren();
          for (let index = 0; index < 20; index += 1) {
            const message = document.createElement('div');
            message.className = index % 2 ? 'chat-message chat-message-assistant' : 'chat-message chat-message-user';
            message.textContent = `Transcript line ${index}: a long described look, repeated `
              + 'so the conversation is taller than the card it lives in. '.repeat(2);
            element.append(message);
          }
        });
      };

      await page.setViewportSize(VIEWPORTS.minimum);
      await page.goto(`/themes/${customSlug}`);
      await fillTranscript(page);

      const transcript = await page.locator('#theme-chat-messages').evaluate((element) => ({
        overflowY: getComputedStyle(element).overflowY,
        scrollHeight: element.scrollHeight,
        clientHeight: element.clientHeight,
        withinViewport: element.getBoundingClientRect().height <= window.innerHeight,
      }));
      expect(transcript.overflowY).toBe('auto');
      expect(transcript.scrollHeight, 'a long conversation must scroll inside the card, not grow the page')
        .toBeGreaterThan(transcript.clientHeight);
      expect(transcript.withinViewport).toBe(true);
      await expectNoPageOverflow(page);

      for (const selector of ['#theme-chat-message', '#theme-chat-form .chat-dialog-actions']) {
        await page.locator(selector).scrollIntoViewIfNeeded();
        await expectFitsViewport(page, selector);
      }

      await withTouchContext(browser, VIEWPORTS.minimum, async (touchPage) => {
        await touchPage.goto(`/themes/${customSlug}`);
        await fillTranscript(touchPage);
        const undersized = await touchPage.locator('#theme-chat-form .chat-dialog-actions > *')
          .evaluateAll(elements => elements
            .filter(element => element.getBoundingClientRect().height < 44)
            .map(element => `${element.textContent!.trim()} → `
              + `${Math.round(element.getBoundingClientRect().height)}px`));
        expect(undersized, 'send controls below the 44px touch target').toEqual([]);
      });
    });

    test('themes_unsent_chat_message_survives_a_resize_through_the_breakpoint - a described look is not lost', async ({ page }) => {
      const described = 'A dark forest green theme with warm paper edges — طلب غير محفوظ';
      await page.setViewportSize(VIEWPORTS.narrow);
      await page.goto(`/themes/${customSlug}`);
      await page.locator('#theme-chat-message').fill(described);

      for (const viewport of [VIEWPORTS.desktop, VIEWPORTS.minimum, VIEWPORTS.narrowLandscape, VIEWPORTS.narrow]) {
        await page.setViewportSize(viewport);
        await expect(page.locator('#theme-chat-message')).toHaveValue(described);
        await expectNoPageOverflow(page);
      }
    });
  });
});
