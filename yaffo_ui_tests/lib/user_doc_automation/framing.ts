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
export const paddedBox = async (page: Page, selector: string, padding = PAD): Promise<Box> => {
    const box = await page.locator(selector).first().boundingBox();
    if (!box) throw new Error(`No bounding box for ${selector}`);
    const pageSize = await page.evaluate(() => ({
        width: document.documentElement.scrollWidth,
        height: document.documentElement.scrollHeight,
    }));
    const x = Math.max(0, box.x - padding);
    const y = Math.max(0, box.y - padding);
    return {
        x,
        y,
        width: Math.min(box.width + padding * 2, pageSize.width - x),
        height: Math.min(box.height + padding * 2, pageSize.height - y),
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
    const clip = await paddedBox(page, shot.clip, shot.clipPadding);
    if (shot.rows) {
        clip.height = Math.max(0, (await rowCut(page, shot.rows)) - clip.y);
    }
    return clip;
};

/**
 * Boxes to exclude from comparison, expressed in the captured image's physical
 * pixels and relative to its origin. Playwright reports element geometry in CSS
 * pixels, so the device scale must be applied before the differ sees these boxes.
 * The published image is never masked.
 */
export const resolveIgnoreRegions = async (
    page: Page,
    shot: Shot,
    clip: Box | undefined,
    deviceScaleFactor: number
): Promise<Box[]> => {
    if (!shot.ignoreRegions?.length) return [];
    const origin = clip ?? {x: 0, y: 0, width: 0, height: 0};
    const boxes: Box[] = [];
    for (const selector of shot.ignoreRegions) {
        const box = await page.locator(selector).first().boundingBox();
        if (!box) continue;
        boxes.push({
            x: (box.x - origin.x) * deviceScaleFactor,
            y: (box.y - origin.y) * deviceScaleFactor,
            width: box.width * deviceScaleFactor,
            height: box.height * deviceScaleFactor,
        });
    }
    return boxes;
};

/** Where an annotation sits in its image, in percent of the image's width and height. */
export interface AnnotationGeometry {
    /** `left top width height`, as the markdown's `data-region` takes it. */
    region: [number, number, number, number];
    /** The numbered marker's centre. */
    marker: [number, number];
}

const tenth = (value: number): number => Math.round(value * 10) / 10;
const clamp = (value: number, low: number, high: number): number => Math.min(high, Math.max(low, value));

/** Below this width and height, in percent, a region is a control rather than a panel. */
const SMALL = 12;

/**
 * Turn element boxes into an annotation, relative to the image they appear in.
 *
 * Several boxes are framed together. A control gets its marker beside it, so the
 * marker never hides the label it explains; on the image's right edge that is its
 * left side. A panel gets its marker inside its top-right corner, which is usually
 * empty, rather than outside where it lands on the neighbouring panel.
 */
export const annotationGeometry = (
    boxes: Box[],
    origin: {x: number; y: number},
    size: {width: number; height: number}
): AnnotationGeometry => {
    const left = Math.min(...boxes.map((box) => box.x));
    const top = Math.min(...boxes.map((box) => box.y));
    const right = Math.max(...boxes.map((box) => box.x + box.width));
    const bottom = Math.max(...boxes.map((box) => box.y + box.height));
    const x0 = clamp((left - origin.x) / size.width * 100, 0, 100);
    const y0 = clamp((top - origin.y) / size.height * 100, 0, 100);
    const x1 = clamp((right - origin.x) / size.width * 100, 0, 100);
    const y1 = clamp((bottom - origin.y) / size.height * 100, 0, 100);
    const region: [number, number, number, number] = [tenth(x0), tenth(y0), tenth(x1 - x0), tenth(y1 - y0)];
    const small = x1 - x0 < SMALL && y1 - y0 < SMALL;
    const markerX = !small ? x1 - 1.5 : x1 > 96 ? x0 - 1.5 : x1 + 1.5;
    const markerY = small ? (y0 + y1) / 2 : y0 + Math.min((y1 - y0) / 2, 2.5);
    return {region, marker: [tenth(clamp(markerX, 1, 99)), tenth(clamp(markerY, 1, 99))]};
};

/** How close two markers may sit, in percent, before the later one moves. */
const MARKER_GAP = 3;

/**
 * Move markers that would overlap down until they don't. Nested regions share a
 * top-right corner (a panel and the header inside it), and two markers in one spot
 * read as one.
 */
export const spreadMarkers = (geometry: Record<string, AnnotationGeometry>): void => {
    const placed: [number, number][] = [];
    for (const annotation of Object.values(geometry)) {
        const [x] = annotation.marker;
        let [, y] = annotation.marker;
        while (placed.some(([px, py]) => Math.abs(px - x) < MARKER_GAP && Math.abs(py - y) < MARKER_GAP) && y < 99) {
            y = Math.min(99, y + MARKER_GAP);
        }
        annotation.marker = [x, tenth(y)];
        placed.push(annotation.marker);
    }
};

/** The part of the page a screenshot actually contains: the clip, cut to the viewport. */
export const capturedArea = (clip: Box | undefined, viewport: {width: number; height: number}): Box => {
    if (!clip) return {x: 0, y: 0, ...viewport};
    const x = Math.max(clip.x, 0);
    const y = Math.max(clip.y, 0);
    return {
        x,
        y,
        width: Math.min(clip.x + clip.width, viewport.width) - x,
        height: Math.min(clip.y + clip.height, viewport.height) - y,
    };
};

/**
 * Measure a shot's annotations in the state it is captured in. A selector that frames
 * nothing visible is a walkthrough defect, so it fails the shot rather than leaving a
 * marker pointing at stale coordinates.
 */
export const resolveAnnotations = async (
    page: Page,
    shot: Shot,
    clip: Box | undefined
): Promise<Record<string, AnnotationGeometry>> => {
    // The image is the clip cut to the viewport: a clip taller than the viewport is
    // silently truncated (see `Shot.viewport`), so measuring against the clip itself
    // would place every marker too high, by more the further down it sits.
    const visible = capturedArea(clip, shot.viewport);
    const origin = visible;
    const size = visible;
    const geometry: Record<string, AnnotationGeometry> = {};
    for (const [id, selector] of Object.entries(shot.annotations ?? {})) {
        const boxes: Box[] = [];
        for (const element of await page.locator(selector).all()) {
            const box = await element.boundingBox();
            if (box && box.width > 0 && box.height > 0) boxes.push(box);
        }
        if (!boxes.length) throw new Error(`Annotation ${id}: no visible element for ${selector}`);
        geometry[id] = annotationGeometry(boxes, origin, size);
    }
    spreadMarkers(geometry);
    return geometry;
};
