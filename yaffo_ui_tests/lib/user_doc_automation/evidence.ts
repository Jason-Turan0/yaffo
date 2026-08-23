import {execFileSync} from "child_process";
import {existsSync, readFileSync} from "fs";
import {join, resolve} from "path";
import type {ShotResult, WalkthroughResult} from "./runner";

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
    /** The page's obligation, from spec.yaml. */
    covers?: string;
    walkthroughSource: string;
    /** Diff of the page's observed dependencies only. */
    codeDiff: string;
    /** Diff of the user-visible string catalogue (Oracle B). */
    catalogDiff: string;
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
        return `Reframed: the capture changed size, so pixels were not compared. ` +
            `Now ${shot.width}x${shot.height}.`;
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
    covers?: string;
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
        covers: options.covers,
        walkthroughSource: options.walkthroughSource,
        codeDiff: truncate(dependencyDiff(result.observation), 12_000),
        catalogDiff: truncate(git(["diff", "HEAD", "--", "messages.pot"]).trim()
            || "(no changes to the message catalogue)", 4_000),
    };
};
