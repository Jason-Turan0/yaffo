import { test, expect, Page } from '@playwright/test';
import {
  CONTRACT_WIDTHS,
  VIEWPORTS,
  expectNoPageOverflow,
  expectRouteFits,
  touchDrag,
  withTouchContext,
} from '../_support/responsive';

const UNIQ = Date.now();
const PAGE_TITLE = `SpecTestPage-${UNIQ}`;

// The canvas policy in static/pages/grid.js, restated here so a change to either
// has to be a deliberate change to both.
const MAX_COLUMNS = 12;
const ROW_HEIGHT = 80;
const SINGLE_COLUMN_MIN_ROWS = 3;

// The suite creates and deletes its own pages; serial keeps the created page ids
// flowing between scenarios and avoids racing the shared nav list.
test.describe.configure({ mode: 'serial' });

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

  // --------------------------------------------------------------------------
  // Responsive coverage (P8 — custom pages and widgets). The shell contract
  // itself is exercised on Home (specs/photo_gallery.yaml); everything below is
  // this family's own behaviour. Shared assertions come from
  // _support/responsive.ts.
  //
  // The canvas policy under test (static/pages/grid.js, CANVAS_BANDS) is measured
  // on the .grid-stack element, never the window:
  //   canvas >= 900px  12 columns, >= 1 row
  //   canvas >= 600px   6 columns, >= 2 rows
  //   canvas <  600px   1 column,  >= 3 rows
  // --------------------------------------------------------------------------

  // A two-widget page in the authored 12-column layout: a short wide widget (the
  // one a single-column reflow squeezes) beside a tall one.
  const LAYOUT_WIDGETS = [
    {
      id: `p8a${UNIQ}`, x: 0, y: 0, w: 6, h: 2, title: 'Trip stats',
      html: '<div class="p8-stats"><b>42</b><span>photos</span><span>7 places</span></div>',
      css: '.p8-stats { display: flex; flex-wrap: wrap; gap: 8px; padding: 12px; }',
      js: '', data_query: {}, state: {},
    },
    {
      id: `p8b${UNIQ}`, x: 6, y: 0, w: 6, h: 5, title: 'Trip gallery',
      html: '<div class="p8-gallery">gallery</div>',
      css: '.p8-gallery { padding: 12px; }',
      js: '', data_query: {}, state: {},
    },
  ];

  // Create a page and commit a widget set straight through the Save endpoint the
  // design view posts to, so each responsive case starts from a known layout.
  async function seedLayoutPage(
    page: Page,
    widgets: Record<string, unknown>[] = LAYOUT_WIDGETS,
  ): Promise<number> {
    const pageId = await createPage(page, `${PAGE_TITLE}-layout`);
    // The JSON endpoints take the token as a header (static/security.js wraps the
    // app's own fetch the same way); a form field only covers the form posts.
    const csrf_token = await csrfToken(page);
    const response = await page.request.post(`/pages/${pageId}/update`, {
      headers: { 'X-CSRF-Token': csrf_token },
      data: {
        title: `${PAGE_TITLE}-layout`, subtitle: '', show_title: false,
        tab_order: 1, widgets,
      },
    });
    expect(response.status()).toBe(204);
    return pageId;
  }

  /**
   * The grid's live geometry, keyed by widget id: [x, y, w, h]. GridStack omits
   * gs-x and gs-w when they are at their defaults (0 and 1), which is exactly the
   * single-column case, so a missing attribute is read as that default rather
   * than as zero.
   */
  async function geometry(page: Page): Promise<Record<string, number[]>> {
    await expect(page.locator('.grid-stack.gs-1, .grid-stack.gs-6, .grid-stack.gs-12')).toBeVisible();
    return page.locator('.grid-stack > .grid-stack-item').evaluateAll((items) =>
      Object.fromEntries(items.map((item) => [
        item.getAttribute('gs-id') || '',
        ([['gs-x', 0], ['gs-y', 0], ['gs-w', 1], ['gs-h', 1]] as [string, number][])
          .map(([name, fallback]) => {
            const value = item.getAttribute(name);
            return value === null ? fallback : Number(value);
          }),
      ])));
  }

  /**
   * The canvas carries `grid-stack-animate`, so a band change transitions every
   * item's box: the column class and the gs-* attributes land immediately, while
   * the geometry is still travelling. Measuring at that moment reads a number
   * somewhere between the old layout and the new one.
   *
   * Two rAFs first, so the style change has been committed and the transitions
   * exist; then wait on the transitions themselves rather than guessing at a
   * delay or watching for frames that happen to round to the same value.
   */
  async function settleCanvas(page: Page): Promise<void> {
    await page.evaluate(() => new Promise<void>((resolve) => {
      requestAnimationFrame(() => requestAnimationFrame(() => {
        const running = Array.from(document.querySelectorAll('.grid-stack > .grid-stack-item'))
          .flatMap((item) => item.getAnimations())
          .map((animation) => animation.finished.catch(() => undefined));
        Promise.all(running).then(() => resolve());
      }));
    }));
  }

  test('custom_page_routes_fit_every_contract_width - The design and presentation views contain themselves from 320px up', async ({ page }) => {
    const pageId = await seedLayoutPage(page);

    for (const width of CONTRACT_WIDTHS) {
      await page.setViewportSize({ width, height: width === 320 ? 568 : 900 });
      await expectRouteFits(page, `/pages/${pageId}`);
      await expectRouteFits(page, `/pages/${pageId}/design`);
    }

    // Short landscape phone: the design view stacks, and the canvas is still contained.
    await page.setViewportSize(VIEWPORTS.narrowLandscape);
    await expectRouteFits(page, `/pages/${pageId}/design`);

    // Custom pages register no page panel of their own — Menu is the only narrow
    // navbar toggle here, so there is no peer-panel contract to satisfy beyond
    // the shell's own (verified on Home).
    await page.setViewportSize(VIEWPORTS.narrow);
    await page.goto(`/pages/${pageId}/design`);
    await expect(page.locator('#nav-menu-toggle')).toBeVisible();
    await expect(page.locator('[data-nav-panel-toggle]:not(#nav-menu-toggle)')).toHaveCount(0);

    await deletePageViaApi(page, pageId);
  });

  test('custom_page_canvas_column_bands_follow_the_canvas_not_the_window - The column count comes from the canvas element, not the viewport', async ({ page }) => {
    const pageId = await seedLayoutPage(page);
    const grid = page.locator('.grid-stack');

    // Desktop: the authored 12 columns, canvas comfortably over 900px.
    await page.setViewportSize(VIEWPORTS.desktop);
    await page.goto(`/pages/${pageId}/design`);
    await expect(grid).toHaveClass(new RegExp(`gs-${MAX_COLUMNS}\\b`));
    await expect(grid).not.toHaveClass(/is-single-column/);

    // The window is not the canvas. At 1280 the editor panel still sits beside the
    // grid (the shared layer stacks it at 1200), so the design canvas is in the
    // 6-column band while the presentation view — same window, no editor panel —
    // is still on the authored 12. A viewport media query could not tell these
    // two apart.
    await page.setViewportSize({ width: 1280, height: 900 });
    await expect(grid).toHaveClass(/gs-6\b/);
    const designCanvas = await grid.evaluate((el) => el.clientWidth);
    expect(designCanvas).toBeGreaterThanOrEqual(600);
    expect(designCanvas).toBeLessThan(900);

    await page.goto(`/pages/${pageId}`);
    await expect(grid).toHaveClass(new RegExp(`gs-${MAX_COLUMNS}\\b`));
    expect(await grid.evaluate((el) => el.clientWidth)).toBeGreaterThanOrEqual(900);

    // Tablet portrait: both views are on the same full-width canvas, so both are
    // in the intermediate band.
    await page.setViewportSize(VIEWPORTS.tabletPortrait);
    await expect(grid).toHaveClass(/gs-6\b/);
    await page.goto(`/pages/${pageId}/design`);
    await expect(grid).toHaveClass(/gs-6\b/);

    // Phone: one full-bleed column, and the direct-controls mode that goes with it.
    await page.setViewportSize(VIEWPORTS.narrow);
    await expect(grid).toHaveClass(/gs-1\b/);
    await expect(grid).toHaveClass(/is-single-column/);
    await expect(grid).toHaveClass(/is-direct-controls/);
    for (const [, box] of Object.entries(await geometry(page))) {
      expect(box[2]).toBe(1);  // every widget spans the single column
    }
    await expectNoPageOverflow(page);

    // Presentation obeys the same policy on the full-width canvas.
    await page.goto(`/pages/${pageId}`);
    await expect(grid).toHaveClass(/gs-1\b/);

    await deletePageViaApi(page, pageId);
  });

  test('custom_page_intermediate_band_widgets_get_a_real_width - Six-column widgets are laid out, not collapsed to zero width', async ({ page }) => {
    const pageId = await seedLayoutPage(page);

    // Regression: the vendored gridstack.min.css only carries the width/offset
    // rules for `.gs-12` and `.gs-1` (upstream's gridstack-extra.css, which holds
    // 2–11, is not vendored). A `.gs-6` item therefore matched no width rule at
    // all: every widget collapsed to 0px while its header buttons still painted
    // over the canvas, so the intermediate band rendered an invisible, unclickable
    // grid. pages/detail.css now supplies the six-column rules.
    await page.setViewportSize(VIEWPORTS.tabletPortrait);
    await page.goto(`/pages/${pageId}`);
    const grid = page.locator('.grid-stack');
    await expect(grid).toHaveClass(/gs-6\b/);
    await settleCanvas(page);

    const canvasWidth = await grid.evaluate((el) => el.clientWidth);
    const widths = await page.locator('.grid-stack > .grid-stack-item').evaluateAll(
      (items) => items.map((item) => item.getBoundingClientRect().width));
    expect(widths).toHaveLength(LAYOUT_WIDGETS.length);
    for (const width of widths) {
      // Each authored 6-of-12 widget is half of the six-column canvas.
      expect(width).toBeGreaterThan(0);
      expect(Math.abs(width - canvasWidth / 2)).toBeLessThanOrEqual(2);
    }
    await expectNoPageOverflow(page);

    await deletePageViaApi(page, pageId);
  });

  test('custom_page_narrow_widgets_keep_a_readable_minimum_height - A short wide widget gains vertical room in one column', async ({ page }) => {
    const pageId = await seedLayoutPage(page);
    const shortWidget = page.locator(`.grid-stack-item[gs-id="${LAYOUT_WIDGETS[0].id}"]`);

    await page.setViewportSize(VIEWPORTS.narrow);
    await page.goto(`/pages/${pageId}`);
    await expect(page.locator('.grid-stack')).toHaveClass(/gs-1\b/);

    // Two authored rows would leave the reflowed content scrolling inside its own
    // frame; the one-column floor gives it three.
    await expect(shortWidget).toHaveAttribute('gs-h', String(SINGLE_COLUMN_MIN_ROWS));
    await settleCanvas(page);
    expect((await shortWidget.boundingBox())!.height)
      .toBeGreaterThanOrEqual(SINGLE_COLUMN_MIN_ROWS * ROW_HEIGHT - 20);

    // Scroll ownership: the card contains its content rather than scrolling sideways.
    const cardOverflow = await shortWidget.locator('.grid-stack-item-content').evaluate(
      (el) => el.scrollWidth - el.clientWidth);
    expect(cardOverflow).toBeLessThanOrEqual(1);

    // The floor is a display-time adjustment, not an edit: the authored height is
    // back as soon as the canvas can carry it.
    await page.setViewportSize(VIEWPORTS.desktop);
    await expect(page.locator('.grid-stack')).toHaveClass(new RegExp(`gs-${MAX_COLUMNS}\\b`));
    await expect(shortWidget).toHaveAttribute('gs-h', '2');

    await deletePageViaApi(page, pageId);
  });

  test('custom_page_widgets_move_and_resize_without_dragging - The explicit controls do everything the drag gestures do', async ({ page, browser }) => {
    const pageId = await seedLayoutPage(page);
    const [first, second] = LAYOUT_WIDGETS;

    await withTouchContext(browser, VIEWPORTS.narrow, async (touchPage) => {
      await touchPage.goto(`/pages/${pageId}/design`);
      const grid = touchPage.locator('.grid-stack');
      await expect(grid).toHaveClass(/is-direct-controls/);

      const widget = touchPage.locator(`.grid-stack-item[gs-id="${first.id}"]`);
      const taller = widget.locator('.widget-size-taller');
      const shorter = widget.locator('.widget-size-shorter');
      const down = widget.locator('.widget-order-down');

      // Coarse-pointer targets are real targets.
      for (const control of [taller, shorter, down]) {
        const box = (await control.boundingBox())!;
        expect(box.width).toBeGreaterThanOrEqual(44);
        expect(box.height).toBeGreaterThanOrEqual(44);
      }

      // Taller / shorter step the height by one row, off the one-column floor.
      await expect(widget).toHaveAttribute('gs-h', String(SINGLE_COLUMN_MIN_ROWS));
      await taller.click();
      await expect(widget).toHaveAttribute('gs-h', String(SINGLE_COLUMN_MIN_ROWS + 1));
      await shorter.click();
      await expect(widget).toHaveAttribute('gs-h', String(SINGLE_COLUMN_MIN_ROWS));

      // Regression: one column is a stack, but the canvas floats, so the row the
      // shrink freed used to stay behind as a hole — and two neighbours with a
      // gap between them are two neighbours GridStack's swap() refuses to
      // reorder, so the Move down below silently did nothing at all.
      const next = touchPage.locator(`.grid-stack-item[gs-id="${second.id}"]`);
      await expect(next).toHaveAttribute('gs-y', String(SINGLE_COLUMN_MIN_ROWS));

      // Move-down swaps this widget past the next one in the stack.
      const orderBefore = await touchPage.locator('.grid-stack > .grid-stack-item').evaluateAll(
        (items) => items
          .map((item) => ({
            id: item.getAttribute('gs-id') || '',
            y: Number(item.getAttribute('gs-y')),
          }))
          .sort((a, b) => a.y - b.y)
          .map((item) => item.id));
      expect(orderBefore).toEqual([first.id, second.id]);

      await down.click();
      await expect.poll(async () => touchPage.locator('.grid-stack > .grid-stack-item').evaluateAll(
        (items) => items
          .map((item) => ({
            id: item.getAttribute('gs-id') || '',
            y: Number(item.getAttribute('gs-y')),
          }))
          .sort((a, b) => a.y - b.y)
          .map((item) => item.id))).toEqual([second.id, first.id]);

      await expectNoPageOverflow(touchPage);
    });

    await deletePageViaApi(page, pageId);
  });

  test('custom_page_touch_drag_on_a_widget_scrolls_the_page - A finger drag scrolls the document instead of moving a widget', async ({ page, browser }) => {
    const pageId = await seedLayoutPage(page);

    await withTouchContext(browser, VIEWPORTS.narrow, async (touchPage, context) => {
      await touchPage.goto(`/pages/${pageId}/design`);
      await expect(touchPage.locator('.grid-stack')).toHaveClass(/is-direct-controls/);

      // Scroll the canvas into view first so the drag starts on a widget header.
      const widget = touchPage.locator(`.grid-stack-item[gs-id="${LAYOUT_WIDGETS[0].id}"]`);
      await widget.scrollIntoViewIfNeeded();
      const before = await touchPage.evaluate(() => window.scrollY);
      const beforeY = await widget.getAttribute('gs-y');
      const header = (await widget.locator('.widget-header').boundingBox())!;

      await touchDrag(
        context,
        touchPage,
        { x: header.x + header.width / 2, y: header.y + header.height / 2 },
        { x: header.x + header.width / 2, y: header.y + header.height / 2 - 220 },
        12,
      );

      // The gesture belongs to the page: it scrolled, and the widget stayed put.
      await expect.poll(() => touchPage.evaluate(() => window.scrollY)).toBeGreaterThan(before);
      await expect(widget).toHaveAttribute('gs-y', beforeY!);
      await expect(touchPage.locator('.grid-stack')).not.toHaveClass(/is-interacting/);
    });

    await deletePageViaApi(page, pageId);
  });

  test('custom_page_mouse_can_reach_the_direct_controls - Hovering a widget reveals its move and resize controls', async ({ page }) => {
    const pageId = await seedLayoutPage(page);

    await page.setViewportSize(VIEWPORTS.desktop);
    await page.goto(`/pages/${pageId}/design`);
    const widget = page.locator(`.grid-stack-item[gs-id="${LAYOUT_WIDGETS[0].id}"]`);
    const controls = widget.locator('.widget-order-controls');

    // Regression: the shared layer only reveals these on :focus-within, which a
    // mouse never produces on its own, so they were unreachable on desktop.
    expect(await controls.evaluate((el) => getComputedStyle(el).opacity)).toBe('0');
    await widget.hover();
    await expect.poll(async () => controls.evaluate((el) => getComputedStyle(el).opacity)).toBe('1');

    await widget.locator('.widget-size-taller').click();
    await expect(widget).toHaveAttribute('gs-h', '3');

    await deletePageViaApi(page, pageId);
  });

  test('custom_page_save_from_a_narrow_canvas_keeps_the_desktop_layout - Saving on a phone publishes the authored 12-column layout', async ({ page }) => {
    const pageId = await seedLayoutPage(page);
    const [first, second] = LAYOUT_WIDGETS;

    await page.setViewportSize(VIEWPORTS.narrow);
    await page.goto(`/pages/${pageId}/design`);
    await expect(page.locator('.grid-stack')).toHaveClass(/gs-1\b/);

    // Regression: the grid's live nodes ARE the one-column layout, so a save made
    // straight off the grid flattened every widget to x=0,w=1 and persisted the
    // heights the reflow had inflated.
    await Promise.all([
      page.waitForResponse((response) =>
        response.url().includes(`/pages/${pageId}/update`) && response.status() === 204),
      page.locator('#save-page-button').click(),
    ]);

    await page.setViewportSize(VIEWPORTS.desktop);
    await page.goto(`/pages/${pageId}/design`);
    await expect(page.locator('.grid-stack')).toHaveClass(new RegExp(`gs-${MAX_COLUMNS}\\b`));
    expect(await geometry(page)).toEqual({
      [first.id]: [first.x, first.y, first.w, first.h],
      [second.id]: [second.x, second.y, second.w, second.h],
    });

    await deletePageViaApi(page, pageId);
  });

  test('custom_page_layout_survives_a_resize_through_the_breakpoint - One column and back restores the layout and the unsaved edit', async ({ page }) => {
    const pageId = await seedLayoutPage(page);
    const [first, second] = LAYOUT_WIDGETS;
    const widget = page.locator(`.grid-stack-item[gs-id="${first.id}"]`);

    await page.setViewportSize(VIEWPORTS.desktop);
    await page.goto(`/pages/${pageId}/design`);
    const authored = await geometry(page);

    // An edit in flight that a reload would destroy.
    await widget.locator('.widget-edit').click();
    await widget.locator('.widget-title-input').fill('Renamed in flight');

    await page.setViewportSize(VIEWPORTS.narrow);
    await expect(page.locator('.grid-stack')).toHaveClass(/gs-1\b/);
    await expect(widget).toHaveAttribute('gs-h', String(SINGLE_COLUMN_MIN_ROWS));

    // Regression: the height floor used to leak into the authored layout on the way
    // back. grid.column() emits a change event of its own, and it fires while the
    // nodes still carry the narrow band's inflated heights — so the "this is an
    // edit, record it" handler captured h=3 as authored and the widget never
    // returned to its two rows.
    await page.setViewportSize(VIEWPORTS.desktop);
    await expect(page.locator('.grid-stack')).toHaveClass(new RegExp(`gs-${MAX_COLUMNS}\\b`));
    expect(await geometry(page)).toEqual(authored);
    expect(authored[first.id]).toEqual([first.x, first.y, first.w, first.h]);
    expect(authored[second.id]).toEqual([second.x, second.y, second.w, second.h]);
    await expect(widget.locator('.widget-title-input')).toHaveValue('Renamed in flight');

    await deletePageViaApi(page, pageId);
  });

  test('custom_page_presentation_reflows_in_source_order - The narrow stack follows the page\'s reading order', async ({ page }) => {
    const pageId = await seedLayoutPage(page);

    await page.setViewportSize(VIEWPORTS.narrow);
    await page.goto(`/pages/${pageId}`);
    await expect(page.locator('.grid-stack')).toHaveClass(/gs-1\b/);
    await settleCanvas(page);

    // The server renders widgets in (grid_y, grid_x) reading order; the reflow
    // must not shuffle them relative to that DOM order.
    const stacked = await page.locator('.grid-stack > .grid-stack-item').evaluateAll((items) => ({
      dom: items.map((item) => item.getAttribute('gs-id') || ''),
      byTop: items
        .map((item) => ({
          id: item.getAttribute('gs-id') || '',
          top: item.getBoundingClientRect().top,
          width: item.getBoundingClientRect().width,
        }))
        .sort((a, b) => a.top - b.top),
    }));
    expect(stacked.byTop.map((item) => item.id)).toEqual(stacked.dom);

    // Every widget is the full width of the single column, and none of them share
    // a row any more. The grid's 8px margin insets the item's *content*, not the
    // item, so a full-bleed widget is exactly the canvas wide.
    const canvasWidth = await page.locator('.grid-stack').evaluate((el) => el.clientWidth);
    for (const item of stacked.byTop) {
      expect(Math.abs(item.width - canvasWidth)).toBeLessThanOrEqual(2);
    }
    const contentWidths = await page.locator('.grid-stack > .grid-stack-item .grid-stack-item-content')
      .evaluateAll((contents) => contents.map((content) => content.getBoundingClientRect().width));
    for (const width of contentWidths) {
      expect(Math.abs(width - (canvasWidth - 16))).toBeLessThanOrEqual(2);
    }
    expect(new Set(stacked.byTop.map((item) => Math.round(item.top))).size)
      .toBe(stacked.byTop.length);

    await deletePageViaApi(page, pageId);
  });

  test('custom_page_widgets_receive_their_real_container_size - A widget frame is exactly the box it occupies', async ({ page }) => {
    const pageId = await seedLayoutPage(page);

    for (const viewport of [VIEWPORTS.narrow, VIEWPORTS.desktop]) {
      await page.setViewportSize(viewport);
      await page.goto(`/pages/${pageId}`);
      await expect(page.locator('.widget-frame')).toHaveCount(LAYOUT_WIDGETS.length);
      await settleCanvas(page);

      // The element matches its container...
      const fit = await page.locator('.widget-frame').evaluateAll((frames) => frames.map((frame) => {
        const body = frame.parentElement!;
        return {
          dw: frame.clientWidth - body.clientWidth,
          dh: frame.clientHeight - body.clientHeight,
          width: frame.clientWidth,
        };
      }));
      for (const frame of fit) {
        expect(frame.dw).toBe(0);
        expect(frame.dh).toBe(0);
        expect(frame.width).toBeGreaterThan(0);
      }

      // ...and the widget document's own viewport is that same box, which is what
      // media queries written inside a generated widget measure against.
      for (const widget of LAYOUT_WIDGETS) {
        const inner = await page
          .frameLocator(`iframe[data-widget-id="${widget.id}"]`)
          .locator('body')
          .evaluate(() => window.innerWidth);
        const outer = fit[LAYOUT_WIDGETS.indexOf(widget)].width;
        expect(Math.abs(inner - outer)).toBeLessThanOrEqual(1);
      }
    }

    await deletePageViaApi(page, pageId);
  });

  test('custom_page_long_widget_titles_do_not_widen_the_canvas - An unbreakable title is truncated, not allowed to push the page', async ({ page }) => {
    const longTitle = `Unbroken${'Titel'.repeat(20)}`;
    const pageId = await seedLayoutPage(page, [
      { ...LAYOUT_WIDGETS[0], title: longTitle },
      LAYOUT_WIDGETS[1],
    ]);

    await page.setViewportSize(VIEWPORTS.minimum);
    await page.goto(`/pages/${pageId}/design`);
    await expectNoPageOverflow(page);

    const title = page.locator(`.grid-stack-item[gs-id="${LAYOUT_WIDGETS[0].id}"] .widget-title`);
    await expect(title).toHaveText(longTitle);
    const clipped = await title.evaluate((el) => ({
      overflowed: el.scrollWidth > el.clientWidth,
      within: el.clientWidth <= (el.closest('.widget-card') as HTMLElement).clientWidth,
    }));
    expect(clipped).toEqual({ overflowed: true, within: true });

    // The presentation view hides the header entirely, so it cannot be widened there.
    await expectRouteFits(page, `/pages/${pageId}`);

    await deletePageViaApi(page, pageId);
  });
});
