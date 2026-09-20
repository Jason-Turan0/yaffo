import {auditTouchScreenshots} from "@lib/services/touch_screenshot_policy";

const audit = (code: string) => auditTouchScreenshots(code, "spec.ts");

describe("auditTouchScreenshots", () => {
    it("flags a fullPage capture with work after it inside a touch context", () => {
        const violations = audit(`
            await withTouchContext(browser, VIEWPORTS.narrow, async (page) => {
                await page.goto('/sharing');
                await page.screenshot({ fullPage: true });
                expect((await button.boundingBox())!.height).toBeGreaterThanOrEqual(44);
            });
        `);
        expect(violations).toHaveLength(1);
        expect(violations[0]).toContain("ends mobile emulation");
    });

    it("allows a fullPage capture that is the callback's last action", () => {
        expect(audit(`
            await withTouchContext(browser, VIEWPORTS.narrow, async (page) => {
                await page.goto('/sharing');
                expect((await button.boundingBox())!.height).toBeGreaterThanOrEqual(44);
                await page.screenshot({ fullPage: true });
            });
        `)).toEqual([]);
    });

    it("allows viewport screenshots anywhere in a touch context", () => {
        expect(audit(`
            await withTouchContext(browser, VIEWPORTS.narrow, async (page) => {
                await page.screenshot();
                await page.screenshot({ fullPage: false });
                expect(1).toBe(1);
            });
        `)).toEqual([]);
    });

    it("ignores fullPage captures outside a touch context", () => {
        expect(audit(`
            test('desktop', async ({ page }) => {
                await page.screenshot({ fullPage: true });
                expect(1).toBe(1);
            });
        `)).toEqual([]);
    });

    it("finds a capture nested inside a block, not just at the top level", () => {
        const violations = audit(`
            await withTouchContext(browser, VIEWPORTS.narrow, async (page) => {
                if (await thing.count() > 0) {
                    await page.screenshot({ fullPage: true });
                }
                expect(1).toBe(1);
            });
        `);
        expect(violations).toHaveLength(1);
    });

    it("reports a capture wrapped in an attach call", () => {
        const violations = audit(`
            await withTouchContext(browser, VIEWPORTS.narrow, async (page) => {
                await testInfo.attach('shot', { body: await page.screenshot({ fullPage: true }) });
                await page.tap('.thing');
            });
        `);
        expect(violations).toHaveLength(1);
    });
});
