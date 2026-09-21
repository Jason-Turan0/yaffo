import {execFileSync} from "child_process";
import {existsSync, readFileSync} from "fs";
import {join, resolve} from "path";
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

const describeDiff = (shot: ShotResult): string => {
    if (shot.status === "new") return "No committed baseline: this shot is new.";
    const diff = shot.diff;
    if (!diff) return "No comparison was recorded.";
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
