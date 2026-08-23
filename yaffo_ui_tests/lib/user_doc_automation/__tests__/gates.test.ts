import {afterAll, afterEach, beforeEach, describe, expect, it, jest} from "@jest/globals";
import {existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync} from "fs";
import {tmpdir} from "os";
import {dirname, join} from "path";

const suiteRoot = mkdtempSync(join(tmpdir(), "yaffo-gates-"));
const REPO = join(suiteRoot, "repo");
const GUIDE_DIR = join(REPO, "docs", "guide");
const UI_TESTS = join(REPO, "yaffo_ui_tests");
const STAGING_DIR = join(UI_TESTS, ".doc-staging");
const BASE_URL = "http://app.test:5002";

const execFileSync = jest.fn<(
    command: string,
    args: readonly string[],
    options: Record<string, unknown>
) => string>();
const requiredShots = jest.fn<(markdown: string) => Array<{filename: string}>>();
const snapshotDockerEnv = jest.fn<(source?: NodeJS.ProcessEnv) => Record<string, string>>();

await jest.unstable_mockModule("child_process", () => ({execFileSync}));
await jest.unstable_mockModule("../generate", () => ({requiredShots}));
await jest.unstable_mockModule("../docker", () => ({snapshotDockerEnv}));
await jest.unstable_mockModule("../paths", () => ({
    BASE_URL,
    GUIDE_DIR,
    REPO,
    STAGING_DIR,
    splitPage: (page: string): [string, string] => [
        page.slice(0, page.indexOf("/")),
        page.slice(page.indexOf("/") + 1),
    ],
}));

const {
    capturesWhatThePageReferences,
    mkdocsStrict,
    revertPage,
    runGates,
    typecheck,
} = await import("../gates");

const savedEnv = {
    ANTHROPIC_API_KEY: process.env.ANTHROPIC_API_KEY,
    DOCKER_HOST: process.env.DOCKER_HOST,
};

const restoreEnv = (key: keyof typeof savedEnv): void => {
    const value = savedEnv[key];
    if (value === undefined) delete process.env[key];
    else process.env[key] = value;
};

beforeEach(() => {
    rmSync(REPO, {recursive: true, force: true});
    mkdirSync(GUIDE_DIR, {recursive: true});
    mkdirSync(UI_TESTS, {recursive: true});
    execFileSync.mockReset();
    requiredShots.mockReset();
    snapshotDockerEnv.mockReset();
    execFileSync.mockReturnValue("");
    requiredShots.mockReturnValue([]);
    snapshotDockerEnv.mockReturnValue({});
});

afterEach(() => {
    restoreEnv("ANTHROPIC_API_KEY");
    restoreEnv("DOCKER_HOST");
});

afterAll(() => {
    rmSync(suiteRoot, {recursive: true, force: true});
});

const page = "library/browsing";

const markdownPath = (): string => join(GUIDE_DIR, `${page}.md`);
const assetPath = (filename: string): string =>
    join(GUIDE_DIR, "library", "assets", "browsing", filename);

const write = (path: string, content = "content"): void => {
    mkdirSync(dirname(path), {recursive: true});
    writeFileSync(path, content, "utf8");
};

const processFailure = (stdout = "", stderr = ""): never => {
    throw {stdout, stderr};
};

describe("typecheck", () => {
    it("runs the cheapest gate in the UI-test workspace", () => {
        expect(typecheck()).toEqual([]);
        expect(execFileSync).toHaveBeenCalledWith(
            "npx",
            ["tsc", "--noEmit"],
            expect.objectContaining({cwd: UI_TESTS, encoding: "utf8", stdio: "pipe"})
        );
    });

    it("returns the tail of compiler output as a gate failure", () => {
        execFileSync.mockImplementation(() => processFailure(
            `old diagnostics\n${"x".repeat(900)}`, "\nTS2339: missing property"));
        const [error] = typecheck();
        expect(error).toMatch(/^does not typecheck:/);
        expect(error).toContain("TS2339: missing property");
        expect(error).not.toContain("old diagnostics");
    });

    it.each([
        [new Error("could not launch npx"), "could not launch npx"],
        [{}, "failed"],
    ])("falls back to a useful process error for %#", (failure, expected) => {
        execFileSync.mockImplementation(() => { throw failure; });
        expect(typecheck()[0]).toContain(expected);
    });
});

