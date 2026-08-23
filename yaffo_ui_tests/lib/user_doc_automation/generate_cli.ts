/**
 * Write the walkthrough for a guide page that has none.
 *
 *   npm run docs:generate -- library-basics/photo-details
 *   npm run docs:generate                 # every page still missing one
 *
 * The page's own image references are the specification. The generated walkthrough is
 * only accepted if it typechecks and then actually produces every shot the page
 * references — a walkthrough that compiles but captures the wrong thing is worse than
 * none, because the next capture run would promote it.
 */
import "dotenv/config";
import {execFileSync, spawnSync} from "child_process";
import {existsSync, mkdirSync, readFileSync, readdirSync, rmSync} from "fs";
import {join, resolve} from "path";
import {parse as parseYaml} from "yaml";
import {createFilesystemClient} from "@lib/tool_providers/mcp_filesystem_client";
import {createPlaywrightClient} from "@lib/tool_providers/mcp_playwright_client";
import {localFilesystemMemoryToolFactory} from "@lib/tool_providers/local_filesystem_memory_tool";
import type {ToolProvider} from "@lib/tool_providers/toolprovider.types";
import type {ModelAlias} from "@lib/model_clients/model_client.interface";
import {YAFFO_APP_ROOT} from "@lib/types";
import {captureEnv} from "./env";
import {snapshotDockerEnv} from "./docker";
import {BASE_URL, CONTENT_DIR, GUIDE_DIR, REPO, STAGING_DIR} from "./paths";
import {verifyBrowserTool} from "./preflight";
import {describeSandboxFacts, gatherSandboxFacts} from "./sandbox_facts";
import {generateWalkthrough, requiredShots} from "./generate";

// The sandbox to drive. Deliberately its own variable: BASE_URL is overloaded in
// this repo — .env points it at the dev app on :5000 for other tooling, and a docs
// run against the wrong instance fails in confusing ways.

const split = (page: string): [string, string] =>
    [page.slice(0, page.indexOf("/")), page.slice(page.indexOf("/") + 1)];

const pagesNeedingOne = (spec: Record<string, {walkthrough?: boolean}>): string[] =>
    Object.entries(spec)
        .filter(([page, entry]) => {
            if (entry.walkthrough === false) return false;   // no surface, by design
            const [area, name] = split(page);
            return !existsSync(join(CONTENT_DIR, area, name, `${name}.ts`));
        })
        .map(([page]) => page);

/**
 * Run the generated walkthrough and confirm it produced what the page asks for.
 *
 * This is the gate that matters. Typechecking only proves the file compiles; capturing
 * proves it reaches the right view. A walkthrough that compiles but frames the wrong
 * element would otherwise be promoted by the next run.
 */
/** Run the page's walkthrough. Without `promote` nothing under docs/ is touched. */
const capture = (
    page: string,
    promote: boolean,
    useDocker = false
): {ok: boolean; output: string} => {
    const args = ["tsx", "lib/user_doc_automation/run.ts", page];
    if (promote) args.push("--promote");
    if (useDocker) args.push("--docker");
    try {
        return {ok: true, output: execFileSync("npx", args, {
            cwd: process.cwd(),
            stdio: "pipe",
            encoding: "utf8",
            // An allowlist, not the ambient environment: this child executes the
            // walkthrough the model just wrote, and dotenv has already loaded every
            // provider key into this process.
            //
            // The docker CLI's own settings ride alongside when containerizing, since
            // the allowlist deliberately omits them — but only into this child, which
            // is the launcher. They never reach the container, and so never reach the
            // walkthrough.
            env: {
                ...captureEnv(process.env, {DOCS_BASE_URL: BASE_URL}),
                ...(useDocker ? snapshotDockerEnv(process.env) : {}),
            },
        })};
    } catch (e) {
        const err = e as {stdout?: string; stderr?: string};
        return {ok: false, output: (err.stdout ?? "") + (err.stderr ?? "")};
    }
};

const verify = (page: string, useDocker = false): string[] => {
    const failures: string[] = [];
    try {
        execFileSync("npx", ["tsc", "--noEmit"], {cwd: process.cwd(), stdio: "pipe"});
    } catch (e) {
        const err = e as {stdout?: string};
        return [`does not typecheck:\n${(err.stdout ?? "").slice(-800)}`];
    }

    const captured = capture(page, false, useDocker);
    if (!captured.ok) return [`capture failed:\n${captured.output.slice(-800)}`];

    // Read the markdown as it now stands, not as it was: the agent may have rewritten
    // it, and it is the new references that have to be satisfied.
    const [area, name] = split(page);
    const markdown = readFileSync(join(GUIDE_DIR, `${page}.md`), "utf8");
    const wanted = requiredShots(markdown);
    if (!wanted.length) failures.push("the page references no screenshots");

    for (const shot of wanted) {
        const staged = join(STAGING_DIR, area, "assets", name, shot.filename);
        if (!existsSync(staged)) failures.push(`references ${shot.filename}, which the walkthrough does not produce`);
    }

    // The page has to build. This is what catches a reference that resolves nowhere,
    // including one pointing at the wrong relative path.
    try {
        execFileSync(join(REPO, "venv", "bin", "mkdocs"),
            ["build", "--strict", "--site-dir", "/tmp/docs-generate-check"],
            {cwd: REPO, stdio: "pipe"});
    } catch (e) {
        const err = e as {stdout?: string; stderr?: string};
        failures.push(`mkdocs build --strict failed:\n${((err.stdout ?? "") + (err.stderr ?? "")).slice(-600)}`);
    }

    return failures;
};

