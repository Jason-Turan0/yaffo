import {afterEach, describe, expect, it} from "@jest/globals";
import {mkdtempSync, readFileSync, rmSync, writeFileSync, mkdirSync} from "fs";
import {tmpdir} from "os";
import {join} from "path";
import type {Page} from "@playwright/test";
import {applyAnnotations, slugify, syncAnnotations} from "../annotations";
import {annotationGeometry, resolveAnnotations, spreadMarkers} from "../framing";
import type {WalkthroughResult} from "../runner";

const PAGE = `# Health

<div class="annotated-screenshot" markdown="1">

![Library Health](assets/health/library-health.webp)

<div class="screenshot-annotation" data-x="1" data-y="2" data-region="0 0 1 1" markdown="1">

#### Library Counts

The counts.

</div>

<div class="screenshot-annotation" markdown="1">

#### Reindex Library

Rebuilds everything.

</div>

<div class="screenshot-annotation" data-x="5" data-y="5" data-region="5 5 5 5" markdown="1">

#### Placed by Hand

Kept.

</div>

</div>

<div class="annotated-screenshot" markdown="1">

![Other](assets/health/other.webp)

<div class="screenshot-annotation" data-x="9" data-y="9" data-region="9 9 9 9" markdown="1">

### Library Counts

Another image's annotation with the same name.

</div>

</div>
`;

const counts = {region: [1.5, 22.5, 97, 43.5], marker: [97.5, 25.5]} as const;
const reindex = {region: [82.5, 6.5, 14.3, 6], marker: [95.3, 9.5]} as const;

describe("slugify", () => {
    it("matches the ids the docs site gives headings", () => {
        expect(slugify("Library Counts")).toBe("library-counts");
        expect(slugify("What It Can Look At")).toBe("what-it-can-look-at");
        expect(slugify("Triggers: When an Automation Runs")).toBe("triggers-when-an-automation-runs");
        expect(slugify("Review a Person's Faces")).toBe("review-a-persons-faces");
        expect(slugify("Grid and **Timeline**")).toBe("grid-and-timeline");
    });
});

describe("annotationGeometry", () => {
    it("frames several boxes together in percent of the image", () => {
        const geometry = annotationGeometry(
            [{x: 110, y: 60, width: 100, height: 20}, {x: 220, y: 60, width: 80, height: 40}],
            {x: 100, y: 50},
            {width: 1000, height: 500}
        );
        expect(geometry.region).toEqual([1, 2, 19, 8]);
        // A panel: inside its top-right corner.
        expect(geometry.marker).toEqual([18.5, 4.5]);
    });

    it("puts a control's marker beside it, on the left at the image's right edge", () => {
        const image = {width: 1000, height: 1000};
        expect(annotationGeometry([{x: 100, y: 100, width: 50, height: 40}], {x: 0, y: 0}, image).marker)
            .toEqual([16.5, 12]);
        expect(annotationGeometry([{x: 920, y: 100, width: 60, height: 40}], {x: 0, y: 0}, image).marker)
            .toEqual([90.5, 12]);
    });

    it("pulls the marker inside an image edge and clamps boxes to the image", () => {
        const geometry = annotationGeometry(
            [{x: 900, y: -20, width: 200, height: 400}],
            {x: 0, y: 0},
            {width: 1000, height: 1000}
        );
        expect(geometry.region).toEqual([90, 0, 10, 38]);
        expect(geometry.marker).toEqual([98.5, 2.5]);
    });

    it("moves a marker down when it would land on an earlier one", () => {
        const at = (x: number, y: number) => ({region: [0, 0, 1, 1] as [number, number, number, number], marker: [x, y] as [number, number]});
        const geometry = {panel: at(97, 3), header: at(97.5, 4), other: at(50, 3)};
        spreadMarkers(geometry);
        expect(geometry.header.marker).toEqual([97.5, 7]);
        expect(geometry.other.marker).toEqual([50, 3]);
    });
});