describe("mkdocsStrict", () => {
    it("builds the site strictly outside the repository tree", () => {
        expect(mkdocsStrict()).toEqual([]);
        expect(execFileSync).toHaveBeenCalledWith(
            join(REPO, "venv", "bin", "mkdocs"),
            ["build", "--strict", "--site-dir", "/tmp/docs-gate-check"],
            expect.objectContaining({cwd: REPO})
        );
    });

    it("returns a concise build failure", () => {
        execFileSync.mockImplementation(() => processFailure("", "missing image: shot.webp"));
        expect(mkdocsStrict()).toEqual([
            "mkdocs build --strict failed:\nmissing image: shot.webp",
        ]);
    });
});

describe("capturesWhatThePageReferences", () => {
    it("runs the walkthrough with promotion and a credential-free environment", () => {
        process.env.ANTHROPIC_API_KEY = "must-not-reach-generated-code";
        write(markdownPath(), "![Gallery](assets/browsing/gallery.webp)");
        requiredShots.mockReturnValue([{filename: "gallery.webp"}]);
        write(assetPath("gallery.webp"), "promoted capture");

        expect(capturesWhatThePageReferences(page)).toEqual([]);

        const [command, args, callOptions] = execFileSync.mock.calls[0];
        expect(command).toBe("npx");
        expect(args).toEqual([
            "tsx", "lib/user_doc_automation/docs_capture.ts", page, "--promote",
        ]);
        const captureOptions = callOptions as {
            cwd: string;
            env: NodeJS.ProcessEnv;
        };
        expect(captureOptions.cwd).toBe(UI_TESTS);
        expect(captureOptions.env.DOCS_BASE_URL).toBe(BASE_URL);
        expect(captureOptions.env.SKIP_DOTENV).toBe("1");
        expect(captureOptions.env.ANTHROPIC_API_KEY).toBeUndefined();
    });

    it("adds Docker mode and preserves only snapshotted daemon settings", () => {
        process.env.DOCKER_HOST = "unix:///ambient.sock";
        snapshotDockerEnv.mockReturnValue({DOCKER_HOST: "unix:///daemon.sock"});
        write(markdownPath(), "![Gallery](assets/browsing/gallery.webp)");
        requiredShots.mockReturnValue([{filename: "gallery.webp"}]);
        write(assetPath("gallery.webp"));

        expect(capturesWhatThePageReferences(page, {useDocker: true})).toEqual([]);

        expect(execFileSync.mock.calls[0][1]).toEqual([
            "tsx", "lib/user_doc_automation/docs_capture.ts", page, "--promote", "--docker",
        ]);
        expect(snapshotDockerEnv).toHaveBeenCalledWith(process.env);
        const env = (execFileSync.mock.calls[0][2] as {env: NodeJS.ProcessEnv}).env;
        expect(env.DOCKER_HOST).toBe("unix:///daemon.sock");
    });

    it("returns capture output without attempting reference validation", () => {
        execFileSync.mockImplementation(() => processFailure("capture stdout\n", "browser failed"));
        expect(capturesWhatThePageReferences(page)).toEqual([
            "capture failed:\ncapture stdout\nbrowser failed",
        ]);
        expect(requiredShots).not.toHaveBeenCalled();
    });

    it("requires the generated Markdown page to exist", () => {
        expect(capturesWhatThePageReferences(page)).toEqual([
            "no such page: library/browsing.md",
        ]);
    });

    it("requires at least one screenshot reference", () => {
        write(markdownPath(), "# Browsing\n\nNo image here.");
        expect(capturesWhatThePageReferences(page)).toEqual([
            "the page references no screenshots",
        ]);
        expect(requiredShots).toHaveBeenCalledWith("# Browsing\n\nNo image here.");
    });

    it("reports every referenced image not promoted by the walkthrough", () => {
        write(markdownPath(), "three shots");
        requiredShots.mockReturnValue([
            {filename: "present.webp"},
            {filename: "missing-one.webp"},
            {filename: "missing-two.webp"},
        ]);
        write(assetPath("present.webp"));

        expect(capturesWhatThePageReferences(page)).toEqual([
            "references missing-one.webp, which the walkthrough does not produce",
            "references missing-two.webp, which the walkthrough does not produce",
        ]);
    });
});

