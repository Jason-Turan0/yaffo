import {afterEach, beforeEach, describe, expect, it} from "@jest/globals";
import {execFileSync} from "child_process";
import {mkdirSync, mkdtempSync, rmSync, writeFileSync} from "fs";
import {tmpdir} from "os";
import {dirname, join, relative, resolve} from "path";
import {buildEvidence} from "../evidence";
import type {EvidenceOptions} from "../evidence";
import type {ShotResult, WalkthroughResult} from "../runner";

let testDir: string;
let guideDir: string;

beforeEach(() => {
    testDir = mkdtempSync(join(tmpdir(), "yaffo-evidence-"));
    guideDir = join(testDir, "guide");
    mkdirSync(guideDir, {recursive: true});
});

afterEach(() => {
    rmSync(testDir, {recursive: true, force: true});
});

const result = (over: Partial<WalkthroughResult> = {}): WalkthroughResult => ({
    page: "library/browsing",
    shots: [],
    observation: {
        page: "library/browsing",
        urls: ["/"],
        static: [],
        templates: [],
        routes: [],
        serverObserver: "recorded",
    },
    ...over,
});

const shot = (over: Partial<ShotResult> = {}): ShotResult => ({
    target: "library/assets/browsing/gallery.webp",
    staged: join(testDir, "gallery.webp"),
    status: "changed",
    width: 1400,
    height: 800,
    ignore: [],
    diff: {
        status: "changed",
        diffPixels: 720,
        ratio: 0.00064286,
        box: {x: 100, y: 50, width: 80, height: 40},
        diffImage: join(testDir, "gallery.diff.png"),
    },
    ...over,
});

const options = (over: Partial<EvidenceOptions> = {}): EvidenceOptions => ({
    guideDir,
    stagingDir: testDir,
    walkthroughSource: "export default defineWalkthrough({page: 'library/browsing'});",
    walkthroughPath: "/repo/user_doc_automation/library/browsing/browsing.ts",
    covers: "Browse and filter the library",
    stringChanges: [{was: "Apply Filters", now: "Apply", source: "en.json"}],
    ...over,
});

/**
 * A throwaway git repository with one committed dependency.
 *
 * Hermetic on purpose: CI checks out with the default `fetch-depth: 1`, so the real
 * checkout has no history to diff against and no second commit to reach for. Owning
 * the repository also means these assertions do not shift with whatever the last
 * real commit happened to touch.
 */
const withRepo = (
    body: (repo: string, commit: (message: string) => string) => void
): void => {
    const repo = mkdtempSync(join(tmpdir(), "yaffo-evidence-repo-"));
    const git = (...args: string[]): string =>
        execFileSync("git", args, {cwd: repo, encoding: "utf8"});
    try {
        git("init", "--quiet", "--initial-branch", "main");
        git("config", "user.email", "tests@example.com");
        git("config", "user.name", "tests");
        mkdirSync(join(repo, "yaffo"), {recursive: true});
        const commit = (message: string): string => {
            git("add", "--all");
            git("commit", "--quiet", "--allow-empty", "--message", message);
            return git("rev-parse", "HEAD").trim();
        };
        writeFileSync(join(repo, DEPENDENCY), "a { color: red; }\n", "utf8");
        body(repo, commit);
    } finally {
        rmSync(repo, {recursive: true, force: true});
    }
};

const DEPENDENCY = "yaffo/app.css";

const observing = (...paths: string[]): WalkthroughResult =>
    result({observation: {...result().observation, static: paths}});

const writeMarkdown = (text: string): string => {
    const path = join(guideDir, "library", "browsing.md");
    mkdirSync(dirname(path), {recursive: true});
    writeFileSync(path, text, "utf8");
    return path;
};

