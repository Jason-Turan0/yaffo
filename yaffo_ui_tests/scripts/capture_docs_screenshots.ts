/**
 * Capture the user-guide screenshots against an isolated sandbox.
 *
 *   npm run isolatedEnvironment:start          # terminal 1 (port 5002)
 *   npx tsx scripts/capture_docs_screenshots.ts # terminal 2
 *
 * POC scope: the five shots docs/guide/start-here/getting-started.md uses.
 *
 * Output follows the docs asset convention: a shot used by more than one page is
 * shared and lands in docs/guide/assets/; a single-page shot lands in
 * docs/guide/{area}/assets/{page}/. Each shot declares its own `dest`.
 *
 * The point of this script is *framing*. Playwright's default capture (what the
 * failure artifacts use) is a full-page shot of a full-width app layout, which is
 * mostly dead space. Every shot here is clipped to the element that actually
 * carries the meaning, captured at DPR 2, after the page has visibly settled.
 */
import {chromium, Browser, Page} from "@playwright/test";
import {execFileSync} from "child_process";
import {mkdirSync, unlinkSync} from "fs";
import {join, resolve} from "path";

const BASE_URL = process.env.BASE_URL || "http://127.0.0.1:5002";
const GUIDE_DIR = resolve(process.env.GUIDE_DIR || join(process.cwd(), "..", "docs", "guide"));
/** Pillow (already a project dependency) does the PNG -> WebP conversion. */
const VENV_PYTHON = resolve(join(process.cwd(), "..", "venv", "bin", "python"));
/** WebP quality. 88 is visually lossless on UI text and ~10x smaller than PNG on
 *  screenshots containing photographs. */
const WEBP_QUALITY = 88;

/** Retina. The docs site renders these at ~half their pixel width, so 1x looks soft. */
const DEVICE_SCALE_FACTOR = 2;
/** Breathing room around a clipped element, in CSS px. */
const PAD = 16;

interface Box {
    x: number;
    y: number;
    width: number;
    height: number;
}

interface Shot {
    name: string;
    url: string;
    viewport: {width: number; height: number};
    /** Element whose box defines the crop. Omit for a plain viewport shot. */
    clip?: string;
    /** Extend the crop down to the bottom of the Nth complete row of this grid. */
    rows?: {grid: string; item: string; count: number};
    /** Drive the page into the state the shot needs before capturing. */
    setup?: (page: Page) => Promise<void>;
    /** Alt text for the docs page. Kept next to the shot so they can't drift apart. */
    alt: string;
    /**
     * Destination directory, relative to docs/guide. "assets" for a shot used by
     * more than one page; "{area}/assets/{page}" for a single-page shot.
     */
    dest: string;
}

/**
 * Wait until the page has stopped moving. Each of these is a real source of both
 * flake and diff-noise: half-loaded lazy images, a font swap mid-capture, an
 * in-flight CSS transition, or a focus ring on whatever was clicked last.
 */
const settle = async (page: Page): Promise<void> => {
    await page.waitForLoadState("networkidle").catch(() => { /* long-poll streams never idle */ });
    await page.addStyleTag({
        content: `
            *, *::before, *::after {
                animation: none !important;
                transition: none !important;
                caret-color: transparent !important;
            }
            html { scroll-behavior: auto !important; }
            /* Toasts and the scroll-to-top affordance are transient UI that would
               otherwise land in a shot depending on timing alone. */
            .notification, .toast, #notification-container { display: none !important; }
        `,
    });
    await page.evaluate(async () => {
        await document.fonts.ready;
        await Promise.all(
            Array.from(document.images).map((img) =>
                img.complete
                    ? Promise.resolve()
                    : new Promise<void>((r) => {
                        img.addEventListener("load", () => r(), {once: true});
                        img.addEventListener("error", () => r(), {once: true});
                    })
            )
        );
    });
    // Blur whatever holds focus so no shot picks up a stray focus ring.
    await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur?.());
    await page.waitForTimeout(200);
};

