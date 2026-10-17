import {execFileSync} from "child_process";
import type {Box} from "./framing";
import {supportScript, VENV_PYTHON} from "./python";

export type CompareStatus = "changed" | "unchanged";

/**
 * What kind of change the row-aligned comparison found. Rows are aligned before the
 * pixel test, so content that only moved is not reported as repainted.
 *
 *   layout-only      identical content; only featureless rows added or removed
 *   content-added    every baseline row survives intact; the candidate shows more
 *   content-removed  every candidate row was in the baseline; some baseline rows are gone
 *   changed          content differs (also every width change, which is not aligned)
 */
export type ChangeKind = "unchanged" | "layout-only" | "content-added" | "content-removed" | "changed";

/**
 * What an edit's lines hold: blank space, content, or content that also appears
 * elsewhere in the other shot, so the pair of edits is really a move.
 */
export type BandKind = "blank" | "content" | "moved";

/** One step of the row alignment, in physical pixel rows, end-exclusive. */
export interface RowHunk {
    op: "equal" | "insert" | "delete" | "replace";
    baseline: [number, number];
    candidate: [number, number];
    /** Equal runs only: how far the run moved, candidate row minus baseline row. */
    offset?: number;
    /** Equal runs only: pixels that still differ once aligned. */
    residualPixels?: number;
    /** Edits only: whether the baseline rows removed or the candidate rows added carry content. */
    removed?: BandKind;
    added?: BandKind;
    /** Edits only: paired with an edit elsewhere holding the same pixels. */
    moved?: boolean;
}

/**
 * An edit or move found by aligning a strip along the other axis: columns within
 * rows that matched yet differed, or rows within those columns. Lines are columns
 * when `axis` is "columns" and rows when it is "rows", in full-image coordinates.
 * Only edits and moved runs are listed; the rest matched in place.
 */
export interface BlockHunk extends RowHunk {
    axis: "rows" | "columns";
    /** Column entries: the rows the strip spans in the new shot, and in the committed one. */
    rows?: [number, number];
    baselineRows?: [number, number];
    /** Row entries: the columns the strip spans in the new shot, and in the committed one. */
    columns?: [number, number];
    baselineColumns?: [number, number];
}

export interface CompareResult {
    status: CompareStatus;
    /** Absent from reports written before rows were aligned. */
    change?: ChangeKind;
    /**
     * Residual pixels plus every pixel of an inserted or removed row. With a width
     * change, perceptually different non-AA pixels after top-left alignment instead.
     */
    diffPixels: number;
    /** Pixels that differ once rows are aligned, excluding those hugging an edit seam. */
    residualPixels?: number;
    /** Pixels that differ within a few rows of an edit seam, where lossy encoding blurs. */
    seamPixels?: number;
    /** The alignment, when any rows were inserted, removed, or replaced; otherwise empty. */
    hunks?: RowHunk[];
    /** Edits and moves found along the other axis inside strips; absent from older reports. */
    blocks?: BlockHunk[];
    /** Pixels in the union of the two image canvases. */
    totalPixels?: number;
    /** Fraction of the image that differs. */
    ratio?: number;
    /** Bounding box of the difference, useful for pointing a reviewer at it. */
    box?: Box | null;
    /**
     * Present when the shot changed: a magenta-on-dimmed overlay for review. When rows
     * moved, baseline and candidate side by side, removed rows red and added rows green.
     */
    diffImage?: string | null;
    /** "size" when the two images have different dimensions. */
    reason?: string;
    /** Physical pixel dimensions when the two images have different sizes. */
    baselineSize?: [number, number];
    candidateSize?: [number, number];
}

/**
 * Compare a freshly captured shot against what is committed.
 *
 * Byte equality is checked first purely as a fast path — identical bytes cannot
 * differ visually. It is not sufficient on its own: the same page rendered on a
 * different machine, or re-encoded by a different libwebp, produces different bytes
 * for identical pixels, which is why the pixel comparison exists at all.
 */
export const compareShots = (
    baseline: string,
    candidate: string,
    ignore: Box[] = [],
    diffOut?: string
): CompareResult => {
    const args = [
        supportScript("imagediff.py"),
        baseline,
        candidate,
        "--ignore", JSON.stringify(ignore),
    ];
    if (diffOut) args.push("--diff-out", diffOut);
    const stdout = execFileSync(VENV_PYTHON, args, {encoding: "utf8"});
    return JSON.parse(stdout) as CompareResult;
};

/**
 * Write the review sheet for a changed shot: verdict, overview, close-ups of each
 * change, the pixel test before and after alignment, and the alignment itself. It
 * ships in the capture artifacts for people to read; nothing downstream parses it.
 *
 * A sheet that cannot be drawn is reported and skipped. It is a reading aid, and
 * must never fail the capture whose evidence it illustrates.
 */
export const renderReviewSheet = (
    baseline: string,
    candidate: string,
    ignore: Box[],
    out: string,
    name: string
): boolean => {
    try {
        execFileSync(VENV_PYTHON, [
            supportScript("review_sheet.py"), baseline, candidate, out,
            "--ignore", JSON.stringify(ignore), "--name", name,
        ], {encoding: "utf8", stdio: ["ignore", "pipe", "pipe"]});
        return true;
    } catch (e) {
        const detail = e instanceof Error ? e.message.split("\n")[0] : String(e);
        console.warn(`      review sheet not written for ${name}: ${detail}`);
        return false;
    }
};
