/**
 * Discover documentation pages that are ready for repository-level healing.
 *
 *   npm run docs:heal:repo
 *   npm run docs:heal:repo -- --github
 *   npm run docs:heal:repo -- --github library-basics/browsing-filtering
 *
 * Discovery deliberately does not decide whether a page is stale. Dependency hashes
 * are cheap to compare here, but visual drift is known only after a walkthrough runs.
 * GitHub therefore fans out every ready page; each job captures and heals one page in
 * its own sandbox.
 */
import {appendFileSync, existsSync, readFileSync} from "fs";
import {join, resolve} from "path";
import {pathToFileURL} from "url";
import {parse as parseYaml} from "yaml";
import {CONTENT_DIR} from "./paths";

interface PageSpec {
    walkthrough?: boolean;
}

interface DocumentationSpec {
    pages?: Record<string, PageSpec>;
}

export interface HealPageEntry {
    id: string;
    page: string;
}

export interface SkippedPage {
    page: string;
    issues: string[];
}

export interface HealRepoDiscovery {
    include: HealPageEntry[];
    skipped: SkippedPage[];
}

export interface HealRepoOptions {
    contentDir?: string;
    githubOutput?: string;
}

const split = (page: string): [string, string] => {
    const slash = page.indexOf("/");
    return slash === -1 ? [page, page] : [page.slice(0, slash), page.slice(slash + 1)];
};

const entryFor = (page: string): HealPageEntry => ({
    id: page.replace(/[^a-zA-Z0-9]+/g, "__"),
    page,
});

/** Pages with everything a matrix job needs to capture and heal them. */
export const discoverHealPages = (
    requested: string[] = [],
    options: HealRepoOptions = {}
): HealRepoDiscovery => {
    const contentDir = options.contentDir ?? CONTENT_DIR;
    const spec = parseYaml(readFileSync(join(contentDir, "spec.yaml"), "utf8")) as DocumentationSpec;
    const pages = spec.pages ?? {};
    const targets = requested.length ? [...new Set(requested)] : Object.keys(pages);
    const include: HealPageEntry[] = [];
    const skipped: SkippedPage[] = [];

    for (const page of targets.sort()) {
        const issues: string[] = [];
        const pageSpec = pages[page];
        if (!pageSpec) issues.push("not declared in spec.yaml");
        else if (pageSpec.walkthrough === false) issues.push("walkthrough disabled by spec.yaml");

        const [area, name] = split(page);
        const pageDir = join(contentDir, area, name);
        if (!existsSync(join(pageDir, `${name}.ts`))) issues.push("missing walkthrough");
        if (!existsSync(join(pageDir, `${name}.lock.json`))) issues.push("missing lockfile");

        if (issues.length) skipped.push({page, issues});
        else include.push(entryFor(page));
    }

    return {include, skipped};
};

export const main = (
    args: string[] = process.argv.slice(2),
    options: HealRepoOptions = {}
): number => {
    const emitGithub = args.includes("--github");
    const requested = args.filter((arg) => !arg.startsWith("--"));
    const discovery = discoverHealPages(requested, options);
    const matrix = JSON.stringify({include: discovery.include});
    process.stdout.write(`${matrix}\n`);

    for (const skipped of discovery.skipped) {
        console.error(`Skipping ${skipped.page}: ${skipped.issues.join("; ")}`);
    }

    const githubOutput = options.githubOutput ?? process.env.GITHUB_OUTPUT;
    if (emitGithub && githubOutput) {
        appendFileSync(githubOutput, `matrix=${matrix}\n`);
        appendFileSync(githubOutput, `has_pages=${discovery.include.length > 0}\n`);
    }
    return 0;
};

export const runCli = (
    args: string[] = process.argv.slice(2),
    options: HealRepoOptions = {}
): void => {
    try {
        process.exitCode = main(args, options);
    } catch (error) {
        console.error(error);
        process.exitCode = 1;
    }
};

const isDirectRun = process.argv[1] !== undefined &&
    import.meta.url === pathToFileURL(resolve(process.argv[1])).href;

if (isDirectRun) runCli();