/** The element's box, padded and clamped to the page so the clip stays on-canvas. */
const paddedBox = async (page: Page, selector: string): Promise<Box> => {
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
 * Bottom edge of the Nth complete row of a grid. A grid clipped to an arbitrary
 * height slices the last row of tiles in half — the single most obvious tell that
 * a screenshot was machine-made (the current gallery-home.png does exactly this).
 */
const rowBottom = async (page: Page, grid: string, item: string, count: number): Promise<number> => {
    return page.evaluate(
        ({grid, item, count}) => {
            const container = document.querySelector(grid);
            if (!container) throw new Error(`No grid ${grid}`);
            const tiles = Array.from(container.querySelectorAll(item)) as HTMLElement[];
            if (!tiles.length) throw new Error(`No items ${item}`);
            // Group tiles into rows by their top offset; tiles in one row can differ
            // by a pixel or two, so bucket with a small tolerance rather than by
            // exact equality. Each row's edge is its tallest tile's bottom.
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
            // gap, which puts a sliver of the next row's tiles in the shot.
            return next ? (last.bottom + next.top) / 2 : last.bottom;
        },
        {grid, item, count}
    );
};

/**
 * Re-encode a captured PNG as WebP and drop the PNG. Screenshots that contain
 * photographs are enormous as PNG (3-4 MB each at 2x); WebP q88 shows no visible
 * artifacts on UI text and cuts them to roughly a tenth of that.
 */
const toWebp = (pngPath: string): void => {
    const webpPath = pngPath.replace(/\.png$/, ".webp");
    execFileSync(VENV_PYTHON, [
        "-c",
        "import sys;from PIL import Image;" +
        "Image.open(sys.argv[1]).convert('RGB')" +
        ".save(sys.argv[2],'WEBP',quality=int(sys.argv[3]),method=6)",
        pngPath, webpPath, String(WEBP_QUALITY),
    ]);
    unlinkSync(pngPath);
};

const SHOTS: Shot[] = [
    {
        name: "settings-overview",
        dest: "assets",
        url: "/settings",
        viewport: {width: 1400, height: 1200},
        clip: ".main-content",
        alt: "The Settings page showing Language, Units, and Media Directories",
        setup: async (page) => {
            // Only the first three sections belong in a Getting Started shot; the rest
            // of Settings has its own page in the guide.
            await page.evaluate(() => {
                const sections = Array.from(document.querySelectorAll(".settings-section"));
                sections.slice(3).forEach((s) => s.remove());
            });
        },
    },
    {
        name: "utilities-index-photos",
        dest: "assets",
        url: "/utilities/index-photos",
        viewport: {width: 1400, height: 1000},
        clip: ".utility-page",
        alt: "The Index Photos utility, showing library counts after a scan",
        setup: async (page) => {
            // Stats render as em-dashes until the scan streams in; a shot taken before
            // that is a page of placeholders.
            await page.waitForFunction(
                () => document.querySelector("#stat-total-filesystem")?.textContent?.trim() !== "—",
                undefined,
                {timeout: 30_000}
            ).catch(() => { /* fall through and capture whatever rendered */ });
        },
    },
    {
        name: "gallery-home",
        dest: "assets",
        url: "/?view=grid",
        viewport: {width: 1400, height: 1100},
        clip: ".main-container-layout",
        rows: {grid: ".photo-grid", item: ".photo-card", count: 2},
        alt: "The Home photo library: filter sidebar beside a grid of indexed photos and videos",
    },
    {
        name: "gallery-filter-sidebar",
        dest: "assets",
        // Tall enough that the whole sidebar lays out; a short viewport clips the
        // element's own box and the shot ends mid-filter.
        url: "/?view=grid",
        viewport: {width: 1400, height: 2000},
        clip: ".sidebar",
        alt: "The filter sidebar with Year, Month, People, Label, and Location filters",
    },
    {
        name: "media-detail",
        dest: "assets",
        url: "/media/view/6",
        viewport: {width: 1400, height: 1150},
        clip: ".photo-viewer",
        alt: "A photo's detail page with its preview, file and location metadata, assigned people, detected faces, and labels",
    },
];


const capture = async (browser: Browser, shot: Shot): Promise<void> => {
    const context = await browser.newContext({
        viewport: shot.viewport,
        deviceScaleFactor: DEVICE_SCALE_FACTOR,
        locale: "en-US",
        timezoneId: "America/Chicago",
        colorScheme: "light",
        reducedMotion: "reduce",
    });
    const page = await context.newPage();
    try {
        await page.goto(`${BASE_URL}${shot.url}`, {waitUntil: "domcontentloaded"});
        await settle(page);
        if (shot.setup) {
            await shot.setup(page);
            await settle(page);
        }

        let clip: Box | undefined;
        if (shot.clip) {
            clip = await paddedBox(page, shot.clip);
            if (shot.rows) {
                const cut = await rowBottom(page, shot.rows.grid, shot.rows.item, shot.rows.count);
                clip.height = Math.max(0, cut - clip.y);
            }
        }

        const outDir = join(GUIDE_DIR, shot.dest);
        mkdirSync(outDir, {recursive: true});
        const png = join(outDir, `${shot.name}.png`);
        await page.screenshot({path: png, clip, scale: "device"});
        toWebp(png);
        const size = clip
            ? `${Math.round(clip.width)}x${Math.round(clip.height)} @${DEVICE_SCALE_FACTOR}x`
            : `${shot.viewport.width}x${shot.viewport.height} @${DEVICE_SCALE_FACTOR}x`;
        console.log(`  ✅ ${shot.dest}/${shot.name}.webp  ${size}`);
    } finally {
        await context.close();
    }
};

const main = async (): Promise<void> => {
    console.log(`📸 Capturing ${SHOTS.length} shots from ${BASE_URL}`);
    console.log(`   → ${GUIDE_DIR}\n`);

    const only = process.argv.slice(2).filter((a) => !a.startsWith("-"));
    const shots = only.length ? SHOTS.filter((s) => only.includes(s.name)) : SHOTS;

    const browser = await chromium.launch();
    let failed = 0;
    try {
        for (const shot of shots) {
            try {
                await capture(browser, shot);
            } catch (e) {
                failed++;
                console.error(`  ❌ ${shot.name}: ${e instanceof Error ? e.message : String(e)}`);
            }
        }
    } finally {
        await browser.close();
    }
    if (failed) process.exit(1);
};

main().catch((e) => {
    console.error(e);
    process.exit(1);
});
