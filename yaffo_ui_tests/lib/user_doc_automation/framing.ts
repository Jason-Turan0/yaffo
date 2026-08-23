import type {Page} from "@playwright/test";
import type {RowRule, Shot} from "./types";

export interface Box {
    x: number;
    y: number;
    width: number;
    height: number;
}

/** Breathing room around a clipped element, in CSS px. */
export const PAD = 16;

/** The element's box, padded and clamped to the page so the clip stays on canvas. */
export const paddedBox = async (page: Page, selector: string): Promise<Box> => {
    const box = await page.locator(selector).first().boundingBox();
    if (!box) throw new Error(`No bounding box for ${selector}`);
    const pageSize = await page.evaluate(() => ({
        width: document.documentElement.scrollWidth,
        height: document.documentElement.scrollHeight,
    }));
    const x = Math.max(0, box.x - PAD);
    const y = Math.max(0, box.y - PAD);
    return {
        x,
        y,
        width: Math.min(box.width + PAD * 2, pageSize.width - x),
        height: Math.min(box.height + PAD * 2, pageSize.height - y),
    };
};

/**
 * Where to cut a grid so the shot ends on a whole row. Clipping at an arbitrary
 * height slices the last row of tiles in half, which is the most obvious tell that
 * a screenshot was machine-made.
 */
export const rowCut = async (page: Page, rule: RowRule): Promise<number> => {
    return page.evaluate(
        ({grid, item, count}) => {
            const container = document.querySelector(grid);
            if (!container) throw new Error(`No grid ${grid}`);
            const tiles = Array.from(container.querySelectorAll(item)) as HTMLElement[];
            if (!tiles.length) throw new Error(`No items ${item}`);
            // Bucket tiles into rows by top offset with a small tolerance; tiles in
            // one row can differ by a pixel or two. A row's edge is its tallest tile.
            const rows: {top: number; bottom: number}[] = [];
            for (const tile of tiles) {
                const rect = tile.getBoundingClientRect();
                const top = rect.top + window.scrollY;
                const bottom = rect.bottom + window.scrollY;
                const row = rows.find((r) => Math.abs(r.top - top) < 8);
                if (row) {
                    row.bottom = Math.max(row.bottom, bottom);
                } else {
                    rows.push({top, bottom});
                }
            }
            rows.sort((a, b) => a.top - b.top);
            const last = rows[Math.min(count, rows.length) - 1];
            const next = rows[Math.min(count, rows.length)];
            // Cut halfway into the gap before the next row. Cutting at the row's own
            // bottom plus a fixed pad overshoots whenever the pad exceeds the grid
            // gap, which leaves a sliver of the next row in the shot.
            return next ? (last.bottom + next.top) / 2 : last.bottom;
        },
        rule
    );
};

/** The crop for a shot, or undefined for a plain viewport capture. */
export const resolveClip = async (page: Page, shot: Shot): Promise<Box | undefined> => {
    if (!shot.clip) return undefined;
    const clip = await paddedBox(page, shot.clip);
    if (shot.rows) {
        clip.height = Math.max(0, (await rowCut(page, shot.rows)) - clip.y);
    }
    return clip;
};

/**
 * Boxes to exclude from comparison, expressed relative to the captured image's
 * own origin so a differ can mask them without knowing about the clip. The
 * published image is never masked.
 */
export const resolveIgnoreRegions = async (
    page: Page,
    shot: Shot,
    clip: Box | undefined
): Promise<Box[]> => {
    if (!shot.ignoreRegions?.length) return [];
    const origin = clip ?? {x: 0, y: 0, width: 0, height: 0};
    const boxes: Box[] = [];
    for (const selector of shot.ignoreRegions) {
        const box = await page.locator(selector).first().boundingBox();
        if (!box) continue;
        boxes.push({x: box.x - origin.x, y: box.y - origin.y, width: box.width, height: box.height});
    }
    return boxes;
};
