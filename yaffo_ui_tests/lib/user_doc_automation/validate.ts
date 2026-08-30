/**
 * Check the guide and its automation agree with each other.
 *
 *   npm run docs:validate
 *
 * Everything here is mechanical and needs no sandbox, so it is cheap enough to run in
 * CI on every push. It catches the drift that otherwise accumulates silently: an
 * orphaned screenshot, a page the spec forgot, a hand-declared dependency the
 * walkthrough already observes.
 */
import "dotenv/config";
import {existsSync, readFileSync, readdirSync, statSync} from "fs";
import {join, relative, resolve} from "path";
import {pathToFileURL} from "url";
import {parse as parseYaml} from "yaml";

const CONTENT_DIR = resolve(join(process.cwd(), "user_doc_automation"));
const REPO = resolve(join(process.cwd(), ".."));
const GUIDE_DIR = resolve(process.env.GUIDE_DIR || join(REPO, "docs", "guide"));

const IMAGE_SUFFIXES = [".png", ".webp", ".jpg", ".jpeg", ".svg", ".gif"];

export interface Problem {
    check: string;
    detail: string;
}

export interface ValidationOptions {
    contentDir?: string;
    repoDir?: string;
    guideDir?: string;
}

const split = (page: string): [string, string] =>
    [page.slice(0, page.indexOf("/")), page.slice(page.indexOf("/") + 1)];

const walk = (dir: string): string[] =>
    !existsSync(dir) ? [] : readdirSync(dir).flatMap((entry) => {
        const full = join(dir, entry);
        return statSync(full).isDirectory() ? walk(full) : [full];
    });

const guidePages = (guideDir: string): string[] =>
    walk(guideDir).filter((f) => f.endsWith(".md"))
        .map((f) => relative(guideDir, f).replace(/\.md$/, ""));

const imagesIn = (markdown: string): string[] =>
    [...markdown.matchAll(/!\[[^\]]*\]\(([^)]+)\)/g)].map((m) => m[1]);

export const validate = (options: ValidationOptions = {}): Problem[] => {
    const contentDir = options.contentDir ?? CONTENT_DIR;
    const repoDir = options.repoDir ?? REPO;
    const guideDir = options.guideDir ?? GUIDE_DIR;
    const spec = parseYaml(readFileSync(join(contentDir, "spec.yaml"), "utf8"));
    const pages: Record<string, {walkthrough?: boolean; also_depends_on?: string[]}> = spec.pages ?? {};
    const problems: Problem[] = [];
    const onDisk = guidePages(guideDir);

    // 1. The spec and the guide describe the same set of pages.
    for (const page of Object.keys(pages)) {
        if (!onDisk.includes(page)) problems.push({check: "spec", detail: `${page} is in spec.yaml but has no page`});
    }
    for (const page of onDisk) {
        if (!(page in pages)) problems.push({check: "spec", detail: `${page}.md has no spec.yaml entry`});
    }

    const referenced = new Set<string>();
    for (const page of onDisk) {
        const [area, name] = split(page);
        const markdown = readFileSync(join(guideDir, `${page}.md`), "utf8");
        const expected = resolve(guideDir, area, "assets", name);

        for (const reference of imagesIn(markdown)) {
            const image = resolve(guideDir, area, reference);
            referenced.add(image);
            // 2. Every reference resolves.
            if (!existsSync(image)) {
                problems.push({check: "images", detail: `${page}.md references ${reference}, which does not exist`});
                continue;
            }
            // 3. And lives in its own page's assets directory — there are no shared
            //    images, so a reference reaching elsewhere means the layout has drifted.
            if (resolve(image, "..") !== expected) {
                problems.push({check: "images", detail: `${page}.md references ${reference} from outside its own assets directory`});
            }
        }

        // 4. A page's walkthrough exists exactly when the spec says it should.
        const walkthrough = join(contentDir, area, name, `${name}.ts`);
        const declared = pages[page]?.walkthrough;
        if (declared === false && existsSync(walkthrough)) {
            problems.push({check: "walkthrough", detail: `${page} is marked walkthrough: false but one exists`});
        }
    }

    // 5. Nothing captured is left unreferenced.
    for (const file of walk(guideDir)) {
        if (!IMAGE_SUFFIXES.some((suffix) => file.endsWith(suffix))) continue;
        if (!referenced.has(resolve(file))) {
            problems.push({check: "images", detail: `${relative(guideDir, file)} is referenced by no page`});
        }
    }

    for (const [page, entry] of Object.entries(pages)) {
        const declared = entry.also_depends_on ?? [];
        if (!declared.length) continue;
        const [area, name] = split(page);

        // 6. A hand-declared dependency has to exist.
        for (const dependency of declared) {
            if (!existsSync(join(repoDir, dependency))) {
                problems.push({check: "depends", detail: `${page} declares ${dependency}, which does not exist`});
            }
        }

        // 7. And must still be something the walkthrough cannot see for itself.
        //    also_depends_on is an escape hatch for what driving the app cannot reach;
        //    left unchecked it silently becomes a second, stale dependency list beside
        //    the observed one. As the observer records more (see the lockfile's layer 4),
        //    entries here should be deleted rather than left to rot.
        const lockPath = join(contentDir, area, name, `${name}.lock.json`);
        if (!existsSync(lockPath)) continue;
        const observed = JSON.parse(readFileSync(lockPath, "utf8")).observed ?? {};
        const seen = new Set<string>([
            ...(observed.routes ?? []), ...(observed.templates ?? []), ...(observed.static ?? []),
        ]);
        for (const dependency of declared) {
            if (seen.has(dependency)) {
                problems.push({
                    check: "depends",
                    detail: `${page} declares ${dependency}, which its walkthrough already observes — delete it`,
                });
            }
        }
    }

    return problems;
};

export const main = (options: ValidationOptions = {}): number => {
    const problems = validate(options);
    if (!problems.length) {
        const pageCount = guidePages(options.guideDir ?? GUIDE_DIR).length;
        const referenced = new Set<string>();
        for (const page of guidePages(options.guideDir ?? GUIDE_DIR)) {
            const [area] = split(page);
            const markdown = readFileSync(join(options.guideDir ?? GUIDE_DIR, `${page}.md`), "utf8");
            for (const reference of imagesIn(markdown)) {
                referenced.add(resolve(options.guideDir ?? GUIDE_DIR, area, reference));
            }
        }
        console.log(`✅ ${pageCount} pages, ${referenced.size} images — no problems.`);
        return 0;
    }
    for (const problem of problems) console.error(`  [${problem.check}] ${problem.detail}`);
    console.error(`\n${problems.length} problem(s).`);
    return 1;
};

export const runCli = (options: ValidationOptions = {}): void => {
    try {
        process.exitCode = main(options);
    } catch (e) {
        console.error(e);
        process.exitCode = 1;
    }
};

const isDirectRun = process.argv[1] !== undefined &&
    import.meta.url === pathToFileURL(resolve(process.argv[1])).href;

if (isDirectRun) runCli();