const main = async (): Promise<void> => {
    const args = process.argv.slice(2);
    const modelArg = args.indexOf("--model");
    const model = modelArg !== -1 ? (args[modelArg + 1] as ModelAlias) : undefined;
    const named = args.filter((a) => !a.startsWith("-") && a !== args[modelArg + 1]);
    // Containerize the capture this run shells out to, so a walkthrough is verified
    // and promoted from the same renderer CI will use.
    const useDocker = args.includes("--docker");

    const spec = parseYaml(readFileSync(join(CONTENT_DIR, "spec.yaml"), "utf8"));
    const targets = named.length ? named : pagesNeedingOne(spec.pages);
    if (!targets.length) {
        console.log("Every page already has a walkthrough.");
        return;
    }

    const runLogDir = join(STAGING_DIR, "generate-logs");
    mkdirSync(runLogDir, {recursive: true});

    // The app source is readable too: selectors and routes are what the agent needs to
    // work out how to reach a view.
    const providers: ToolProvider[] = [
        await createFilesystemClient([YAFFO_APP_ROOT, GUIDE_DIR, CONTENT_DIR], {readonly: false}),
        await createPlaywrightClient({
            headless: true, baseUrl: BASE_URL, browser: "chromium",
            artifacts: {outputDir: runLogDir, saveVideo: false, saveSession: false},
        }),
    ];

    // Fail here rather than letting the agent discover a dead browser mid-run.
    await verifyBrowserTool(providers, BASE_URL);

    // Runtime state the agent would otherwise try to derive from source, and cannot.
    const facts = describeSandboxFacts(await gatherSandboxFacts(BASE_URL));

    let failed = 0;
    try {
        for (const page of targets) {
            console.log(`\n📝 ${page}`);
            const [area, name] = split(page);
            const memoriesDir = join(CONTENT_DIR, area, name, "memories");
            const memory = localFilesystemMemoryToolFactory(memoriesDir);

            const result = await generateWalkthrough(page, {
                model, runLogDir, baseUrl: BASE_URL, guideDir: GUIDE_DIR, contentDir: CONTENT_DIR,
                sandboxFacts: facts,
                toolProviders: [...providers, memory],
                covers: spec.pages?.[page]?.covers,
                hasMemories: existsSync(memoriesDir) && readdirSync(memoriesDir).length > 0,
            });

            for (const error of result.errors) console.error(`   ‼️  ${error}`);
            if (!result.written.length) {
                failed++;
                continue;
            }
            for (const file of result.written) console.log(`   wrote ${file}`);

            const failures = verify(page, useDocker);
            if (failures.length) {
                failed++;
                for (const failure of failures) console.error(`   ‼️  ${failure}`);
                // Rejected output is not a starting point: leaving a walkthrough that
                // captures the wrong thing would let the next run promote it, and a
                // half-written page would ship. Tracked files are restored rather than
                // deleted, so an existing page comes back intact.
                for (const file of result.written) {
                    const tracked = spawnSync("git", ["ls-files", "--error-unmatch", file],
                        {cwd: REPO}).status === 0;
                    if (tracked) spawnSync("git", ["checkout", "--", file], {cwd: REPO});
                    else rmSync(resolve(REPO, file), {force: true});
                }
                console.error("   → rolled back");
            } else {
                // Generation is not finished until the guide holds what the walkthrough
                // produces. Verification captured to staging only, so the guide would
                // otherwise keep whatever screenshot was there before — and the next
                // capture run would immediately report it as changed. This also writes
                // the page's lockfile.
                const promoted = capture(page, true, useDocker);
                if (promoted.ok) {
                    console.log(`   ✅ captures every shot ${page}.md references, and promoted them`);
                } else {
                    failed++;
                    console.error(`   ‼️  verified but could not promote:\n${promoted.output.slice(-400)}`);
                }
            }
        }
    } finally {
        await Promise.all(providers.map((provider) => provider.disconnect()));
    }

    if (failed) process.exit(1);
};

main().catch((e) => {
    console.error(e);
    process.exit(1);
});
