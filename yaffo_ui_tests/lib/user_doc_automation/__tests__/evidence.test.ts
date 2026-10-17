import {afterEach, beforeEach, describe, expect, it} from "@jest/globals";
import {execFileSync} from "child_process";
import {mkdirSync, mkdtempSync, rmSync, writeFileSync} from "fs";
import {tmpdir} from "os";
import {dirname, join} from "path";
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

    it("describes a row-aligned comparison band by band", () => {
        const evidence = buildEvidence(result(), shot({
            diff: {
                status: "changed",
                change: "content-added",
                diffPixels: 200448,
                residualPixels: 0,
                reason: "size",
                baselineSize: [2784, 4206],
                candidateSize: [2784, 4278],
                hunks: [
                    {op: "equal", baseline: [0, 4206], candidate: [0, 4206], offset: 0},
                    {op: "insert", baseline: [4206, 4206], candidate: [4206, 4278], added: "content"},
                ],
            },
        }), options());
        expect(evidence.diffSummary).toBe(
            "Rows were aligned before comparing, so content that only moved is not counted as changed. " +
            "Result: content-added — every row of the committed shot is still present and unchanged; " +
            "the new shot shows more. The shot went from 2784x4206 to 2784x4278.\n" +
            "- inserted: 72 row(s) at y=4206–4277 of the new shot (content)\n" +
            "0 pixel(s) differ across the matched rows."
        );
    });

    it("describes moved and partly changed matched runs", () => {
        const evidence = buildEvidence(result(), shot({
            diff: {
                status: "changed",
                change: "changed",
                diffPixels: 263,
                residualPixels: 263,
                hunks: [
                    {op: "equal", baseline: [0, 200], candidate: [0, 200], offset: 0},
                    {op: "delete", baseline: [200, 272], candidate: [200, 200], removed: "content"},
                    {op: "equal", baseline: [272, 900], candidate: [200, 828], offset: -72, residualPixels: 263},
                ],
            },
        }), options());
        expect(evidence.diffSummary).toContain("Result: changed — content differs.");
        expect(evidence.diffSummary).toContain("- removed: 72 row(s) at y=200–271 of the committed shot (content)");
        expect(evidence.diffSummary).toContain(
            "- matched: 628 row(s) at y=272–899 of the committed shot, moved up 72 px, 263 px differ within it");
        expect(evidence.diffSummary).not.toContain("y=0–199");
    });

    it("describes bands found by aligning a strip along its columns", () => {
        const evidence = buildEvidence(result(), shot({
            diff: {
                status: "changed",
                change: "layout-only",
                diffPixels: 18800,
                residualPixels: 0,
                hunks: [{op: "equal", baseline: [0, 470], candidate: [0, 470], offset: 0}],
                blocks: [
                    {axis: "columns", op: "insert", baseline: [363, 363], candidate: [363, 403],
                     rows: [0, 470], baselineRows: [0, 470], added: "blank"},
                    {axis: "columns", op: "equal", baseline: [363, 563], candidate: [403, 603], offset: 40,
                     rows: [0, 470], baselineRows: [0, 470]},
                    {axis: "rows", op: "insert", baseline: [36, 36], candidate: [36, 50],
                     columns: [956, 1174], baselineColumns: [956, 1174], added: "moved", moved: true},
                ],
            },
        }), options());
        expect(evidence.diffSummary).toBe(
            "Rows were aligned before comparing, and strips that still differed were aligned along " +
            "their columns too, so content that only moved is not counted as changed. " +
            "Result: layout-only — the content is identical; only blank space was added or removed.\n" +
            "- inserted: 40 column(s) at x=363–402 of the new shot (within rows 0–469) (blank space)\n" +
            "- moved: 200 column(s) at x=363–562 of the committed shot, right 40 px (within rows 0–469)\n" +
            "- inserted: 14 row(s) at y=36–49 of the new shot (within columns 956–1173) " +
            "(moved, same pixels as an edit elsewhere)\n" +
            "0 pixel(s) differ across the matched rows."
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