describe("buildEvidence", () => {
    it("assembles the page, capture, walkthrough, and detector evidence", () => {
        const markdownPath = writeMarkdown("# Browsing\n\nClick **Apply Filters**.");
        const changed = shot();
        const evidence = buildEvidence(result(), changed, options());

        expect(evidence).toMatchObject({
            page: "library/browsing",
            target: "library/assets/browsing/gallery.webp",
            baselinePath: join(guideDir, changed.target),
            candidatePath: changed.staged,
            overlayPath: changed.diff?.diffImage,
            markdown: "# Browsing\n\nClick **Apply Filters**.",
            markdownPath,
            walkthroughPath: "/repo/user_doc_automation/library/browsing/browsing.ts",
            covers: "Browse and filter the library",
            walkthroughSource: expect.stringContaining("defineWalkthrough"),
            stringChanges: [{was: "Apply Filters", now: "Apply", source: "en.json"}],
        });
        expect(evidence.diffSummary).toBe(
            "720 pixels differ (0.0643% of the image), bounded by 80x40 at (100, 50). " +
            "Shot is 1400x800."
        );
        expect(evidence.codeDiff).toMatch(/no observed dependencies/);
    });

    it("describes a new shot without pretending a comparison occurred", () => {
        const evidence = buildEvidence(result(), shot({status: "new", diff: undefined}), options());
        expect(evidence.diffSummary).toBe("No committed baseline: this shot is new.");
        expect(evidence.overlayPath).toBeUndefined();
    });

    it("describes the aligned pixel comparison for a reframed shot", () => {
        const evidence = buildEvidence(result(), shot({
            width: 900,
            height: 600,
            diff: {
                status: "changed",
                reason: "size",
                diffPixels: 1800,
                baselineSize: [1800, 1198],
                candidateSize: [1800, 1200],
            },
        }), options());
        expect(evidence.diffSummary).toBe(
            "Reframed: 1800x1198 became 1800x1200; 1800 pixels differ after top-left alignment."
        );
    });

    it("handles a missing comparison and missing Markdown page", () => {
        const evidence = buildEvidence(result(), shot({status: "unchanged", diff: undefined}), options({
            covers: undefined,
            stringChanges: undefined,
        }));
        expect(evidence.diffSummary).toBe("No comparison was recorded.");
        expect(evidence.markdown).toBe("(page not found)");
        expect(evidence.covers).toBeUndefined();
        expect(evidence.stringChanges).toEqual([]);
    });

    it("describes a pixel difference that has no bounding box", () => {
        const evidence = buildEvidence(result(), shot({
            diff: {status: "changed", diffPixels: 101, ratio: undefined, box: null},
        }), options());
        expect(evidence.diffSummary).toContain(
            "101 pixels differ (0.0000% of the image), with no bounding box"
        );
    });

    it("limits dependency evidence to existing observed files", () => {
        withRepo((repo, commit) => {
            commit("baseline");
            const observed = observing(DEPENDENCY, "yaffo/does-not-exist.ts");
            expect(buildEvidence(observed, shot(), options({repoDir: repo})).codeDiff)
                .toContain("no changes to this page's dependencies since HEAD");
        });
    });

    it("reports no observed dependencies when the page records none", () => {
        withRepo((repo) => {
            expect(buildEvidence(result(), shot(), options({repoDir: repo})).codeDiff)
                .toContain("no observed dependencies recorded");
        });
    });

    it("spans the commits since the page's baseline, not just the working tree", () => {
        withRepo((repo, commit) => {
            const baseline = commit("baseline");
            writeFileSync(join(repo, DEPENDENCY), "a { color: blue; }\n", "utf8");
            commit("responsive work");

            const observed = observing(DEPENDENCY);
            const codeDiff = buildEvidence(observed, shot(), options({
                repoDir: repo, lastVerifiedSha: baseline,
            })).codeDiff;

            expect(codeDiff).toContain(`since ${baseline.slice(0, 12)}`);
            expect(codeDiff).toContain(DEPENDENCY);
            expect(codeDiff).toContain("+a { color: blue; }");
        });
    });

    it("is blind to committed work without a watermark, and says so", () => {
        // The exact CI failure this fix exists for: the change is committed, the tree
        // is clean, and a HEAD-relative diff therefore reports nothing — which triage
        // read as "the product did not change" and filed as renderer noise.
        withRepo((repo, commit) => {
            commit("baseline");
            writeFileSync(join(repo, DEPENDENCY), "a { color: blue; }\n", "utf8");
            commit("responsive work");

            const codeDiff = buildEvidence(
                observing(DEPENDENCY), shot(), options({repoDir: repo})).codeDiff;

            expect(codeDiff).not.toContain("+a { color: blue; }");
            expect(codeDiff).toContain("absence of a diff is not evidence that the app is unchanged");
        });
    });

    it("falls back to HEAD when the recorded baseline commit is not in this checkout", () => {
        // A rebase, a squash, or a shallow clone can leave a lockfile pointing at a
        // commit this checkout does not have. That must degrade, not crash.
        withRepo((repo, commit) => {
            commit("baseline");
            expect(buildEvidence(observing(DEPENDENCY), shot(), options({
                repoDir: repo, lastVerifiedSha: "0".repeat(40),
            })).codeDiff).toContain("since HEAD");
        });
    });
});
