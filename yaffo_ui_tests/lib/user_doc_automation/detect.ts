/**
 * Detector B — which pages quote text the app no longer shows.
 *
 *   npm run docs:detect
 *   npm run docs:detect -- --base <sha>     # override the per-page watermark
 *
 * Reads each page's watermark from its lockfile and diffs the string catalogues from
 * there to HEAD. No sandbox, no model, no capture — so it is cheap enough to run before
 * deciding whether a heal is worth booting an environment for.
 *
 * Detector A is not run here: it needs a live sandbox and a full capture. Run
 * `npm run docs:capture` for that.
 */
import "dotenv/config";
import {existsSync, readFileSync} from "fs";
import {join, resolve} from "path";
import {pathToFileURL} from "url";
import {parse as parseYaml} from "yaml";
import {changedStrings, changesQuotedBy} from "./strings";
import type {StringChange} from "./strings";

const CONTENT_DIR = resolve(join(process.cwd(), "user_doc_automation"));
const REPO = resolve(join(process.cwd(), ".."));
const GUIDE_DIR = resolve(process.env.GUIDE_DIR || join(REPO, "docs", "guide"));

const split = (page: string): [string, string] =>
    [page.slice(0, page.indexOf("/")), page.slice(page.indexOf("/") + 1)];

const watermark = (page: string, contentDir: string): string | null => {
    const [area, name] = split(page);
    const lock = join(contentDir, area, name, `${name}.lock.json`);
    if (!existsSync(lock)) return null;
    return JSON.parse(readFileSync(lock, "utf8")).lastVerifiedSha ?? null;
};

const describe = (change: StringChange): string =>
    change.now !== undefined
        ? `"${change.was}" is now "${change.now}"  (${change.key})`
        : `"${change.was}" no longer exists  (${change.source}${change.key ? `: ${change.key}` : ""})`;

export interface DetectionOptions {
    contentDir?: string;
    guideDir?: string;
    overrideBase?: string;
    changedStrings?: (base: string) => StringChange[];
}

export interface DetectionResult {
    flagged: Array<{page: string; changes: StringChange[]}>;
    scanned: number;
    unwatermarked: number;
}

export const detect = (options: DetectionOptions = {}): DetectionResult => {
    const contentDir = options.contentDir ?? CONTENT_DIR;
    const guideDir = options.guideDir ?? GUIDE_DIR;
    const findChanges = options.changedStrings ?? changedStrings;

    const spec = parseYaml(readFileSync(join(contentDir, "spec.yaml"), "utf8"));
    const pages: string[] = Object.keys(spec.pages ?? {});

    // Diffs are cached per base: pages sharing a watermark share the catalogue
    // comparison, which is the expensive half.
    const byBase = new Map<string, StringChange[]>();
    const flagged: Array<{page: string; changes: StringChange[]}> = [];
    let unwatermarked = 0;

    for (const page of pages) {
        const base = options.overrideBase ?? watermark(page, contentDir);
        if (!base) {
            // A page never promoted has nothing to diff from. Saying so is more useful
            // than silently reporting it clean.
            unwatermarked++;
            continue;
        }
        if (!byBase.has(base)) byBase.set(base, findChanges(base));
        const changes = byBase.get(base) as StringChange[];
        if (!changes.length) continue;

        const markdownPath = join(guideDir, `${page}.md`);
        if (!existsSync(markdownPath)) continue;
        const quoted = changesQuotedBy(readFileSync(markdownPath, "utf8"), changes);
        if (quoted.length) flagged.push({page, changes: quoted});
    }

    const scanned = pages.length - unwatermarked;
    return {flagged, scanned, unwatermarked};
};

export const main = (
    args: string[] = process.argv.slice(2),
    options: Omit<DetectionOptions, "overrideBase"> = {}
): number => {
    const baseArg = args.indexOf("--base");
    const overrideBase = baseArg !== -1 ? args[baseArg + 1] : undefined;
    const {flagged, scanned, unwatermarked} = detect({...options, overrideBase});
    if (!flagged.length) {
        console.log(`✅ ${scanned} page(s) checked — none quotes a string the app has changed.`);
    } else {
        for (const {page, changes} of flagged) {
            console.log(`\n${page}`);
            for (const change of changes) console.log(`   ${describe(change)}`);
        }
        console.log(`\n${flagged.length} page(s) quote text the app no longer shows.`);
    }
    if (unwatermarked) {
        console.log(`${unwatermarked} page(s) skipped: no watermark yet (never promoted).`);
    }
    return flagged.length ? 2 : 0;
};

export const runCli = (
    args: string[] = process.argv.slice(2),
    options: Omit<DetectionOptions, "overrideBase"> = {}
): void => {
    try {
        process.exitCode = main(args, options);
    } catch (e) {
        console.error(e);
        process.exitCode = 1;
    }
};

const isDirectRun = process.argv[1] !== undefined &&
    import.meta.url === pathToFileURL(resolve(process.argv[1])).href;

if (isDirectRun) runCli();
