import type {Page} from "@playwright/test";

/**
 * Wait until the page has stopped moving. Each of these is a real source of both
 * flake and diff noise: half-loaded lazy images, a font swap mid-capture, an
 * in-flight CSS transition, or a focus ring left on whatever was clicked last.
 */
export const settle = async (page: Page): Promise<void> => {
    // Streaming endpoints (thumbnail stats, timeline batches) never go idle, so a
    // timeout here is expected rather than an error.
    await page.waitForLoadState("networkidle").catch(() => undefined);
    await page.addStyleTag({
        content: `
            *, *::before, *::after {
                animation: none !important;
                transition: none !important;
                caret-color: transparent !important;
            }
            html { scroll-behavior: auto !important; }
            /* Transient UI that would otherwise land in a shot on timing alone. */
            .notification, .toast, #notification-container { display: none !important; }
        `,
    });
    await page.evaluate(async () => {
        await document.fonts.ready;
        await Promise.all(
            Array.from(document.images).map((img) =>
                img.complete
                    ? Promise.resolve()
                    : new Promise<void>((resolve) => {
                        img.addEventListener("load", () => resolve(), {once: true});
                        img.addEventListener("error", () => resolve(), {once: true});
                    })
            )
        );
    });
    // Blur whatever holds focus so no shot picks up a stray focus ring.
    await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur?.());
    await page.waitForTimeout(200);
};
