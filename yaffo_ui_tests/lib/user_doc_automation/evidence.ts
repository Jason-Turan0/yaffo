import {execFileSync} from "child_process";
import {existsSync, readFileSync} from "fs";
import {join, resolve} from "path";
import type {BandKind, BlockHunk, ChangeKind, CompareResult, RowHunk} from "./compare";
import type {ShotResult, WalkthroughResult} from "./runner";
import type {StringChange} from "./strings";

/**
 * Everything triage needs to classify one changed shot, and nothing else.
 *
 * The bounding matters: handing a model the whole repository diff is both expensive
 * and imprecise. Scoping to the page's observed dependencies is what the lockfile
 * exists to make possible.
 */
export interface Evidence {
    page: string;
    /** Present when capture threw before the walkthrough completed. */
    walkthroughError?: string;
    /** Guide-relative path of the shot, e.g. library-basics/assets/…/gallery-home.webp */
    target: string;
    baselinePath: string;
    candidatePath: string;
    /** Magenta-on-dimmed overlay, present whenever the shot changed. */
    overlayPath?: string;
    diffSummary: string;
    /** The page's markdown, prose and image captions together. */
    markdown: string;
    /** Absolute path to the page, so the agent is never left hunting for it. */
    markdownPath: string;
    /** Absolute path to the walkthrough that captured this shot. */
    walkthroughPath: string;
    /** The page's obligation, from spec.yaml. */
    covers?: string;
    walkthroughSource: string;
    /** Diff of the page's observed dependencies only. */
    codeDiff: string;
    /**
     * Strings this page quotes that the app has stopped saying, from Detector B.
     * Structured rather than a raw diff: the model is told which quoted control
     * changed and, where the catalogue allows, what replaced it.
     */
    stringChanges: StringChange[];
}

const REPO = resolve(join(process.cwd(), ".."));

const git = (args: string[], repoDir: string): string => {
    try {
        return execFileSync("git", args, {cwd: repoDir, encoding: "utf8", maxBuffer: 8 * 1024 * 1024});
    } catch {
        return "";
    }
};

const truncate = (text: string, limit: number): string =>
    text.length <= limit ? text : `${text.slice(0, limit)}\n… (${text.length - limit} more characters)`;

/** How much raw patch triage gets. The stat above it is never truncated. */
const PATCH_BUDGET = 12_000;

/**
 * A watermark this checkout can actually resolve. A rebase or squash can orphan one,
 * and a shallow clone may simply not have fetched it.
 */
const resolvableCommit = (sha: string | null | undefined, repoDir: string): string | null =>
    sha && git(["rev-parse", "--verify", "--quiet", `${sha}^{commit}`], repoDir).trim() ? sha : null;

/**
 * Changes to the files this page depends on, since the page was last verified.
 *
 * The window is the lockfile watermark, not HEAD. A CI checkout is clean, so
 * `git diff HEAD` is empty there no matter how much the UI moved — which told triage
 * the product had not changed and left it calling real work renderer noise. From
 * `lastVerifiedSha` to the working tree spans the commits that landed since this
 * page's baseline plus anything uncommitted, the same window `changedDependencies`
 * already uses for the prose path.
 *
 * The stat leads and is never truncated. A page's dependency patch runs to tens of
 * thousands of characters, and a budget applied to the patch alone would silently
 * drop whole files past the cut — reintroducing "nothing changed" for everything
 * sorted after it.
 */
const dependencyDiff = (
    observation: WalkthroughResult["observation"],
    lastVerifiedSha: string | null | undefined,
    repoDir: string
): string => {
    const deps = [...observation.routes, ...observation.templates, ...observation.static]
        .filter((path) => existsSync(join(repoDir, path)));
    if (!deps.length) return "(no observed dependencies recorded — is the server observer running?)";

    const since = resolvableCommit(lastVerifiedSha, repoDir);
    const range = since ?? "HEAD";
    const stat = git(["diff", "--stat", range, "--", ...deps], repoDir).trim();
    if (!stat) {
        return since
            ? `(no changes to this page's dependencies since ${since.slice(0, 12)}, ` +
              "when this page was last verified)"
            : "(no changes to this page's dependencies since HEAD. No verified baseline " +
              "commit is recorded for this page, so anything already committed is " +
              "invisible here — absence of a diff is not evidence that the app is unchanged.)";
    }
    const window = since
        ? `since ${since.slice(0, 12)}, when this page was last verified`
        : "since the last commit (this page records no verified baseline commit)";
    return `Dependencies of this page changed ${window}:\n\n${stat}\n\n` +
        truncate(git(["diff", range, "--", ...deps], repoDir), PATCH_BUDGET);
};

const CHANGE_MEANING: Record<Exclude<ChangeKind, "unchanged">, string> = {
    "layout-only": "the content is identical; only blank space was added or removed",
    "content-added": "every row of the committed shot is still present and unchanged; the new shot shows more",
    "content-removed": "every row of the new shot was already in the committed one; some committed rows are gone",
    changed: "content differs",
};

const rows = (span: [number, number]): string => `${span[1] - span[0]} row(s) at y=${span[0]}–${span[1] - 1}`;
const columns = (span: [number, number]): string => `${span[1] - span[0]} column(s) at x=${span[0]}–${span[1] - 1}`;

const what = (kind?: BandKind): string =>
    kind === "blank" ? "blank space" : kind === "moved" ? "moved, same pixels as an edit elsewhere" : "content";

