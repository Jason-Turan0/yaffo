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

  test('responsive sidebars preserve their controls across resize', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto('/');

    const toggle = page.locator('.responsive-panel-toggle');
    await expect(toggle).toBeVisible();
    await toggle.click();
    await expect(toggle).toHaveAttribute('aria-expanded', 'true');
    await expect(page.locator('#filter-form')).toBeVisible();

    await page.setViewportSize({ width: 1440, height: 900 });
    await expect(toggle).toBeHidden();
    await expect(page.locator('#filter-form')).toBeVisible();
    await expectNoPageOverflow(page);
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
    await page.goto('/faces');

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
