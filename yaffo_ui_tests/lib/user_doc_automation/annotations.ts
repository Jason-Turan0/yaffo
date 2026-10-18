import {existsSync, readFileSync, writeFileSync} from "fs";
import {basename, join} from "path";
import type {AnnotationGeometry} from "./framing";
import type {WalkthroughResult} from "./runner";

/**
 * Keep annotated screenshots' markers on the elements they describe.
 *
 * A walkthrough names each annotation's element by selector (`Shot.annotations`), and
 * capture measures it in the image's own coordinates. This writes those measurements
 * into the page markdown, so a reframed shot moves its markers with it instead of
 * leaving them on whatever now occupies the old spot.
 */

/**
 * The heading id Python-Markdown's `toc` extension generates, which is what both the
 * docs site and an annotation's key in the walkthrough use. Mirrors its default
 * `slugify`: strip accents and punctuation, lower-case, and hyphenate whitespace.
 */
export const slugify = (heading: string): string =>
    heading
        .replace(/[*_`]/g, "")
        .normalize("NFKD")
        .replace(/[^\x00-\x7F]/g, "")
        .replace(/[^\w\s-]/g, "")
        .trim()
        .toLowerCase()
        .replace(/[-\s]+/g, "-");

export interface AnnotationSync {
    markdown: string;
    /** Annotation ids whose geometry was written. */
    applied: string[];
    /** Ids the walkthrough measured that the page has no annotation for. */
    unknown: string[];
    /** Annotations on the page that the walkthrough does not measure; left as written. */
    unmeasured: string[];
}

const OPENING = /<div class="screenshot-annotation"[^>]*>/g;
const HEADING = /^#{3,4}\s+(.+?)\s*$/m;

const attributes = (geometry: AnnotationGeometry): string =>
    `data-x="${geometry.marker[0]}" data-y="${geometry.marker[1]}" ` +
    `data-region="${geometry.region.join(" ")}"`;

/**
 * Rewrite the geometry of the annotated screenshot that shows `image`. The block runs
 * from its wrapper to the next annotated screenshot, which is far enough to reach all
 * of its annotations and never far enough to reach another image's.
 */
export const applyAnnotations = (
    markdown: string,
    image: string,
    measured: Record<string, AnnotationGeometry>
): AnnotationSync => {
    const imageAt = markdown.search(new RegExp(`\\]\\([^)]*/${image.replace(/[.]/g, "\\.")}\\)`));
    const result: AnnotationSync = {markdown, applied: [], unknown: [], unmeasured: []};
    const start = imageAt < 0 ? -1 : markdown.lastIndexOf('<div class="annotated-screenshot', imageAt);
    if (start < 0) {
        result.unknown = Object.keys(measured);
        return result;
    }
    const next = markdown.indexOf('<div class="annotated-screenshot', imageAt);
    const end = next < 0 ? markdown.length : next;
    const block = markdown.slice(start, end).replace(OPENING, (opening, offset: number, whole: string) => {
        const id = slugify(HEADING.exec(whole.slice(offset + opening.length))?.[1] ?? "");
        const geometry = measured[id];
        if (!geometry) {
            result.unmeasured.push(id);
            return opening;
        }
        result.applied.push(id);
        return opening
            .replace(/\s+data-(x|y|region)="[^"]*"/g, "")
            .replace('class="screenshot-annotation"', `class="screenshot-annotation" ${attributes(geometry)}`);
    });
    result.unknown = Object.keys(measured).filter((id) => !result.applied.includes(id));
    result.markdown = markdown.slice(0, start) + block + markdown.slice(end);
    return result;
};

/**
 * Write measured geometry into each page that captured some. Returns one line per
 * page that changed or disagrees with its walkthrough, for the capture report.
 */
export const syncAnnotations = (guideDir: string, results: WalkthroughResult[]): string[] => {
    const report: string[] = [];
    for (const result of results) {
        const path = join(guideDir, `${result.page}.md`);
        if (!existsSync(path)) continue;
        const original = readFileSync(path, "utf8");
        let markdown = original;
        for (const shot of result.shots) {
            if (!shot.annotations) continue;
            const sync = applyAnnotations(markdown, basename(shot.target), shot.annotations);
            markdown = sync.markdown;
            for (const id of sync.unknown) {
                report.push(`${result.page}: ${basename(shot.target)} measures ${id}, which the page does not annotate`);
            }
            for (const id of sync.unmeasured) {
                report.push(`${result.page}: ${basename(shot.target)} annotation ${id} has no selector in the walkthrough`);
            }
        }
        if (markdown !== original) {
            writeFileSync(path, markdown);
            report.push(`${result.page}: annotation geometry updated`);
        }
    }
    return report;
};