describe("resolveAnnotations", () => {
    const page = (boxes: Record<string, {x: number; y: number; width: number; height: number}[]>): Page => ({
        locator: (selector: string) => ({
            all: async () => (boxes[selector] ?? []).map((box) => ({boundingBox: async () => box})),
        }),
    }) as unknown as Page;

    it("measures each annotation relative to the clip", async () => {
        const geometry = await resolveAnnotations(
            page({".counts": [{x: 50, y: 150, width: 400, height: 100}]}),
            {viewport: {width: 800, height: 600}, goto: "/", annotations: {"library-counts": ".counts"}},
            {x: 50, y: 50, width: 500, height: 500}
        );
        expect(geometry["library-counts"].region).toEqual([0, 20, 80, 20]);
    });

    it("measures against the part of a tall clip the viewport actually captured", async () => {
        const geometry = await resolveAnnotations(
            page({".low": [{x: 0, y: 400, width: 100, height: 100}]}),
            {viewport: {width: 800, height: 600}, goto: "/", annotations: {low: ".low"}},
            {x: 0, y: 100, width: 800, height: 1500}
        );
        // The image is 500px tall (100..600), not 1500.
        expect(geometry.low.region).toEqual([0, 60, 12.5, 20]);
    });

    it("fails the shot when a selector frames nothing visible", async () => {
        await expect(resolveAnnotations(
            page({".gone": [{x: 0, y: 0, width: 0, height: 0}]}),
            {viewport: {width: 800, height: 600}, goto: "/", annotations: {gone: ".gone"}},
            undefined
        )).rejects.toThrow("Annotation gone: no visible element for .gone");
    });
});

describe("applyAnnotations", () => {
    it("rewrites measured annotations and leaves the rest of the page alone", () => {
        const sync = applyAnnotations(PAGE, "library-health.webp",
            {"library-counts": counts as never, "reindex-library": reindex as never, missing: counts as never});
        expect(sync.applied).toEqual(["library-counts", "reindex-library"]);
        expect(sync.unmeasured).toEqual(["placed-by-hand"]);
        expect(sync.unknown).toEqual(["missing"]);
        expect(sync.markdown).toContain(
            '<div class="screenshot-annotation" data-x="97.5" data-y="25.5" data-region="1.5 22.5 97 43.5" markdown="1">');
        expect(sync.markdown).toContain(
            '<div class="screenshot-annotation" data-x="95.3" data-y="9.5" data-region="82.5 6.5 14.3 6" markdown="1">');
        expect(sync.markdown).toContain('data-x="5" data-y="5" data-region="5 5 5 5"');
        // The other image's same-named annotation is outside this block.
        expect(sync.markdown).toContain('data-x="9" data-y="9" data-region="9 9 9 9"');
    });

    it("reports every id as unknown when the image is not annotated", () => {
        const sync = applyAnnotations("![Plain](assets/x/plain.webp)\n", "plain.webp", {a: counts as never});
        expect(sync.unknown).toEqual(["a"]);
        expect(sync.markdown).toBe("![Plain](assets/x/plain.webp)\n");
    });
});

describe("syncAnnotations", () => {
    let guideDir: string | undefined;
    afterEach(() => {
        if (guideDir) rmSync(guideDir, {recursive: true, force: true});
    });

    it("writes changed pages and reports disagreements", () => {
        guideDir = mkdtempSync(join(tmpdir(), "annotations-"));
        mkdirSync(join(guideDir, "library-basics"));
        writeFileSync(join(guideDir, "library-basics", "health.md"), PAGE);
        const result = {
            page: "library-basics/health",
            observation: {} as never,
            shots: [{
                target: "library-basics/assets/health/library-health.webp",
                annotations: {"library-counts": counts, "reindex-library": reindex},
            }],
        } as unknown as WalkthroughResult;

        const report = syncAnnotations(guideDir, [result]);

        expect(readFileSync(join(guideDir, "library-basics", "health.md"), "utf8"))
            .toContain('data-region="82.5 6.5 14.3 6"');
        expect(report).toEqual([
            "library-basics/health: library-health.webp annotation placed-by-hand has no selector in the walkthrough",
            "library-basics/health: annotation geometry updated",
        ]);
    });
});
