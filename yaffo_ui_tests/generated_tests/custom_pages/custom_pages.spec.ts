import { test, expect, Page } from '@playwright/test';

const UNIQ = Date.now();
const PAGE_TITLE = `SpecTestPage-${UNIQ}`;

// The suite creates and deletes its own pages; serial keeps the created page ids
// flowing between scenarios and avoids racing the shared nav list.
test.describe.configure({ mode: 'serial', timeout: 120_000 });

const createdPageIds = new Set<number>();

async function csrfToken(page: Page): Promise<string> {
  const response = await page.request.get('/');
  expect(response.ok()).toBeTruthy();
  const token = (await response.text()).match(/name="csrf_token" value="([^"]+)"/)?.[1];
  expect(token).toBeTruthy();
  return token!;
}

// Create a page directly through the create endpoint (the nav's "New page" form
// posts the same thing) and land on its design view.
async function createPage(page: Page, title?: string): Promise<number> {
  const csrf_token = await csrfToken(page);
  const response = await page.request.post('/pages', {
    form: { csrf_token, ...(title ? { title } : {}) },
  });
  expect(response.ok()).toBeTruthy();
  const pageId = Number(new URL(response.url()).pathname.match(/\/pages\/(\d+)/)?.[1]);
  expect(pageId).toBeGreaterThan(0);
  createdPageIds.add(pageId);
  await page.goto(`/pages/${pageId}/design`);
  await expect(page.locator('.page-design')).toBeVisible();
  return pageId;
}

async function deletePageViaApi(page: Page, pageId: number): Promise<void> {
  const csrf_token = await csrfToken(page);
  await page.request.post(`/pages/${pageId}/delete`, { form: { csrf_token } }).catch(() => {});
  createdPageIds.delete(pageId);
}

