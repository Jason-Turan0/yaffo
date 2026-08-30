import {existsSync, mkdirSync, readdirSync, realpathSync, rmSync} from "fs";
import {tmpdir} from "os";
import {basename, dirname, join, resolve} from "path";

/**
 * Every path the docs pipeline uses, in one place.
 *
 * Resolved from the working directory, like the rest of the harness: every entry point
 * here is run from `yaffo_ui_tests/`.
 *
 * The reason this is centralized rather than repeated per entry point is the split
 * below. `CONTENT_DIR` is handed to the agent's filesystem tool; `STAGING_DIR` must not
 * be reachable from it. Keeping the two definitions apart in four files is how they
 * drifted into a parent/child relationship in the first place.
 */
export const REPO = resolve(join(process.cwd(), ".."));

/**
 * Authored and generated content: the spec, `_support/`, and one folder per guide page.
 * **Granted to the agent's filesystem tool** — everything under it is readable by a
 * model, so nothing transient belongs here.
 */
export const CONTENT_DIR = resolve(join(process.cwd(), "user_doc_automation"));

export const GUIDE_DIR = resolve(
    process.env.GUIDE_DIR || join(REPO, "docs", "guide"));

/**
 * Transient machine output: staged captures, diff overlays, `raw.json`, `report.json`,
 * and the per-run API logs.
 *
 * Deliberately a *sibling* of `CONTENT_DIR`, not a child. It used to be
 * `user_doc_automation/.staging`, which put it inside the tree granted to the
 * filesystem tool — so a generate run could read back its own API logs, prompts and
 * reasoning included, and was observed doing exactly that. Nothing here is input to
 * the agent; it is all output about the agent.
 */
export const STAGING_DIR = resolve(
    process.env.DOCS_STAGING_DIR || join(process.cwd(), ".doc-staging"));

/**
 * Where captures land. A subdirectory of staging, because a capture run **empties**
 * this directory before it starts, and it must only ever delete its own output.
 *
 * It used to be `STAGING_DIR` itself, which also holds `generate-logs/` and
 * `heal-logs/`. The capture gate therefore deleted the run's own log directory
 * mid-flight, and the next API call died with
 * `ENOENT: ... .doc-staging/generate-logs/1_gemini_api.json`.
 *
 * `DOCS_CAPTURE_DIR` overrides it directly, and is what the container is given. It has
 * to be its own variable rather than reusing `DOCS_STAGING_DIR`: passing the capture
 * path as the staging path makes this line append `captures` to it a second time, and
 * the container then writes to `captures/captures/` while the host reads `captures/`.
 */
export const CAPTURE_DIR = resolve(
    process.env.DOCS_CAPTURE_DIR || join(STAGING_DIR, "captures"));

/** The sandbox to drive. Its own variable because BASE_URL is overloaded in this repo. */
export const BASE_URL = process.env.DOCS_BASE_URL || "http://127.0.0.1:5002";

/**
 * Canonical host path of the reproducible documentation fixture.
 *
 * macOS exposes `/tmp` through a `/private/tmp` symlink. The seed intentionally
 * stores resolved paths in SQLite, so walkthroughs must use that same spelling when
 * they submit fixture directories back to the app. This value is passed unchanged
 * into the Linux capture container; resolving it inside the container would describe
 * the container's filesystem instead of the host application being driven.
 */
const canonicalHostPath = (value: string): string => {
    const absolute = resolve(value);
    if (existsSync(absolute)) return realpathSync(absolute);
    const parent = dirname(absolute);
    // The fixture itself may not exist yet during `docs:fixture:build`, but its
    // temp parent does. Resolving the parent still removes macOS's /tmp alias.
    return existsSync(parent)
        ? join(realpathSync(parent), basename(absolute))
        : absolute;
};
const defaultDocsDataDir = join(
    process.platform === "win32" ? tmpdir() : "/tmp",
    "yaffo-docs"
);
export const DOCS_DATA_DIR = canonicalHostPath(
    process.env.YAFFO_DOCS_DATA_DIR || defaultDocsDataDir);

/** `{area}/{page}` -> its two parts. */
export const splitPage = (page: string): [string, string] =>
    [page.slice(0, page.indexOf("/")), page.slice(page.indexOf("/") + 1)];

/** A page's folder under the content tree. */
export const pageDir = (page: string): string => join(CONTENT_DIR, ...splitPage(page));

/**
 * How many past runs of each kind to keep. Enough to compare a failure against the run
 * before it; bounded because each run writes every request and response in full.
 */
const KEEP_RUNS = 20;

/**
 * A fresh log directory for this run, under `generate-logs/` or `heal-logs/`.
 *
 * Per-run rather than shared, because `apiCallCount` restarts at zero every run: with
 * one flat directory, a rerun silently overwrote the previous run's `0_*.json`,
 * `1_*.json` and so on. Two people reading "call 9" were then looking at different
 * runs through the same filename — which happened, mid-investigation, and cost real
 * time before anyone noticed the logs had been swapped underneath them.
 *
 * The name sorts chronologically as a string, so `ls` is oldest-first and the newest
 * run is the last line.
 */
export const newRunLogDir = (kind: "generate-logs" | "heal-logs"): string => {
    const root = join(STAGING_DIR, kind);
    mkdirSync(root, {recursive: true});
    pruneRunLogs(root);

    // 2026-08-23T07-41-57-805Z — ISO order, no colons, safe on every filesystem.
    const stamp = new Date().toISOString().replace(/[:.]/g, "-");
    const dir = join(root, stamp);
    mkdirSync(dir, {recursive: true});
    return dir;
};

/** Drop the oldest runs, keeping the most recent KEEP_RUNS. */
const pruneRunLogs = (root: string): void => {
    if (!existsSync(root)) return;
    const runs = readdirSync(root, {withFileTypes: true})
        .filter((entry) => entry.isDirectory())
        .map((entry) => entry.name)
        .sort();                                  // Timestamps sort chronologically.
    for (const stale of runs.slice(0, Math.max(0, runs.length - KEEP_RUNS + 1))) {
        rmSync(join(root, stale), {recursive: true, force: true});
    }
};
