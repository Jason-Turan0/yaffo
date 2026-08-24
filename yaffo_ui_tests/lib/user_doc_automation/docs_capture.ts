/**
 * Run guide walkthroughs against a sandbox.
 *
 *   npm run isolatedEnvironment:start
 *   npm run docs:capture
 *   npm run docs:capture -- --promote library-basics/browsing-filtering
 *   npm run docs:capture -- --docker
 *
 * Captures land in .staging/ and are compared against what is committed. Nothing
 * touches docs/ unless --promote is passed, so a plain run can answer "is anything
 * stale?" without dirtying the tree.
 *
 * `--docker` moves the browser half into a container. The committed screenshots are
 * compared per-pixel, and macOS and Linux disagree on font metrics — which changes
 * line wrapping, which moves layout. Captured on a laptop and again in CI, the same
 * page differs every time and the comparison is noise. The container pins the
 * rendering stack so the two agree. Encoding and comparison stay on the host either
 * way: they need Pillow and NumPy from the project virtualenv.
 */
import {createHash} from "crypto";
import {scrubProcessEnv} from "./env";
import {DOCS_CAPTURE_IMAGE, dockerAvailable, runCaptureContainer, snapshotDockerEnv} from "./docker";
import {existsSync, readFileSync, writeFileSync} from "fs";
import {join, resolve} from "path";
import {pathToFileURL} from "url";
import {parse as parseYaml} from "yaml";
import {loadWalkthroughs} from "./load";
import {currentHead, dependencyHashes} from "./dependency_changes";
import {BASE_URL, CAPTURE_DIR, CONTENT_DIR, DOCS_DATA_DIR, GUIDE_DIR} from "./paths";
import {processResults, RAW_FILENAME, runWalkthroughs} from "./runner";
import type {RawResult, WalkthroughResult} from "./runner";

// Resolved from the working directory, like isolated_runner.ts and the rest of the
// harness: every entry point here is run from yaffo_ui_tests/.
// Authored spec, generated walkthroughs, and transient staging live in the
// content tree; this module is infrastructure and lives under lib/.
// The sandbox to drive. Deliberately its own variable: BASE_URL is overloaded in
// this repo — .env points it at the dev app on :5000 for other tooling, and a docs
// run against the wrong instance fails in confusing ways.

// Taken before the scrub below removes them: the docker CLI needs its own settings to
// find the daemon, and they must not end up in what a walkthrough runs with.
const DOCKER_ENV = snapshotDockerEnv();
const REPO = resolve(join(process.cwd(), ".."));

// Before anything else, and in particular before any walkthrough is imported:
// walkthroughs are model-generated code, and nothing they run should be able to
// read a provider key out of the ambient environment.
scrubProcessEnv({DOCS_BASE_URL: BASE_URL, YAFFO_DOCS_DATA_DIR: DOCS_DATA_DIR});

const declaredDependencies = (page: string): string[] => {
    const specPath = join(CONTENT_DIR, "spec.yaml");
    if (!existsSync(specPath)) return [];
    const spec = parseYaml(readFileSync(specPath, "utf8")) as {
        pages?: Record<string, {also_depends_on?: string[]}>;
    };
    return spec.pages?.[page]?.also_depends_on ?? [];
};

/**
 * The page's fingerprint: what its walkthrough touched, and what its shots looked
 * like when they were last written. Committed beside the walkthrough so a diff shows
 * which page's dependencies moved.
 */
const writeLockfile = (result: WalkthroughResult): void => {
    const [area, name] = [result.page.slice(0, result.page.indexOf("/")),
                          result.page.slice(result.page.indexOf("/") + 1)];
    const dir = join(CONTENT_DIR, area, name);
    if (!existsSync(dir)) return;
    const lock = {
        page: result.page,
        // Capture runs from the exact feature commit being documented. The generated
        // healing commit comes later, so no caller-supplied SHA is needed here.
        lastVerifiedSha: currentHead(),
        observed: result.observation,
        dependencyHashes: dependencyHashes(
            result.observation, declaredDependencies(result.page), REPO),
        shots: Object.fromEntries(result.shots.map((shot) => [
            shot.target,
            {
                width: shot.width,
                height: shot.height,
                // Detects a committed screenshot being replaced outside this pipeline;
                // the staleness comparison itself is per-pixel, not by hash.
                sha256: existsSync(join(GUIDE_DIR, shot.target))
                    ? createHash("sha256").update(readFileSync(join(GUIDE_DIR, shot.target))).digest("hex")
                    : null,
            },
        ])),
    };
    writeFileSync(join(dir, `${name}.lock.json`), JSON.stringify(lock, null, 4) + "\n");
};