test.describe('Custom Pages', () => {
  test.afterAll(async ({ browser }) => {
    const baseURL = process.env.BASE_URL || 'http://127.0.0.1:5001';
    const context = await browser.newContext({ baseURL });
    const page = await context.newPage();
    for (const pageId of [...createdPageIds]) {
      await deletePageViaApi(page, pageId);
    }
    await context.close();
  });

  test('pages_create_new_page', async ({ page }) => {
    // Create through the real nav control: the pages bar is expanded by default
    // in a fresh browser context (the "Pages" button is a collapse TOGGLE — do
    // not click it first or the bar hides).
    await page.goto('/');
    await Promise.all([
      page.waitForURL(/\/pages\/\d+\/design$/),
      page.locator('.nav-new-page').click(),
    ]);
    const pageId = Number(page.url().match(/\/pages\/(\d+)\/design$/)![1]);
    createdPageIds.add(pageId);

    // A fresh page opens in the design view with the default title and no widgets.
    await expect(page.locator('.page-design')).toBeVisible();
    await expect(page.locator('#page-title')).toHaveValue('Untitled Page');
    await expect(page.locator('.grid-stack .grid-stack-item')).toHaveCount(0);

    await deletePageViaApi(page, pageId);
  });

  test('pages_edit_metadata_and_save', async ({ page }) => {
    const pageId = await createPage(page);

    await page.locator('#page-title').fill(PAGE_TITLE);
    await page.locator('#page-subtitle').fill('A subtitle for the spec test');
    // tab_order is a position among the nav pages and the server repositions/
    // clamps it (out-of-range values collapse to the last slot), so use a valid
    // position for a deterministic round-trip.
    await page.locator('#page-tab-order').fill('1');
    await page.locator('#page-show-title').setChecked(true);

    // Save posts the metadata and navigates to the page; with no widgets, the
    // detail route bounces back to the design view.
    await Promise.all([
      page.waitForResponse(response =>
        response.url().includes(`/pages/${pageId}/update`) && response.status() === 204),
      page.locator('#save-page-button').click(),
    ]);
    await page.waitForURL(/\/pages\/\d+(\/design)?$/);

    // The saved values round-trip.
    await page.goto(`/pages/${pageId}/design`);
    await expect(page.locator('#page-title')).toHaveValue(PAGE_TITLE);
    await expect(page.locator('#page-subtitle')).toHaveValue('A subtitle for the spec test');
    await expect(page.locator('#page-tab-order')).toHaveValue('1');
    await expect(page.locator('#page-show-title')).toBeChecked();

    await deletePageViaApi(page, pageId);
  });

  test('pages_design_add_widget_manually', async ({ page }) => {
    const pageId = await createPage(page);

    // Add a blank widget: the client renders a preview shell onto the grid.
    await page.locator('#add-widget-button').click();
    const widget = page.locator('.grid-stack .grid-stack-item');
    await expect(widget).toHaveCount(1);
    await expect(widget.locator('.widget-title')).toHaveText('New Widget');

    // Rename inline via the pencil.
    await widget.locator('.widget-edit').click();
    const titleInput = widget.locator('.widget-title-input');
    await expect(titleInput).toBeVisible();
    await titleInput.fill('Spec Widget');
    await titleInput.press('Enter');
    await expect(widget.locator('.widget-title')).toHaveText('Spec Widget');

    // Save publishes the manual draft; the page now has widgets, so the detail
    // route shows the presentation view.
    await page.locator('#save-page-button').click();
    await page.waitForURL(new RegExp(`/pages/${pageId}$`));

    // The widget and its title persist in the design view.
    await page.goto(`/pages/${pageId}/design`);
    await expect(page.locator('.grid-stack .grid-stack-item')).toHaveCount(1);
    await expect(page.locator('.widget-title-input')).toHaveValue('Spec Widget');

    await deletePageViaApi(page, pageId);
  });

  test('pages_generate_widgets_via_ai_chat', async ({ page }) => {
    const pageId = await createPage(page);

    // The sandbox has no AI key: simulate the generation. The chat POST forks a
    // working version; its status is polled until READY, whose widgets the client
    // renders through the REAL preview route (nothing persisted server-side).
    const versionId = 990000 + (UNIQ % 1000);
    const widgetId = `specwidget${UNIQ}`;
    const generatedWidget = {
      id: widgetId,
      title: 'Photo count',
      data_query: {},
      state: {},
      html: '<div class="stat">42 photos</div>',
      css: '.stat { font-weight: bold; }',
      js: '',
      grid_x: 0, grid_y: 0, grid_w: 4, grid_h: 3,
    };
    let statusCalls = 0;
    await page.route(`**/pages/${pageId}/chat`, route => route.fulfill({
      status: 202, contentType: 'application/json', body: JSON.stringify({ version_id: versionId }),
    }));
    await page.route(`**/pages/${pageId}/versions/${versionId}/status`, route => {
      statusCalls += 1;
      const running = statusCalls < 3;
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          version_id: versionId,
          status: running ? 'IN_PROGRESS' : 'READY',
          started_at: new Date().toISOString(),
          completed_at: null,
          error: null,
          messages: [
            { type: 'user', content: 'show a count of my photos' },
            { type: 'assistant', content: 'I added a widget showing your photo count.' },
          ],
          widgets: running ? [] : [generatedWidget],
        }),
      });
    });
    await page.route(`**/pages/${pageId}/versions/${versionId}/publish`, route => route.fulfill({ status: 204, body: '' }));

    await page.locator('#conversation-message').fill('show a count of my photos');
    await page.locator('#conversation-form button[type="submit"]').click();

    // While generating: the grid locks, the status bar ticks, and the feed shows
    // the conversation.
    await expect(page.locator('.page-design')).toHaveClass(/is-generating/);
    await expect(page.locator('#conversation-status')).toBeVisible();
    await expect(page.locator('#add-widget-button')).toBeDisabled();
    await expect(page.locator('#conversation-messages .chat-message-user')).toContainText('count of my photos');
    await expect(page.locator('#conversation-messages .chat-message-assistant')).toContainText('added a widget');

    // When the draft is READY the generated widget renders on the unlocked grid
    // and Save (= publish) becomes available; Cancel would discard the draft.
    const widget = page.locator(`.grid-stack-item[gs-id="${widgetId}"]`);
    await expect(widget).toBeVisible({ timeout: 15_000 });
    await expect(widget.locator('.widget-title')).toHaveText('Photo count');
    await expect(page.locator('.page-design')).not.toHaveClass(/is-generating/);
    await expect(page.locator('#conversation-status')).toBeHidden();
    await expect(page.locator('#save-page-button')).toBeEnabled();
    // Cancel is only enabled while a run is active (or FAILED); on a READY draft
    // the discard path is Cancel-during-run, so here it is disabled.
    await expect(page.locator('#conversation-cancel')).toBeDisabled();

    // Save publishes the READY draft (endpoint simulated) and navigates to the page.
    await page.locator('#save-page-button').click();
    await page.waitForURL(new RegExp(`/pages/${pageId}(/design)?$`));

    await deletePageViaApi(page, pageId);
  });

  test('pages_presentation_view_renders_widgets', async ({ page }) => {
    // The Bennett sandbox has one published showcase page.
    await page.goto('/');
    const pageLinks = page.locator('.nav-page-tab');
    await expect(pageLinks).toHaveCount(1);
    await expect(pageLinks.first()).toHaveText('Florida Trip');
    await pageLinks.first().click();

    // The hero replaces the ordinary page header, and the template gallery fills
    // the remainder of the static presentation grid.
    await expect(page.locator('.page-presentation')).toBeVisible();
    const items = page.locator('.grid-stack .grid-stack-item');
    await expect(items).toHaveCount(2);
    await expect(items.locator('.widget-title')).toHaveText(['Hero banner', 'Photo gallery']);
    await expect(items.nth(0)).toHaveAttribute('gs-x', '0');
    await expect(items.nth(0)).toHaveAttribute('gs-y', '0');
    await expect(items.nth(0)).toHaveAttribute('gs-w', '12');
    await expect(items.nth(1)).toHaveAttribute('gs-x', '0');
    await expect(items.nth(1)).toHaveAttribute('gs-y', '5');
    await expect(items.nth(1)).toHaveAttribute('gs-w', '12');
    await expect(page.locator('.grid-stack')).toHaveClass(/grid-stack-static/);
    await expect(page.locator('.widget-frame').first()).toBeVisible();
    await expect(page.locator('.widget-edit')).toHaveCount(0);
    await expect(page.locator('.widget-delete')).toHaveCount(0);
    await expect(page.locator('.page-presentation .page-header')).toHaveCount(0);

    const heroFrame = page.frameLocator('iframe[title="Hero banner preview"]');
    await expect(heroFrame.locator('.hero-wrap')).toBeVisible();
    await expect(heroFrame.locator('#hero-img')).toHaveAttribute('src', /\/media\/\d+/);
    await expect(heroFrame.locator('#hero-title')).toContainText('Florida');

    const galleryFrame = page.frameLocator('iframe[title="Photo gallery preview"]');
    await expect(galleryFrame.locator('.gallery-grid .gallery-item')).toHaveCount(13);

    const pageId = Number(new URL(page.url()).pathname.match(/\/pages\/(\d+)/)?.[1]);
    await page.goto(`/pages/${pageId}/design`);
    const conversation = page.locator('#conversation-messages');
    await expect(conversation.locator('.chat-message-user')).toHaveCount(2);
    await expect(conversation.locator('.chat-message-assistant')).toHaveCount(2);
    await expect(conversation.locator('.chat-message-user').first()).toContainText('July 2021');
    await expect(conversation.locator('.chat-message-user').last()).toContainText('feel repetitive');
    await expect(conversation.locator('.chat-message-assistant').last())
      .toContainText('scoped both widgets');
  });

  test('pages_delete_page', async ({ page }) => {
    const pageId = await createPage(page, PAGE_TITLE);
    await page.goto(`/pages/${pageId}/design`);

    // Deleting asks for confirmation (naming the page), then redirects home.
    await page.locator('#delete-page-button').click();
    const dialog = page.locator('#global-confirm-dialog');
    await expect(dialog).toHaveClass(/active/);
    await expect(dialog).toContainText(PAGE_TITLE);
    await Promise.all([
      page.waitForURL(/\/$/),
      page.locator('#confirm-dialog-confirm').click(),
    ]);

    // The page is gone from the Pages navigation.
    await expect(page.locator(`nav a[href="/pages/${pageId}"]`)).toHaveCount(0);
    createdPageIds.delete(pageId);
  });
});
