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

const git = (args: string[]): string => {
    try {
        return execFileSync("git", args, {cwd: REPO, encoding: "utf8", maxBuffer: 8 * 1024 * 1024});
    } catch {
        return "";
    }
};

/**
 * Uncommitted changes to the files this page actually depends on.
 *
 * Against HEAD rather than a watermark: the watermark lands with the workflow, and
 * for a local run "what have I changed since the last commit" is the useful window.
 */
const dependencyDiff = (observation: WalkthroughResult["observation"]): string => {
    const deps = [...observation.routes, ...observation.templates, ...observation.static]
        .filter((path) => existsSync(join(REPO, path)));
    if (!deps.length) return "(no observed dependencies recorded — is the server observer running?)";
    const diff = git(["diff", "HEAD", "--", ...deps]);
    return diff.trim() || "(no changes to this page's dependencies)";
};

const truncate = (text: string, limit: number): string =>
    text.length <= limit ? text : `${text.slice(0, limit)}\n… (${text.length - limit} more characters)`;

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
        codeDiff: truncate(dependencyDiff(result.observation), 12_000),
        stringChanges: options.stringChanges ?? [],
    };
};
