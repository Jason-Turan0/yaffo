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
 * Run `body` against a repo-relative dependency that exists but has no diff.
 * Untracked rather than tracked, so the assertion does not depend on whether the
 * developer running the suite happens to have edited that file.
 */
const withCleanDependency = (body: (dependency: string) => void): void => {
    const repo = resolve(process.cwd(), "..");
    const path = join(repo, `.evidence-unit-${process.pid}-${Date.now()}.ts`);
    writeFileSync(path, "export const observed = true;\n", "utf8");
    try {
        body(relative(repo, path));
    } finally {
        rmSync(path, {force: true});
    }
};

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
        // An untracked file exists but has no `git diff HEAD` output. This exercises
        // the scoped git path without coupling the test to the developer's dirty tree.
        const repo = resolve(process.cwd(), "..");
        const dependency = join(repo, `.evidence-unit-${process.pid}-${Date.now()}.ts`);
        writeFileSync(dependency, "export const observed = true;\n", "utf8");
        try {
            const observed = result({
                observation: {
                    ...result().observation,
                    static: [relative(repo, dependency), "yaffo/does-not-exist.ts"],
                },
            });
            expect(buildEvidence(observed, shot(), options()).codeDiff)
                .toContain("no changes to this page's dependencies since HEAD");
        } finally {
            rmSync(dependency, {force: true});
        }
    });

    it("says so when it has no watermark and is therefore blind to committed work", () => {
        // The CI failure this guards: a clean checkout makes `git diff HEAD` empty no
        // matter how much the app moved, and triage read that silence as "nothing
        // changed" and classified real UI work as renderer noise.
        withCleanDependency((dependency) => {
            const observed = result({
                observation: {...result().observation, static: [dependency]},
            });
            expect(buildEvidence(observed, shot(), options()).codeDiff)
                .toContain("absence of a diff is not evidence that the app is unchanged");
        });
    });

    it("spans the commits since the page's baseline, not just the working tree", () => {
        const repo = resolve(process.cwd(), "..");
        const head = execFileSync("git", ["rev-parse", "HEAD"], {cwd: repo, encoding: "utf8"}).trim();
        const previous = execFileSync("git", ["rev-parse", "HEAD~1"], {cwd: repo, encoding: "utf8"}).trim();
        const changed = execFileSync(
            "git", ["diff", "--name-only", previous, head], {cwd: repo, encoding: "utf8"})
            .split("\n").filter(Boolean);
        if (!changed.length) return; // An empty commit leaves nothing to assert.

        const observed = result({
            observation: {...result().observation, static: changed},
        });
        const codeDiff = buildEvidence(observed, shot(), options({lastVerifiedSha: previous})).codeDiff;

        expect(codeDiff).toContain(`since ${previous.slice(0, 12)}`);
        expect(codeDiff).toContain(changed[0]);
    });

    it("falls back to HEAD when the recorded baseline commit is not in this checkout", () => {
        // A rebase or squash can leave a lockfile pointing at a commit that no longer
        // exists. That must degrade to the old window, not crash the run.
        withCleanDependency((dependency) => {
            const observed = result({
                observation: {...result().observation, static: [dependency]},
            });
            expect(buildEvidence(observed, shot(), options({
                lastVerifiedSha: "0".repeat(40),
            })).codeDiff).toContain("since HEAD");
        });
    });
});