const describeHunk = (hunk: RowHunk): string => {
    if (hunk.op === "insert") return `- inserted: ${rows(hunk.candidate)} of the new shot (${what(hunk.added)})`;
    if (hunk.op === "delete") return `- removed: ${rows(hunk.baseline)} of the committed shot (${what(hunk.removed)})`;
    if (hunk.op === "replace") {
        return `- replaced: ${rows(hunk.baseline)} of the committed shot (${what(hunk.removed)}) ` +
            `became ${rows(hunk.candidate)} of the new shot (${what(hunk.added)})`;
    }
    const moved = hunk.offset ? `, moved ${hunk.offset > 0 ? "down" : "up"} ${Math.abs(hunk.offset)} px` : "";
    const residual = hunk.residualPixels ? `, ${hunk.residualPixels} px differ within it` : "";
    return `- matched: ${rows(hunk.baseline)} of the committed shot${moved}${residual}`;
};

/** A band found inside a strip: which lines, where the strip is, and what happened. */
const describeBlock = (block: BlockHunk): string => {
    const lines = block.axis === "columns" ? columns : rows;
    const span = block.axis === "columns" ? block.rows : block.columns;
    const within = span
        ? ` (within ${block.axis === "columns" ? "rows" : "columns"} ${span[0]}–${span[1] - 1})`
        : "";
    if (block.op === "insert") return `- inserted: ${lines(block.candidate)} of the new shot${within} (${what(block.added)})`;
    if (block.op === "delete") return `- removed: ${lines(block.baseline)} of the committed shot${within} (${what(block.removed)})`;
    if (block.op === "replace") {
        return `- replaced: ${lines(block.baseline)} of the committed shot (${what(block.removed)}) ` +
            `became ${lines(block.candidate)} of the new shot${within} (${what(block.added)})`;
    }
    const offset = block.offset ?? 0;
    const direction = block.axis === "columns"
        ? (offset > 0 ? "right" : "left")
        : (offset > 0 ? "down" : "up");
    return `- moved: ${lines(block.baseline)} of the committed shot, ${direction} ${Math.abs(offset)} px${within}`;
};

/**
 * The aligned comparison as the model should read it: what kind of change, then each
 * band in physical pixels. Matched runs that neither moved nor differ are left out.
 */
const describeAlignment = (diff: CompareResult): string => {
    const before = diff.baselineSize?.join("x");
    const after = diff.candidateSize?.join("x");
    const size = before && after ? ` The shot went from ${before} to ${after}.` : "";
    const kind = diff.change && diff.change !== "unchanged" ? diff.change : "changed";
    const bands = [
        ...(diff.hunks ?? [])
            .filter((hunk) => hunk.op !== "equal" || hunk.offset || hunk.residualPixels)
            .map(describeHunk),
        ...(diff.blocks ?? []).map(describeBlock),
    ];
    const axes = diff.blocks?.length
        ? "Rows were aligned before comparing, and strips that still differed were aligned " +
          "along their columns too"
        : "Rows were aligned before comparing";
    return `${axes}, so content that only moved is not counted as changed. ` +
        `Result: ${kind} — ${CHANGE_MEANING[kind]}.${size}\n` +
        `${bands.join("\n")}\n` +
        `${diff.residualPixels ?? 0} pixel(s) differ across the matched rows.`;
};

const describeDiff = (shot: ShotResult): string => {
    if (shot.status === "new") return "No committed baseline: this shot is new.";
    const diff = shot.diff;
    if (!diff) return "No comparison was recorded.";
    if (diff.hunks?.length) return describeAlignment(diff);
    if (diff.reason === "size") {
        const before = diff.baselineSize?.join("x") ?? "unknown";
        const after = diff.candidateSize?.join("x") ?? "unknown";
        return `Reframed: ${before} became ${after}; ${diff.diffPixels} pixels differ ` +
            "after top-left alignment.";
    }
    const box = diff.box
        ? `bounded by ${box_(diff.box)}`
        : "with no bounding box";
    return `${diff.diffPixels} pixels differ (${((diff.ratio ?? 0) * 100).toFixed(4)}% of the image), ${box}. ` +
        `Shot is ${shot.width}x${shot.height}.`;
};

const box_ = (b: {x: number; y: number; width: number; height: number}): string =>
    `${b.width}x${b.height} at (${b.x}, ${b.y})`;

export interface EvidenceOptions {
    guideDir: string;
    stagingDir: string;
    walkthroughSource: string;
    walkthroughPath: string;
    covers?: string;
    /** From Detector B, for the page being healed. */
    stringChanges?: StringChange[];
    /**
     * The commit this page's baseline was captured at, from its lockfile. Bounds the
     * dependency diff; without it only uncommitted work is visible to triage.
     */
    lastVerifiedSha?: string | null;
    /** The repository to resolve dependencies and run git in. Defaults to the checkout. */
    repoDir?: string;
}

export const buildEvidence = (
    result: WalkthroughResult,
    shot: ShotResult,
    options: EvidenceOptions
): Evidence => {
    const markdownPath = join(options.guideDir, `${result.page}.md`);
    return {
        page: result.page,
        target: shot.target,
        baselinePath: join(options.guideDir, shot.target),
        candidatePath: shot.staged,
        overlayPath: shot.diff?.diffImage ?? undefined,
        diffSummary: describeDiff(shot),
        markdown: existsSync(markdownPath) ? readFileSync(markdownPath, "utf8") : "(page not found)",
        markdownPath,
        walkthroughPath: options.walkthroughPath,
        covers: options.covers,
        walkthroughSource: options.walkthroughSource,
        codeDiff: dependencyDiff(
            result.observation, options.lastVerifiedSha, options.repoDir ?? REPO),
        stringChanges: options.stringChanges ?? [],
    };
};
