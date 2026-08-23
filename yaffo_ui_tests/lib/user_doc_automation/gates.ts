import {execFileSync} from "child_process";
import {existsSync, readdirSync, readFileSync, rmSync} from "fs";
import {join} from "path";
import {captureEnv} from "./env";
import {snapshotDockerEnv} from "./docker";
import {requiredShots} from "./generate";
import {BASE_URL, GUIDE_DIR, REPO, STAGING_DIR, splitPage} from "./paths";

/**
 * The correctness gates, shared by generation and healing.
 *
 * These exist as one module because both turns produce the same two artifacts — a
 * guide page and the walkthrough that captures its screenshots — and so have to clear
 * the same bar. They had drifted: generation ran the walkthrough and checked what it
 * produced, healing only typechecked it. A heal that reframed a shot onto the wrong
 * element passed every gate it faced.
 *
 * They are correctness gates, not style gates: nothing here counts changed lines or
 * checks whether headings moved. That judgement belongs to the human reading the PR.
 */

const run = (command: string, args: string[], cwd: string, env?: NodeJS.ProcessEnv):
    {ok: boolean; output: string} => {
    try {
        return {ok: true, output: execFileSync(command, args,
            {cwd, encoding: "utf8", stdio: "pipe", ...(env ? {env} : {})})};
    } catch (e) {
        const err = e as {stdout?: string; stderr?: string; message?: string};
        return {ok: false,
                output: `${err.stdout ?? ""}${err.stderr ?? ""}` || err.message || "failed"};
    }
};

const UI_TESTS = join(REPO, "yaffo_ui_tests");

/** The walkthrough compiles. Cheapest gate, so it runs first. */
export const typecheck = (): string[] => {
    const result = run("npx", ["tsc", "--noEmit"], UI_TESTS);
    return result.ok ? [] : [`does not typecheck:\n${result.output.slice(-800)}`];
};

/** The site builds, which is what catches an image reference resolving nowhere. */
export const mkdocsStrict = (): string[] => {
    const result = run(join(REPO, "venv", "bin", "mkdocs"),
        ["build", "--strict", "--site-dir", "/tmp/docs-gate-check"], REPO);
    return result.ok ? [] : [`mkdocs build --strict failed:\n${result.output.slice(-800)}`];
};

/**
 * Run the generated walkthrough, promote what it captured, and confirm the page's
 * references are satisfied.
 *
 * The promote is not optional and the ordering is not arbitrary. `mkdocs --strict`
 * treats a missing image as a fatal warning:
 *
 *   WARNING - Doc file 'guide/.../page.md' contains a link '.../new-shot.webp',
 *   but the target ... is not found among documentation files.
 *   Aborted with 1 warnings in strict mode!
 *
 * So a page that references a *new* screenshot cannot build until that screenshot is in
 * `docs/guide/`. Capturing to staging and then asking mkdocs to find it in the guide
 * fails every time — and passes only for pages whose images already existed, which is
 * exactly how this went unnoticed.
 *
 * Promoting during verification means a rejected answer has left images behind, so the
 * caller must revert them along with the files it wrote. `revertPage` does that.
 */
export const capturesWhatThePageReferences = (
    page: string,
    options: {useDocker?: boolean} = {}
): string[] => {
    // --promote: the images have to be in the guide for mkdocs to resolve them. This
    // also writes the page's lockfile, which is the other half of a finished capture.
    const args = ["tsx", "lib/user_doc_automation/run.ts", page, "--promote"];
    if (options.useDocker) args.push("--docker");

    // An allowlist, not the ambient environment: this child executes the walkthrough
    // the model just wrote, and dotenv has already loaded every provider key into this
    // process. The docker CLI's own settings ride along only when containerizing, and
    // only into this launcher — never into the container, so never into the walkthrough.
    const env = {
        ...captureEnv(process.env, {DOCS_BASE_URL: BASE_URL}),
        ...(options.useDocker ? snapshotDockerEnv(process.env) : {}),
    };

    const captured = run("npx", args, UI_TESTS, env);
    if (!captured.ok) return [`capture failed:\n${captured.output.slice(-800)}`];

    // Read the markdown as it now stands: the agent has already written it, and it is
    // the new references that have to be satisfied.
    const markdownPath = join(GUIDE_DIR, `${page}.md`);
    if (!existsSync(markdownPath)) return [`no such page: ${page}.md`];
    const wanted = requiredShots(readFileSync(markdownPath, "utf8"));
    if (!wanted.length) return ["the page references no screenshots"];

    // Checked in the guide, not in staging — that is where mkdocs will look for them.
    const [area, name] = splitPage(page);
    return wanted
        .filter((shot) => !existsSync(join(GUIDE_DIR, area, "assets", name, shot.filename)))
        .map((shot) =>
            `references ${shot.filename}, which the walkthrough does not produce`);
};

/**
 * Undo a rejected answer, images included.
 *
 * Verification promotes, so the guide holds screenshots from an answer that may be
 * about to be thrown away. Tracked files are restored rather than deleted, so a page
 * that already existed comes back exactly as it was; files the run created are removed.
 */
export const revertPage = (page: string, written: string[]): void => {
    const [area, name] = splitPage(page);
    const assets = join(GUIDE_DIR, area, "assets", name);
    const pageDir = join(REPO, "yaffo_ui_tests", "user_doc_automation", area, name);
    const paths = [
        ...written.map((file) => join(REPO, file)),
        ...(existsSync(assets)
            ? readdirSync(assets).map((file) => join(assets, file))
            : []),
        // Neither of these is in `written`: the lockfile is a side effect of the
        // verification capture's --promote, and the catalog is written on success. A
        // failed attempt that follows a promoted one would otherwise leave both
        // describing an answer that no longer exists.
        join(pageDir, `${name}.lock.json`),
        join(pageDir, `${name}.json`),
    ];
    // `memories/` is deliberately absent: notes an agent left are the one thing meant
    // to survive a failed attempt, so the next one does not rediscover the same dead
    // ends.
    for (const path of paths) {
        if (!existsSync(path)) continue;
        const tracked = run("git", ["ls-files", "--error-unmatch", path], REPO).ok;
        if (tracked) run("git", ["checkout", "--", path], REPO);
        else rmSync(path, {force: true});
    }
};

/** Every gate, in increasing order of cost. Empty means the answer is acceptable. */
export const runGates = (
    page: string,
    options: {useDocker?: boolean} = {}
): string[] => {
    const types = typecheck();
    if (types.length) return types;              // Nothing downstream can run.
    // Capture *before* mkdocs, and promote while doing it: mkdocs cannot resolve an
    // image that is not in the guide yet, so the order here is a requirement, not a
    // preference.
    const captured = capturesWhatThePageReferences(page, options);
    if (captured.length) return captured;        // mkdocs would only repeat the cause.
    return mkdocsStrict();
};
