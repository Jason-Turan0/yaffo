/**
 * Shared helpers for the responsive suite.
 *
 * One spec per page family owns its own file (see docs/development/responsive.md,
 * "Shared gates and ownership" — S3): page agents add cases to their family's
 * spec and import the contract from here, so nobody has to edit a shared spec to
 * cover their own page. Anything asserted about the *contract* rather than about
 * one page belongs in this module.
 */
import { expect, Browser, BrowserContext, Page } from '@playwright/test';

export const BASE_URL = process.env.BASE_URL || 'http://127.0.0.1:5001';

/** Widths the support contract requires every page family to survive. */
export const CONTRACT_WIDTHS = [320, 390, 768, 1024, 1440] as const;

/** The named viewport classes from the support contract. */
export const VIEWPORTS = {
  minimum: { width: 320, height: 568 },
  narrow: { width: 390, height: 844 },
  narrowLandscape: { width: 844, height: 390 },
  tabletPortrait: { width: 768, height: 1024 },
  tabletLandscape: { width: 1024, height: 768 },
  desktop: { width: 1440, height: 900 },
} as const;

/**
 * Fails with the elements responsible, not just the fact of the overflow: a bare
 * "scrollWidth > clientWidth" tells a page owner nothing about what to fix.
 */
export async function expectNoPageOverflow(page: Page): Promise<void> {
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

/** Navigate to a route and assert it renders without page-level overflow. */
export async function expectRouteFits(page: Page, route: string): Promise<void> {
  await page.goto(route);
  await expect(page.locator('body')).toBeVisible();
  await expectNoPageOverflow(page);
}

/**
 * A surface that is meant to sit inside the viewport actually does. Use for
 * dialogs, sheets, and panels — overflow diagnostics skip `position: fixed`, so
 * a modal hanging off-screen passes the page-level check.
 */
export async function expectFitsViewport(page: Page, selector: string): Promise<void> {
  const box = await page.locator(selector).boundingBox();
  expect(box, `${selector} has no box`).not.toBeNull();
  const viewport = page.viewportSize();
  expect(viewport).not.toBeNull();
  expect(box!.x, `${selector} starts left of the viewport`).toBeGreaterThanOrEqual(-1);
  expect(box!.y, `${selector} starts above the viewport`).toBeGreaterThanOrEqual(-1);
  expect(box!.x + box!.width, `${selector} runs past the right edge`)
    .toBeLessThanOrEqual(viewport!.width + 1);
  expect(box!.y + box!.height, `${selector} runs past the bottom edge`)
    .toBeLessThanOrEqual(viewport!.height + 1);
}

/**
 * The narrow-screen peer-panel contract, asserted end to end for one page:
 * hidden on desktop, peer of Menu on narrow, mutually exclusive with it, an
 * active state, and live DOM preserved across a resize.
 *
 * Every page family that registers a panel should call this once, so the
 * contract is verified where it is consumed rather than only on Home.
 *
 * Keyboard and focus behaviour is deliberately out of scope here — that is the
 * accessibility workstream's concern (docs/development/accessibility.md), which
 * asserts it with a rule engine rather than hand-written expectations.
 */
export async function expectPanelContract(
  page: Page,
  options: { route: string; panelId: string; probeSelector?: string },
): Promise<void> {
  const { route, panelId, probeSelector } = options;
  const toggle = page.locator(`#${panelId}-toggle`);
  const panel = page.locator(`#${panelId}`);
  const menuToggle = page.locator('#nav-menu-toggle');

  await page.setViewportSize(VIEWPORTS.narrow);
  await page.goto(route);

  // Closed on first paint, before any script has run.
  await expect(toggle).toBeVisible();
  await expect(toggle).toHaveAttribute('aria-expanded', 'false');
  await expect(panel).toBeHidden();

  // Peer of Menu: same parent, and a real gap between the two targets.
  expect(await toggle.evaluate(element => element.parentElement?.className))
    .toBe(await menuToggle.evaluate(element => element.parentElement?.className));

  const closedBackground = await toggle.evaluate(element => getComputedStyle(element).backgroundColor);
  await toggle.click();
  await expect(toggle).toHaveAttribute('aria-expanded', 'true');
  await expect(panel).toBeVisible();
  expect(await toggle.evaluate(element => getComputedStyle(element).backgroundColor))
    .not.toBe(closedBackground);

  // Mutual exclusion: opening Menu closes the panel and vice versa.
  await menuToggle.click();
  await expect(menuToggle).toHaveAttribute('aria-expanded', 'true');
  await expect(toggle).toHaveAttribute('aria-expanded', 'false');
  await expect(panel).toBeHidden();
  await toggle.click();
  await expect(menuToggle).toHaveAttribute('aria-expanded', 'false');
  await expect(panel).toBeVisible();

  // The live DOM survives the trip back to desktop — panels are moved, not
  // re-rendered, so anything the user had typed or selected is still there.
  const probe = probeSelector ?? `#${panelId}`;
  await page.locator(probe).evaluate((element) => {
    const input = document.createElement('input');
    input.id = 'responsive-panel-state-probe';
    input.value = 'preserved';
    element.append(input);
  });
  await page.setViewportSize(VIEWPORTS.desktop);
  await expect(toggle).toBeHidden();
  await expect(menuToggle).toBeHidden();
  await expect(panel).toBeVisible();
  await expect(page.locator('#responsive-panel-state-probe')).toHaveValue('preserved');
  await expectNoPageOverflow(page);
}

/** A context with a real coarse pointer, for hover-alternative and drag cases. */
export async function withTouchContext(
  browser: Browser,
  viewport: { width: number; height: number },
  run: (page: Page, context: BrowserContext) => Promise<void>,
): Promise<void> {
  const context = await browser.newContext({
    baseURL: BASE_URL,
    viewport,
    hasTouch: true,
    isMobile: true,
  });
  try {
    await run(await context.newPage(), context);
  } finally {
    await context.close();
  }
}

/**
 * Drag with Chrome's real emulated touch stream. Synthetic pointer events pass
 * against handlers that touch never reaches, so drag coverage that matters has
 * to go through CDP.
 */
export async function touchDrag(
  context: BrowserContext,
  page: Page,
  from: { x: number; y: number },
  to: { x: number; y: number },
  steps = 8,
): Promise<void> {
  const cdp = await context.newCDPSession(page);
  const point = (x: number, y: number) => ({ x, y, radiusX: 2, radiusY: 2, force: 1, id: 1 });
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [point(from.x, from.y)] });
  for (let step = 1; step <= steps; step += 1) {
    await cdp.send('Input.dispatchTouchEvent', {
      type: 'touchMove',
      touchPoints: [point(
        from.x + ((to.x - from.x) * step) / steps,
        from.y + ((to.y - from.y) * step) / steps,
      )],
    });
  }
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
}
