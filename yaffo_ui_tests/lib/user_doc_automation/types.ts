import type {Page} from "@playwright/test";

export interface Viewport {
    width: number;
    height: number;
}

/** Trim a crop to a whole number of grid rows. */
export interface RowRule {
    /** Container holding the tiles. */
    grid: string;
    /** Tile selector within the container. */
    item: string;
    /** How many complete rows to keep. */
    count: number;
}

export interface Shot {
    /**
     * Viewport the page lays out in. Must be tall enough for `clip` to lay out
     * fully: the element's own box is capped by the viewport, so a short viewport
     * silently truncates the shot rather than failing.
     */
    viewport: Viewport;
    /** Path to open. Include any query that pins server-persisted state. */
    goto: string;
    /** Element whose box defines the crop. Omit for a plain viewport shot. */
    clip?: string;
    /** Trim the crop to N complete rows of a grid. */
    rows?: RowRule;
    /**
     * Selectors whose boxes are excluded from *comparison* — never masked in the
     * published image. For content that is not reproducible run to run, such as
     * live map tiles. See "Non-reproducible regions" in the plan.
     */
    ignoreRegions?: string[];
    /** Drive the page into the state this shot needs, after it has settled. */
    setup?: (page: Page) => Promise<void>;
}

export interface FlowContext {
    /** Open a path and wait for it to settle. */
    visit: (path: string) => Promise<void>;
    page: Page;
}

export interface Walkthrough {
    /** Guide page id. Must match a key under `pages:` in spec.yaml. */
    page: string;
    /**
     * Output filename -> how to capture it. Filenames are the shot's identity and
     * must match an image reference in that page's markdown.
     */
    shots: Record<string, Shot>;
    /**
     * Flows the page describes but does not illustrate. Driven purely so their
     * routes, templates, and static assets land in the dependency set — a page
     * with no shots at all still gets one of these.
     */
    flows?: (ctx: FlowContext) => Promise<void>;
}

export const defineWalkthrough = (walkthrough: Walkthrough): Walkthrough => walkthrough;