export const main = async (
    args: string[] = process.argv.slice(2),
    dockerEnv: Record<string, string> = DOCKER_ENV
): Promise<number> => {
    const promote = args.includes("--promote");
    const useDocker = args.includes("--docker");
    const only = args.filter((a) => !a.startsWith("-"));

    let results: WalkthroughResult[];
    if (useDocker) {
        if (!dockerAvailable(dockerEnv)) {
            console.error("Docker is not reachable. Start Docker Desktop or Rancher Desktop, " +
                "then `npm run docker:build:docs-capture`. See README.md#docker.");
            return 1;
        }
        // The host never imports the walkthroughs in this mode: loading a module runs
        // its top-level code, and keeping model-generated code out of this process is
        // most of the reason the container exists.
        console.log(`Capturing in ${DOCS_CAPTURE_IMAGE} against ${BASE_URL}`);
        console.log(`  staging: ${CAPTURE_DIR}${promote ? "  (promoting changes)" : ""}\n`);
        const code = runCaptureContainer({
            repoDir: REPO,
            stagingDir: CAPTURE_DIR,
            baseUrl: BASE_URL,
            pages: only,
            dockerEnv,
        });
        if (code !== 0) return code;

        // The container wrote these through the shared staging mount, so the host is
        // reading the very same files rather than a copy.
        const rawPath = join(CAPTURE_DIR, RAW_FILENAME);
        if (!existsSync(rawPath)) {
            console.error(`The container produced no ${RAW_FILENAME}.`);
            return 1;
        }
        const raw = JSON.parse(readFileSync(rawPath, "utf8")) as {results: RawResult[]};
        results = processResults(raw.results, {
            guideDir: GUIDE_DIR, stagingDir: CAPTURE_DIR, promote,
        });
    } else {
        const walkthroughs = await loadWalkthroughs(CONTENT_DIR, only);
        if (!walkthroughs.length) {
            console.error(only.length ? `No walkthrough for: ${only.join(", ")}` : "No walkthroughs found");
            return 1;
        }
        console.log(`Running ${walkthroughs.length} walkthrough(s) against ${BASE_URL}`);
        console.log(`  staging: ${CAPTURE_DIR}${promote ? "  (promoting changes)" : ""}\n`);
        results = await runWalkthroughs(walkthroughs, {
            baseUrl: BASE_URL, guideDir: GUIDE_DIR, stagingDir: CAPTURE_DIR, promote,
        });
    }

    let failed = 0;
    for (const result of results) {
        if (promote) writeLockfile(result);
        console.log(`${result.page}`);
        for (const shot of result.shots) {
            const mark = {new: "+", changed: "~", unchanged: "="}[shot.status];
            const detail = shot.diff?.reason === "size"
                ? "  reframed"
                : shot.diff?.diffPixels
                    ? `  ${shot.diff.diffPixels} px differ`
                    : "";
            console.log(`  ${mark} ${shot.target}  ${shot.width}x${shot.height}${detail}`);
        }
        const {urls, static: statics, templates, routes, serverObserver} = result.observation;
        console.log(`  deps: ${routes.length} route(s), ${templates.length} template(s), ` +
            `${statics.length} static file(s), ${urls.length} url(s)`);
        if (serverObserver === "unavailable") {
            console.log("        (server observer unavailable - start the app with YAFFO_DOC_OBSERVER=1)");
        }
        if (result.error) {
            failed++;
            console.error(`  ! ${result.error}`);
        }
    }

    const changed = results.flatMap((r) => r.shots).filter((s) => s.status !== "unchanged");
    console.log(`\n${changed.length} shot(s) new or changed${promote ? " and promoted" : ""}.`);
    return failed ? 1 : 0;
};

const isDirectRun = process.argv[1] !== undefined &&
    import.meta.url === pathToFileURL(resolve(process.argv[1])).href;

export const runCli = async (args: string[] = process.argv.slice(2)): Promise<void> => {
    try {
        process.exitCode = await main(args);
    } catch (e) {
        console.error(e);
        process.exitCode = 1;
    }
};

if (isDirectRun) void runCli();