describe("revertPage", () => {
    it("restores tracked artifacts, removes new ones, and preserves memories", () => {
        const trackedMarkdown = join(REPO, "docs", "guide", "library", "browsing.md");
        const newWalkthrough = join(
            REPO, "yaffo_ui_tests", "user_doc_automation", "library", "browsing", "browsing.ts");
        const trackedAsset = assetPath("tracked.webp");
        const newAsset = assetPath("new.webp");
        const pageDir = join(
            REPO, "yaffo_ui_tests", "user_doc_automation", "library", "browsing");
        const trackedLock = join(pageDir, "browsing.lock.json");
        const newCatalog = join(pageDir, "browsing.json");
        const memory = join(pageDir, "memories", "keep.md");
        for (const path of [
            trackedMarkdown, newWalkthrough, trackedAsset, newAsset,
            trackedLock, newCatalog, memory,
        ]) write(path);
        const tracked = new Set([trackedMarkdown, trackedAsset, trackedLock]);
        execFileSync.mockImplementation((command, args) => {
            if (command === "git" && args[0] === "ls-files") {
                if (tracked.has(String(args[2]))) return "tracked\n";
                return processFailure("", "not tracked");
            }
            return "";
        });

        revertPage(page, [
            "docs/guide/library/browsing.md",
            "yaffo_ui_tests/user_doc_automation/library/browsing/browsing.ts",
            "does/not/exist.md",
        ]);

        expect(existsSync(trackedMarkdown)).toBe(true);
        expect(existsSync(trackedAsset)).toBe(true);
        expect(existsSync(trackedLock)).toBe(true);
        expect(existsSync(newWalkthrough)).toBe(false);
        expect(existsSync(newAsset)).toBe(false);
        expect(existsSync(newCatalog)).toBe(false);
        expect(existsSync(memory)).toBe(true);
        const checkouts = execFileSync.mock.calls
            .filter(([command, args]) => command === "git" && args[0] === "checkout")
            .map(([, args]) => args[2]);
        expect(checkouts).toEqual(expect.arrayContaining([
            trackedMarkdown, trackedAsset, trackedLock,
        ]));
        expect(readFileSync(memory, "utf8")).toBe("content");
    });

    it("does not require an assets directory to remove capture metadata", () => {
        const catalog = join(
            REPO, "yaffo_ui_tests", "user_doc_automation", "library", "browsing", "browsing.json");
        write(catalog);
        execFileSync.mockImplementation((_command, args) =>
            args[0] === "ls-files" ? processFailure("", "not tracked") : "");

        revertPage(page, []);

        expect(existsSync(assetPath("anything.webp"))).toBe(false);
        expect(existsSync(catalog)).toBe(false);
    });
});

describe("runGates", () => {
    const readyPage = (): void => {
        write(markdownPath(), "![Gallery](assets/browsing/gallery.webp)");
        requiredShots.mockReturnValue([{filename: "gallery.webp"}]);
        write(assetPath("gallery.webp"));
    };

    it("stops after typechecking fails", () => {
        execFileSync.mockImplementation(() => processFailure("", "TS1005"));
        expect(runGates(page)).toEqual(["does not typecheck:\nTS1005"]);
        expect(execFileSync).toHaveBeenCalledTimes(1);
    });

    it("stops after capture fails instead of running a redundant site build", () => {
        execFileSync
            .mockReturnValueOnce("")
            .mockImplementationOnce(() => processFailure("", "capture failed"));
        expect(runGates(page)).toEqual(["capture failed:\ncapture failed"]);
        expect(execFileSync).toHaveBeenCalledTimes(2);
    });

    it("runs typecheck, capture, and mkdocs in increasing cost order", () => {
        readyPage();
        expect(runGates(page, {useDocker: true})).toEqual([]);
        expect(execFileSync.mock.calls.map(([command, args]) =>
            command === "npx" ? args.slice(0, 2).join(" ") : "mkdocs build"
        )).toEqual(["tsc --noEmit", "tsx lib/user_doc_automation/docs_capture.ts", "mkdocs build"]);
    });

    it("returns the final strict-build failure after earlier gates pass", () => {
        readyPage();
        execFileSync
            .mockReturnValueOnce("")
            .mockReturnValueOnce("")
            .mockImplementationOnce(() => processFailure("", "strict warning"));
        expect(runGates(page)).toEqual([
            "mkdocs build --strict failed:\nstrict warning",
        ]);
    });
});
