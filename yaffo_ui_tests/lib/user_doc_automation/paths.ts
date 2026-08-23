import {join, resolve} from "path";

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

/** The sandbox to drive. Its own variable because BASE_URL is overloaded in this repo. */
export const BASE_URL = process.env.DOCS_BASE_URL || "http://127.0.0.1:5002";

/** `{area}/{page}` -> its two parts. */
export const splitPage = (page: string): [string, string] =>
    [page.slice(0, page.indexOf("/")), page.slice(page.indexOf("/") + 1)];

/** A page's folder under the content tree. */
export const pageDir = (page: string): string => join(CONTENT_DIR, ...splitPage(page));
