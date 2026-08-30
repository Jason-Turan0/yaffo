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
import {changedDependencies} from "./dependency_changes";
import type {DependencyChange, PageLock} from "./dependency_changes";
import {changedStrings, changesQuotedBy} from "./strings";
import type {StringChange} from "./strings";

const CONTENT_DIR = resolve(join(process.cwd(), "user_doc_automation"));
const REPO = resolve(join(process.cwd(), ".."));
const GUIDE_DIR = resolve(process.env.GUIDE_DIR || join(REPO, "docs", "guide"));

const split = (page: string): [string, string] =>
    [page.slice(0, page.indexOf("/")), page.slice(page.indexOf("/") + 1)];

const pageLock = (page: string, contentDir: string): PageLock | null => {
    const [area, name] = split(page);
    const lock = join(contentDir, area, name, `${name}.lock.json`);
    if (!existsSync(lock)) return null;
    return JSON.parse(readFileSync(lock, "utf8")) as PageLock;
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
    changedDependencies?: (lock: PageLock, alsoDependsOn: string[]) => DependencyChange[];
}

export interface DetectionResult {
    flagged: Array<{page: string; changes: StringChange[]; dependencies: DependencyChange[]}>;
    scanned: number;
    unwatermarked: number;
}

export const detect = (options: DetectionOptions = {}): DetectionResult => {
    const contentDir = options.contentDir ?? CONTENT_DIR;
    const guideDir = options.guideDir ?? GUIDE_DIR;
    const findChanges = options.changedStrings ?? changedStrings;
    const findDependencyChanges = options.changedDependencies ?? changedDependencies;

    const spec = parseYaml(readFileSync(join(contentDir, "spec.yaml"), "utf8"));
    const pages: Record<string, {also_depends_on?: string[]}> = spec.pages ?? {};

    // Diffs are cached per base: pages sharing a watermark share the catalogue
    // comparison, which is the expensive half.
    const byBase = new Map<string, StringChange[]>();
    const flagged: DetectionResult["flagged"] = [];
    let unwatermarked = 0;

    for (const [page, entry] of Object.entries(pages)) {
        const lock = pageLock(page, contentDir);
        const base = options.overrideBase ?? lock?.lastVerifiedSha ?? null;
        const dependencies = lock
            ? findDependencyChanges(lock, entry.also_depends_on ?? [])
            : [];
        let changes: StringChange[] = [];
        if (!base) {
            // A page never promoted has nothing to diff from. Saying so is more useful
            // than silently reporting it clean.
            unwatermarked++;
        } else {
            if (!byBase.has(base)) byBase.set(base, findChanges(base));
            changes = byBase.get(base) as StringChange[];
        }

        const markdownPath = join(guideDir, `${page}.md`);
        const quoted = changes.length && existsSync(markdownPath)
            ? changesQuotedBy(readFileSync(markdownPath, "utf8"), changes)
            : [];
        if (quoted.length || dependencies.length) flagged.push({page, changes: quoted, dependencies});
    }

    const scanned = Object.keys(pages).length - unwatermarked;
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
        console.log(`✅ ${scanned} page(s) checked — no relevant dependency or quoted-string changes.`);
    } else {
        for (const {page, changes, dependencies} of flagged) {
            console.log(`\n${page}`);
            for (const dependency of dependencies) {
                console.log(`   dependency changed: ${dependency.path}`);
            }
            for (const change of changes) console.log(`   ${describe(change)}`);
        }
        console.log(`\n${flagged.length} page(s) need documentation regeneration.`);
    }
    if (unwatermarked) {
        console.log(`${unwatermarked} page(s) skipped by quoted-string detection: ` +
            "no watermark yet (never promoted).");
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
