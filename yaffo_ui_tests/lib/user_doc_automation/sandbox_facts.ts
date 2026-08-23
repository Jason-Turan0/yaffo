/**
 * Concrete facts about the sandbox, gathered before the agent starts.
 *
 * A generate run for `library-basics/photo-details` was observed spending some forty
 * rounds trying to work out one thing: how to reach a media item that exists. It read
 * route modules, templates, `common.py` for the data directory, lock files,
 * `raw.json`, the fixture-seeding code, and finally its own API logs — because the
 * answer is runtime state and none of those files contain it.
 *
 * The fix is not a better prompt telling it how to search. It is to stop making it
 * search: the pipeline can ask the running app directly and hand the answer over.
 *
 * What is handed over is **filenames, never ids**. Ids are assigned at index time and
 * change on every reseed, so a walkthrough built on one documents whichever item lands
 * at that number next time. Filenames survive. Reporting ids at all — even "for
 * orientation" — just puts the unstable thing in front of the model.
 */

/**
 * A fixture file that is always present, pinned by name. The Playwright specs pin the
 * same one (`PRIMARY_DETAIL_IMAGE` in `generated_tests/_support/media-test-data.ts`);
 * keeping both on one file means a fixture change breaks them together rather than
 * silently skewing the docs.
 */
export const PRIMARY_DETAIL_IMAGE = "2015-09-10_153200_daughter-one-year-portrait.png";

export interface SandboxFacts {
    /** Photo filenames, newest first. Basenames — the directory is a per-run temp dir. */
    photos: string[];
    /** Video filenames, newest first. */
    videos: string[];
    /** Set when the sandbox could not be read; the prompt then says so plainly. */
    error?: string;
}

/** How many of each to report. Enough to choose from, not enough to bury the prompt. */
const SAMPLE = 8;

/**
 * Fixture files that exist to be duplicates, not to be looked at. They are synthetic
 * test patterns, and the duplicate-detection fixture needs them, so they cannot be
 * removed — but a screenshot of one looks like a broadcast test card rather than a
 * photo library, so a walkthrough should never frame one.
 */
const TEST_PATTERN = /^1mb-example-video-file/;

const fetchText = async (url: string): Promise<string> => {
    const response = await fetch(url);
    if (!response.ok) throw new Error(`${url} -> HTTP ${response.status}`);
    return response.text();
};

/**
 * Filenames from a gallery listing.
 *
 * Each card carries its absolute path in the thumbnail's `title`. Only the basename is
 * kept: the fixture lives under a per-run temp directory
 * (`/private/var/.../yaffo_test_20260823_054353/...`), so the leading path is exactly
 * the sort of value that looks stable in a prompt and is not.
 */
const filenamesFrom = (html: string): string[] => [...new Set(
    [...html.matchAll(/title="([^"]*\/([^"/]+\.[a-z0-9]{2,5}))"/gi)].map((m) => m[2]))];

export const gatherSandboxFacts = async (baseUrl: string): Promise<SandboxFacts> => {
    try {
        // The app classifies these itself, so there is no need to guess from the
        // extension — and no need to open each item to find out.
        const [photoHtml, videoHtml] = await Promise.all([
            fetchText(`${baseUrl}/?media-type=photo&page-size=250`),
            fetchText(`${baseUrl}/?media-type=video&page-size=250`),
        ]);
        const photos = filenamesFrom(photoHtml);
        const videos = filenamesFrom(videoHtml);
        if (!photos.length && !videos.length) {
            return {photos: [], videos: [], error: "the gallery returned no media items"};
        }
        return {photos, videos};
    } catch (e) {
        return {photos: [], videos: [],
                error: e instanceof Error ? e.message : String(e)};
    }
};

/** The facts as a prompt section. Explicit about being runtime state, not source. */
export const describeSandboxFacts = (facts: SandboxFacts): string => {
    if (facts.error) {
        return `## The sandbox\n\nCould not be read (${facts.error}). Drive it with the ` +
            `browser tools to find what you need; do not guess filenames.`;
    }
    const list = (names: string[]): string => names.length
        ? names.slice(0, SAMPLE).map((n) => `  - \`${n}\`` +
            (TEST_PATTERN.test(n) ? "  ← synthetic test pattern, do not screenshot" : ""))
            .join("\n")
        : "  - (none in the fixture)";

    return [
        "## The sandbox, as it is right now",
        "",
        "Runtime state, not source. Which media items exist is decided at index time, so",
        "the code does not contain it and no amount of reading will reveal it.",
        "",
        "### Reach an item by filename, never by id",
        "",
        "Ids are assigned at index time and change on every reseed, so",
        '`goto: "/media/view/31"` documents whichever item lands at 31 next time. `goto`',
        "takes a function for exactly this reason:",
        "",
        "```typescript",
        `const PRIMARY_DETAIL_IMAGE = "${PRIMARY_DETAIL_IMAGE}";`,
        "",
        "goto: ({mediaIdByFilename}) =>",
        "    mediaIdByFilename(PRIMARY_DETAIL_IMAGE).then((id) => `/media/view/${id}`),",
        "```",
        "",
        "The Playwright specs pin the same file the same way — see",
        "`generated_tests/_support/media-test-data.ts`.",
        "",
        "### Files in the fixture, newest first",
        "",
        "Photos:",
        list(facts.photos),
        "",
        "Videos:",
        list(facts.videos),
        "",
        "Prefer a file with real content for any shot: some fixture files exist only so",
        "duplicate detection has something to find, and photograph as colour bars.",
        "",
        "### Useful queries on the gallery",
        "",
        "- `/?path=<filename>&page-size=250` — the item with that filename",
        "- `/?media-type=photo` or `video` — one kind only",
        "- a detail page reports its faces as `Faces (n)`",
        "",
        "A card links through its `onclick`, not an `href`, so a selector like",
        '`a[href*="/media/view/"]` finds nothing.',
    ].join("\n");
};
