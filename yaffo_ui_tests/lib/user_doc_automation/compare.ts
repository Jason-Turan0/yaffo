import {execFileSync} from "child_process";
import type {Box} from "./framing";
import {supportScript, VENV_PYTHON} from "./python";

export type CompareStatus = "changed" | "unchanged";

export interface CompareResult {
    status: CompareStatus;
    /** Pixels exceeding the colour tolerance, or null when the sizes differ. */
    diffPixels: number | null;
    /** Fraction of the image that differs. */
    ratio?: number;
    /** Bounding box of the difference, useful for pointing a reviewer at it. */
    box?: Box | null;
    /** Present when the shot changed: a magenta-on-dimmed overlay for review. */
    diffImage?: string | null;
    /** "size" when the shot was reframed rather than repainted. */
    reason?: string;
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
