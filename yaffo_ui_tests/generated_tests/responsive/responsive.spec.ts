import { expect, Page, test } from '@playwright/test';

const ROUTES = [
  '/',
  '/albums',
  '/faces',
  '/people',
  '/locations',
  '/utilities/index-photos',
  '/sharing',
  '/themes',
  '/settings',
];
const BASE_URL = process.env.BASE_URL || 'http://127.0.0.1:5001';

async function expectNoPageOverflow(page: Page): Promise<void> {
  const result = await page.evaluate(() => {
    const root = document.documentElement;
    const overflowing = Array.from(document.querySelectorAll<HTMLElement>('body *'))
      .filter(element => {
        const style = getComputedStyle(element);
        if (style.position === 'fixed' || style.display === 'none') return false;
        const rect = element.getBoundingClientRect();
        return rect.right > root.clientWidth + 1 || rect.left < -1;
      })
      .slice(0, 8)
      .map(element => ({
        tag: element.tagName.toLowerCase(),
        id: element.id,
        className: element.className,
        rect: element.getBoundingClientRect().toJSON(),
      }));
    const wideContainers = Array.from(document.querySelectorAll<HTMLElement>('body, body *'))
      .filter(element => element.scrollWidth > element.clientWidth + 1)
      .slice(0, 12)
      .map(element => ({
        tag: element.tagName.toLowerCase(),
        id: element.id,
        className: element.className,
        clientWidth: element.clientWidth,
        scrollWidth: element.scrollWidth,
        overflowX: getComputedStyle(element).overflowX,
      }));
    return {
      clientWidth: root.clientWidth,
      scrollWidth: root.scrollWidth,
      overflowing,
      wideContainers,
    };
  });

  expect(result, JSON.stringify({
    overflowing: result.overflowing,
    wideContainers: result.wideContainers,
  }, null, 2)).toMatchObject({
    scrollWidth: result.clientWidth,
  });
}

async function expectRouteFits(page: Page, route: string): Promise<void> {
  await page.goto(route);
  await expect(page.locator('body')).toBeVisible();
  await expectNoPageOverflow(page);
}

