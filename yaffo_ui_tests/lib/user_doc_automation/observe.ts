import type {BrowserContext, Request} from "@playwright/test";

/**
 * The runtime dependency set for one guide page: the second output of every
 * walkthrough, and the input to Oracle C.
 */
export interface Observation {
    page: string;
    /** Same-origin paths visited, with numeric ids normalized. */
    urls: string[];
    /** Source files under yaffo/static that the page loaded. */
    static: string[];
    /**
     * Templates rendered and route modules hit, from the server-side observer
     * (yaffo/doc_observer.py). A browser cannot see either: it observes that
     * "GET /" returned HTML, not that Flask dispatched to yaffo/routes/home.py and
     * pulled in components/photo_card.html.
     */
    templates: string[];
    routes: string[];
    /**
     * Whether the server-side observer answered. Without this an empty templates
     * list is ambiguous between "the app was not started with YAFFO_DOC_OBSERVER=1"
     * and "nothing was rendered".
     */
    serverObserver: "recorded" | "unavailable";
}

/** Rides along as metadata so a bucket says which page produced it. */
export const PAGE_HEADER = "X-Yaffo-Doc-Page";
/**
 * Buckets the server-side records. A fresh id per walkthrough run means two runs
 * never share a bucket, so collecting one cannot disturb another and there is no
 * global reset to race against.
 */
export const RUN_HEADER = "X-Yaffo-Doc-Run";

/** Mirrors OBSERVER_PREFIX in yaffo/doc_observer.py. */
const OBSERVER_PREFIX = "/__doc_observer__";

export interface ServerObservation {
    routes: string[];
    templates: string[];
    serverObserver: "recorded" | "unavailable";
}

const UNAVAILABLE: ServerObservation = {routes: [], templates: [], serverObserver: "unavailable"};

/**
 * Collect one run's records, consuming them server-side.
 *
 * Absent observer (the app started without YAFFO_DOC_OBSERVER=1) is reported rather
 * than thrown: capture still works without it, only the dependency set is short. A
 * 404 means the run recorded nothing, which is reported the same way — an app that
 * answered but saw no yaffo source is indistinguishable from one that was not
 * watching, and neither should fail the capture.
 */
export const takeServerObservation = async (
    baseUrl: string,
    runId: string
): Promise<ServerObservation> => {
    try {
        const response = await fetch(`${baseUrl}${OBSERVER_PREFIX}/${encodeURIComponent(runId)}`);
        if (!response.ok) return UNAVAILABLE;
        const body = (await response.json()) as {routes?: string[]; templates?: string[]};
        return {
            routes: body.routes ?? [],
            templates: body.templates ?? [],
            serverObserver: "recorded",
        };
    } catch {
        return UNAVAILABLE;
    }
};

/**
 * Numeric path segments are fixture-dependent — /media/view/6 becomes /media/view/6
 * only for this seed. Normalizing keeps the lockfile stable when the fixture is
 * rebuilt and ids shift.
 */
const normalize = (pathname: string): string => pathname.replace(/\/\d+(?=\/|$)/g, "/:id");

export interface Observer {
    attach: (context: BrowserContext) => void;
    result: (server?: ServerObservation) => Observation;
}

export const createObserver = (page: string, baseUrl: string): Observer => {
    const urls = new Set<string>();
    const statics = new Set<string>();
    const origin = new URL(baseUrl).origin;

    const record = (request: Request): void => {
        let url: URL;
        try {
            url = new URL(request.url());
        } catch {
            return; // data: and blob: requests carry no dependency information
        }
        if (url.origin !== origin) return;
        if (url.pathname.startsWith("/static/")) {
            statics.add(`yaffo${url.pathname}`);
            return;
        }
        urls.add(normalize(url.pathname));
    };

    return {
        attach: (context) => context.on("request", record),
        result: (server: ServerObservation = UNAVAILABLE) => ({
            page,
            urls: [...urls].sort(),
            static: [...statics].sort(),
            templates: server.templates,
            routes: server.routes,
            serverObserver: server.serverObserver,
        }),
    };
};