test.describe('Responsive layout', () => {
  for (const width of [320, 390, 768, 1024, 1440]) {
    test(`shell has no page overflow at ${width}px`, async ({ page }) => {
      await page.setViewportSize({ width, height: width < 600 ? 844 : 900 });
      await page.goto('/');
      await expect(page.locator('.navbar')).toBeVisible();
      await expectNoPageOverflow(page);
    });
  }

  test('primary page families fit a narrow viewport', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    for (const route of ROUTES) {
      await page.goto(route);
      await expect(page.locator('body')).toBeVisible();
      await expectNoPageOverflow(page);
    }
  });

  test('mobile navigation exposes every primary destination and closes with Escape', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto('/');

    const toggle = page.locator('#nav-menu-toggle');
    await expect(toggle).toBeVisible();
    await expect(toggle).toHaveAttribute('aria-expanded', 'false');
    await toggle.click();
    await expect(toggle).toHaveAttribute('aria-expanded', 'true');
    await expect(page.locator('.navbar-nav .nav-link')).toHaveCount(9);
    await expect(page.locator('.navbar-nav')).toBeVisible();

    await page.keyboard.press('Escape');
    await expect(toggle).toHaveAttribute('aria-expanded', 'false');
    await expect(toggle).toBeFocused();
  });

  test('mobile navigation and page panels are closed before JavaScript initializes', async ({ browser }) => {
    const context = await browser.newContext({
      baseURL: BASE_URL,
      viewport: { width: 390, height: 844 },
      javaScriptEnabled: false,
    });
    const page = await context.newPage();
    await page.goto('/?page=2');

    await expect(page.locator('#nav-menu-toggle')).toBeVisible();
    await expect(page.locator('#navbar-primary')).toBeHidden();
    await expect(page.locator('#navbar-pages-bar')).toBeHidden();
    await expect(page.locator('#home-filters')).toBeHidden();
    await context.close();
  });

  test('home filters and menu use mutually exclusive navbar panels', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto('/');

    const menuToggle = page.locator('#nav-menu-toggle');
    const filterToggle = page.locator('#nav-filters-toggle');
    await expect(filterToggle).toBeVisible();
    await expect(menuToggle).toBeVisible();
    await expect(filterToggle).toHaveAttribute('data-icon', 'filter');
    await expect(menuToggle).toHaveAttribute('data-icon', 'menu');
    expect(await filterToggle.evaluate((element) => element.parentElement?.className))
      .toBe(await menuToggle.evaluate((element) => element.parentElement?.className));
    const toggleGap = await page.evaluate(() => {
      const filters = document.getElementById('nav-filters-toggle')!.getBoundingClientRect();
      const menu = document.getElementById('nav-menu-toggle')!.getBoundingClientRect();
      return menu.left - filters.right;
    });
    expect(toggleGap).toBeGreaterThanOrEqual(8);
    await expect(page.locator('.responsive-panel-toggle')).toHaveCount(0);
    await expect(page.locator('#home-filters')).toBeHidden();
    const filterClosedBackground = await filterToggle.evaluate((element) =>
      getComputedStyle(element).backgroundColor);
    const menuClosedBackground = await menuToggle.evaluate((element) =>
      getComputedStyle(element).backgroundColor);

    await filterToggle.click();
    await expect(filterToggle).toHaveAttribute('aria-expanded', 'true');
    await expect(menuToggle).toHaveAttribute('aria-expanded', 'false');
    expect(await filterToggle.evaluate((element) =>
      getComputedStyle(element).backgroundColor)).not.toBe(filterClosedBackground);
    await expect(page.locator('#filter-form')).toBeVisible();
    await expect(page.locator('.navbar-nav')).toBeHidden();
    await expect(page.locator('#home-filters .sidebar')).toHaveCSS('padding', '0px');
    await expect(page.locator('#home-filters .sidebar')).toHaveCSS('box-shadow', 'none');
    await page.locator('#filter-form').evaluate((form) => {
      const input = document.createElement('input');
      input.id = 'responsive-panel-state';
      input.value = 'preserved';
      form.append(input);
    });

    await menuToggle.click();
    await expect(menuToggle).toHaveAttribute('aria-expanded', 'true');
    await expect(filterToggle).toHaveAttribute('aria-expanded', 'false');
    expect(await menuToggle.evaluate((element) =>
      getComputedStyle(element).backgroundColor)).not.toBe(menuClosedBackground);
    await expect(filterToggle).toHaveCSS('background-color', filterClosedBackground);
    await expect(page.locator('#filter-form')).toBeHidden();
    await expect(page.locator('.navbar-nav')).toBeVisible();

    await filterToggle.click();
    await expect(filterToggle).toHaveAttribute('aria-expanded', 'true');
    await expect(menuToggle).toHaveAttribute('aria-expanded', 'false');
    await expect(page.locator('#responsive-panel-state')).toHaveValue('preserved');
    await page.keyboard.press('Escape');
    await expect(filterToggle).toHaveAttribute('aria-expanded', 'false');
    await expect(filterToggle).toBeFocused();

    await page.setViewportSize({ width: 1440, height: 900 });
    await expect(filterToggle).toBeHidden();
    await expect(menuToggle).toBeHidden();
    await expect(page.locator('#filter-form')).toBeVisible();
    await expect(page.locator('.main-container-layout > #home-filters')).toBeVisible();
    await expectNoPageOverflow(page);
  });

  test('home view toggle is vertically centered in its header', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto('/?view=grid&page-size=10');

    const centers = await page.evaluate(() => {
      const main = document.querySelector('.photo-gallery .page-header-main')!.getBoundingClientRect();
      const actions = document.querySelector('.photo-gallery .page-header-actions')!.getBoundingClientRect();
      const links = Array.from(document.querySelectorAll<HTMLElement>('.view-toggle a'));
      return {
        main: main.top + main.height / 2,
        actions: actions.top + actions.height / 2,
        linkTops: links.map(link => link.getBoundingClientRect().top),
        linkHeights: links.map(link => link.getBoundingClientRect().height),
      };
    });
    expect(Math.abs(centers.main - centers.actions)).toBeLessThanOrEqual(1);
    expect(new Set(centers.linkTops).size).toBe(1);
    expect(new Set(centers.linkHeights).size).toBe(1);
  });

  test('configure filters supports touch drag reordering', async ({ browser }) => {
    const context = await browser.newContext({
      baseURL: BASE_URL,
      viewport: { width: 390, height: 844 },
      hasTouch: true,
      isMobile: true,
    });
    const page = await context.newPage();
    await page.goto('/?view=grid&page-size=10');
    await page.locator('#nav-filters-toggle').click();
    await page.locator('#configure-filters-btn').click();

    const modal = page.locator('#configureFiltersModal');
    const list = modal.locator('#filter-config-list');
    const rows = list.locator('.filter-config-row');
    await expect(modal).toHaveClass(/active/);
    expect(await rows.count()).toBeGreaterThan(1);

    const originalOrder = await rows.evaluateAll((elements) =>
      elements.map(element => (element as HTMLElement).dataset.key));
    const handle = rows.first().locator('.filter-config-handle');
    const firstBox = await handle.boundingBox();
    const secondBox = await rows.nth(1).boundingBox();
    expect(firstBox).not.toBeNull();
    expect(secondBox).not.toBeNull();
    expect(firstBox!.width).toBeGreaterThanOrEqual(44);
    expect(firstBox!.height).toBeGreaterThanOrEqual(44);

    const cdp = await context.newCDPSession(page);
    const x = firstBox!.x + firstBox!.width / 2;
    const startY = firstBox!.y + firstBox!.height / 2;
    const endY = secondBox!.y + secondBox!.height;
    await cdp.send('Input.dispatchTouchEvent', {
      type: 'touchStart',
      touchPoints: [{ x, y: startY, radiusX: 2, radiusY: 2, force: 1, id: 1 }],
    });
    for (let step = 1; step <= 8; step += 1) {
      await cdp.send('Input.dispatchTouchEvent', {
        type: 'touchMove',
        touchPoints: [{
          x,
          y: startY + ((endY - startY) * step / 8),
          radiusX: 2,
          radiusY: 2,
          force: 1,
          id: 1,
        }],
      });
    }
    await cdp.send('Input.dispatchTouchEvent', {
      type: 'touchEnd',
      touchPoints: [],
    });

    await expect.poll(() => rows.evaluateAll((elements) =>
      elements.map(element => (element as HTMLElement).dataset.key))).toEqual([
      originalOrder[1],
      originalOrder[0],
      ...originalOrder.slice(2),
    ]);
    await expect(list.locator('.filter-config-row.dragging')).toHaveCount(0);
    await modal.getByRole('button', { name: 'Cancel' }).click();
    await context.close();
  });

  test('home pagination uses one icon row on mobile and text on desktop', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto('/?view=grid&page-size=10');

    const buttons = page.locator('.page-navigation .page-btn');
    await expect(buttons).toHaveCount(4);
    await expect(buttons.nth(0)).toHaveAttribute('aria-label', 'First');
    await expect(buttons.nth(1)).toHaveAttribute('aria-label', 'Previous');
    await expect(buttons.nth(2)).toHaveAttribute('aria-label', 'Next');
    await expect(buttons.nth(3)).toHaveAttribute('aria-label', 'Last');
    await expect(page.locator('.page-btn-label').first()).toBeHidden();

    const mobileLayout = await buttons.evaluateAll((elements) => elements.map((element) => {
      const rect = element.getBoundingClientRect();
      const icon = getComputedStyle(element, '::before');
      return {
        top: rect.top,
        width: rect.width,
        iconDisplay: icon.display,
        maskImage: icon.maskImage,
        backgroundImage: icon.backgroundImage,
      };
    }));
    expect(Math.max(...mobileLayout.map(button => button.top))
      - Math.min(...mobileLayout.map(button => button.top))).toBeLessThanOrEqual(1);
    for (const button of mobileLayout) {
      expect(button.width).toBe(44);
      expect(button.iconDisplay).not.toBe('none');
      expect(button.maskImage !== 'none' || button.backgroundImage !== 'none').toBe(true);
    }
    await expectNoPageOverflow(page);

    await buttons.nth(2).click();
    await expect(page).toHaveURL(/(?:\?|&)page=2(?:&|$)/);
    await expect(page.locator('#navbar-primary')).toBeHidden();

    await page.setViewportSize({ width: 1440, height: 900 });
    await expect(page.locator('.page-btn-label').first()).toBeVisible();
    expect(await buttons.first().evaluate(element =>
      getComputedStyle(element, '::before').display)).toBe('none');
  });

  test('people rows become labeled cards on narrow screens', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto('/people');
    const firstRow = page.locator('.people-table tbody tr').first();
    if (await firstRow.count()) {
      await expect(firstRow.locator('td').first()).toHaveAttribute('data-label', 'Name');
      await expect(firstRow).toBeVisible();
    }
    await expectNoPageOverflow(page);
  });

  test('coarse pointers can open a face source preview without changing selection', async ({ browser }) => {
    const context = await browser.newContext({
      baseURL: BASE_URL,
      viewport: { width: 390, height: 844 },
      hasTouch: true,
      isMobile: true,
    });
    const page = await context.newPage();
    await page.goto('/faces?group_by=similarity&threshold=2');

    const face = page.locator('.face').first();
    const preview = face.locator('.face-preview-button');
    await expect(preview).toBeVisible();
    const selectedBefore = await face.evaluate(element => element.classList.contains('selected'));
    await preview.click();
    await expect(page.locator('.face-tooltip')).toHaveClass(/visible/);
    expect(await face.evaluate(element => element.classList.contains('selected'))).toBe(selectedBefore);
    await expectNoPageOverflow(page);
    await preview.click();
    await expect(page.locator('.face-tooltip')).not.toHaveClass(/visible/);
    await context.close();
  });

  test('media, album, person, automation, and custom-page detail routes fit narrow screens', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });

    await page.goto('/');
    const mediaOnclick = await page.locator('.photo-card').first().getAttribute('onclick');
    const mediaRoute = mediaOnclick?.match(/window\.open\('([^']+)'/)?.[1];
    expect(mediaRoute).toBeTruthy();
    await expectRouteFits(page, mediaRoute!);
    const mediaTop = await page.locator('.photo-container').evaluate(element => element.getBoundingClientRect().top);
    const detailsTop = await page.locator('.photo-sidebar').evaluate(element => element.getBoundingClientRect().top);
    expect(mediaTop).toBeLessThan(detailsTop);

    await page.goto('/albums');
    const albumRoute = await page.locator('.album-tile').first().getAttribute('href');
    expect(albumRoute).toBeTruthy();
    await expectRouteFits(page, albumRoute!);
    await expectRouteFits(page, `${albumRoute}?edit=1`);
    const firstAlbumCard = page.locator('#album-grid .album-photo-card').first();
    if (await firstAlbumCard.count()) {
      await firstAlbumCard.focus();
      await expect(firstAlbumCard.locator('.album-reorder-controls')).toBeVisible();
    }

    await page.goto('/people');
    const personRoute = await page.locator('.person-name').first().getAttribute('href');
    expect(personRoute).toBeTruthy();
    await expectRouteFits(page, personRoute!);

    await expectRouteFits(page, '/utilities/automations/file-favorite-kid-photos/edit');
    await expectRouteFits(page, '/utilities/automations/file-favorite-kid-photos/triggers/edit');

    await expectRouteFits(page, '/pages/1');
    await expectRouteFits(page, '/pages/1/design');
    await expect.poll(() => page.evaluate(() =>
      (window as any).PHOTO_ORGANIZER.pages.grid?.grid?.getColumn?.())).toBe(1);
    await expect(page.locator('.grid-stack')).toHaveClass(/is-direct-controls/);
    await expectNoPageOverflow(page);
  });

  test('location map and selection panel follow narrow container resizes', async ({ page }) => {
    await page.setViewportSize({ width: 768, height: 1024 });
    await page.goto('/locations');
    await expect.poll(() => page.locator('#map').evaluate(element => {
      const rect = element.getBoundingClientRect();
      return rect.width > 0 && rect.height > 0;
    })).toBe(true);

    await page.setViewportSize({ width: 390, height: 844 });
    await expect.poll(() => page.evaluate(() => {
      const api = (window as any).PHOTO_ORGANIZER.locations.map;
      return api.map.getSize()[0] === document.getElementById('map')!.clientWidth;
    })).toBe(true);

    await page.evaluate(async () => {
      const api = (window as any).PHOTO_ORGANIZER.locations.map;
      const feature = api.vectorSource.getFeatures()[0];
      api.selectedPhotoIds.add(feature.get('id'));
      await api.updateSelectionPanel();
    });
    await expect(page.locator('#selection-panel')).toHaveClass(/active/);
    await expect(page.locator('#selection-panel')).toBeVisible();
    await expectNoPageOverflow(page);
  });

  test('shell and core layout remain contained in RTL flow', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto('/');
    await page.locator('html').evaluate(element => element.setAttribute('dir', 'rtl'));
    await expectNoPageOverflow(page);
    await page.locator('#nav-menu-toggle').click();
    await expect(page.locator('.navbar-nav')).toBeVisible();
    await expectNoPageOverflow(page);
  });

  test('built-in themes keep the narrow shell contained', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    for (const theme of ['classic', 'darkroom', 'memphis', 'neobrutalist', 'photos-app', 'scrapbook']) {
      await expectRouteFits(page, `/themes/${theme}`);
      await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
    }
  });

  test('short landscape and doubled root text remain usable', async ({ page }) => {
    await page.setViewportSize({ width: 844, height: 390 });
    await page.goto('/');
    await expectNoPageOverflow(page);
    await page.locator('html').evaluate(element => {
      (element as HTMLElement).style.fontSize = '32px';
    });
    await expectNoPageOverflow(page);
    await expect(page.locator('#nav-menu-toggle')).toBeVisible();
    await page.locator('#nav-menu-toggle').click();
    await expect(page.locator('.navbar-nav')).toBeVisible();
  });

  test('album and widget order controls work without dragging', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto('/albums');
    const albumRoute = await page.locator('.album-tile').first().getAttribute('href');
    expect(albumRoute).toBeTruthy();
    await page.goto(`${albumRoute}?edit=1`);

    const albumCards = page.locator('#album-grid [data-select-id]');
    if (await albumCards.count() > 1) {
      const originalIds = await albumCards.evaluateAll(cards =>
        cards.map(card => (card as HTMLElement).dataset.selectId));
      const firstCard = page.locator(`[data-select-id="${originalIds[0]}"]`);
      await firstCard.focus();
      const [moveResponse] = await Promise.all([
        page.waitForResponse(response => response.url().includes('/reorder')),
        firstCard.locator('.album-reorder-button').nth(1).click(),
      ]);
      expect(moveResponse.ok()).toBe(true);
      await expect.poll(() => albumCards.evaluateAll(cards =>
        cards.map(card => (card as HTMLElement).dataset.selectId))).toEqual([
        originalIds[1], originalIds[0], ...originalIds.slice(2),
      ]);
      const [restoreResponse] = await Promise.all([
        page.waitForResponse(response => response.url().includes('/reorder')),
        page.locator(`[data-select-id="${originalIds[0]}"] .album-reorder-button`).first().click(),
      ]);
      expect(restoreResponse.ok()).toBe(true);
      await expect.poll(() => albumCards.evaluateAll(cards =>
        cards.map(card => (card as HTMLElement).dataset.selectId))).toEqual(originalIds);
    }

    await page.goto('/pages/1/design');
    await expect(page.locator('.grid-stack')).toHaveClass(/is-direct-controls/);
    const orderBefore = await page.evaluate(() => {
      const grid = (window as any).PHOTO_ORGANIZER.pages.grid.grid;
      return [...grid.engine.nodes]
        .sort((a: any, b: any) => a.y - b.y || a.x - b.x)
        .map((node: any) => String(node.id));
    });
    if (orderBefore.length > 1) {
      const firstHeight = await page.evaluate((id) => {
        const grid = (window as any).PHOTO_ORGANIZER.pages.grid.grid;
        return grid.engine.nodes.find((node: any) => String(node.id) === id).h;
      }, orderBefore[0]);
      await page.locator(`[gs-id="${orderBefore[0]}"] .widget-size-taller`).click();
      await expect.poll(() => page.evaluate((id) => {
        const grid = (window as any).PHOTO_ORGANIZER.pages.grid.grid;
        return grid.engine.nodes.find((node: any) => String(node.id) === id).h;
      }, orderBefore[0])).toBe(firstHeight + 1);
      await page.locator(`[gs-id="${orderBefore[0]}"] .widget-size-shorter`).click();
      await expect.poll(() => page.evaluate((id) => {
        const grid = (window as any).PHOTO_ORGANIZER.pages.grid.grid;
        return grid.engine.nodes.find((node: any) => String(node.id) === id).h;
      }, orderBefore[0])).toBe(firstHeight);
      await page.locator(`[gs-id="${orderBefore[0]}"] .widget-order-down`).click();
      await expect.poll(() => page.evaluate(() => {
        const grid = (window as any).PHOTO_ORGANIZER.pages.grid.grid;
        return [...grid.engine.nodes]
          .sort((a: any, b: any) => a.y - b.y || a.x - b.x)
          .map((node: any) => String(node.id));
      })).not.toEqual(orderBefore);
    }
    await expectNoPageOverflow(page);
  });
});
