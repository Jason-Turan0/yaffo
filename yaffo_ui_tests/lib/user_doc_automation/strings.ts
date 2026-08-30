import {execFileSync} from "child_process";
import {readFileSync} from "fs";
import {join, resolve} from "path";

/**
 * The app's user-visible English strings, and how they changed between two commits.
 *
 * Two catalogues, because the app renders text from two places, and they diff
 * differently enough to warrant separate handling:
 *
 * - `messages.pot` — server-rendered text. The msgid *is* the English string, so
 *   rewording a control removes one msgid and adds another with nothing linking them.
 *   A change is only ever visible as a disappearance.
 * - `static/locales/en.json` — client-side text. The key is stable and the value is the
 *   English string, so the same reword is a value change under one key, and both the
 *   before and after are recoverable.
 *
 * Only the English sources are read. The other locales are downstream translations; a
 * change to `de.json` cannot make an English guide page wrong.
 */
export const CATALOGUE_FILES = ["messages.pot", "yaffo/static/locales/en.json"];

const REPO = resolve(join(process.cwd(), ".."));

/** A string that no longer says what the guide says it does. */
export interface StringChange {
    /** The English text as the guide would have quoted it. */
    was: string;
    /** What replaced it, when the catalogue makes that recoverable. */
    now?: string;
    /** Which catalogue it came from. */
    source: "messages.pot" | "en.json";
    /** The JSON key, where there is one — useful for pointing at the change. */
    key?: string;
}

/**
 * A catalogue as of `ref`, or as it stands on disk when `ref` is null.
 *
 * The working tree is the default "after" because the change being detected is usually
 * uncommitted — you rename a control, re-extract the catalogue, and want to know which
 * pages that breaks *before* committing. Reading `HEAD` would compare a commit against
 * itself and report everything clean.
 */
const catalogue = (ref: string | null, path: string): string | null => {
    try {
        return ref === null
            ? readFileSync(join(REPO, path), "utf8")
            : execFileSync("git", ["show", `${ref}:${path}`],
                {
                    cwd: REPO,
                    encoding: "utf8",
                    maxBuffer: 8 * 1024 * 1024,
                    stdio: ["ignore", "pipe", "pipe"],
                });
    } catch {
        const source = ref === null ? "the working tree" : `watermark ${ref}`;
        console.warn(`String catalogue ${path} is unavailable at ${source}; ` +
            "skipping its quoted-string comparison.");
        return null;   // absent at that commit, or not a valid ref
    }
};

/** msgids, ignoring the empty header entry. */
export const potStrings = (contents: string): Set<string> =>
    new Set([...contents.matchAll(/^msgid "(.+)"$/gm)].map((m) => m[1]));

/** Flattened `namespace.key` -> English text. */
export const jsonStrings = (contents: string): Map<string, string> => {
    const out = new Map<string, string>();
    const walk = (node: unknown, prefix: string): void => {
        if (!node || typeof node !== "object") return;
        for (const [key, value] of Object.entries(node as Record<string, unknown>)) {
            if (typeof value === "string") out.set(`${prefix}${key}`, value);
            else walk(value, `${prefix}${key}.`);
        }
    };
    try {
        walk(JSON.parse(contents), "");
    } catch {
        // A malformed catalogue at one end of the diff is not worth failing the run.
    }
    return out;
};

/**
 * Strings that stopped meaning what they did between `base` and `head`.
 *
 * Additions are deliberately ignored. A new string is a new feature, which is a
 * question of *incompleteness* — scoping handles that by flagging pages whose
 * dependencies changed. This detector only answers "does the guide quote something the
 * app no longer says?"
 */
export interface Catalogues {
    pot: string | null;
    json: string | null;
}

/**
 * Diff two snapshots of the catalogues. Pure, so the rules below are testable without
 * a repository: the git reads live in `changedStrings`.
 */
export const diffCatalogues = (before: Catalogues, after: Catalogues): StringChange[] => {
    const changes: StringChange[] = [];

    if (before.pot && after.pot) {
        const present = potStrings(after.pot);
        for (const was of potStrings(before.pot)) {
            if (!present.has(was)) changes.push({was, source: "messages.pot"});
        }
    }

    if (before.json && after.json) {
        const present = jsonStrings(after.json);
        for (const [key, was] of jsonStrings(before.json)) {
            const now = present.get(key);
            // A deleted key and a reworded one are the same thing to a reader: the guide
            // quotes text the app no longer shows. The reworded case keeps both sides,
            // which the gettext catalogue cannot offer.
            if (now === undefined) changes.push({was, source: "en.json", key});
            else if (now !== was) changes.push({was, now, source: "en.json", key});
        }
    }

    return changes;
};

/**
 * Strings that stopped meaning what they did between `base` and `head`.
 *
 * Additions are deliberately ignored. A new string is a new feature, which is a
 * question of *incompleteness* — scoping handles that by flagging pages whose
 * dependencies changed. This detector only answers "does the guide quote something the
 * app no longer says?"
 */
export const changedStrings = (base: string, head: string | null = null): StringChange[] =>
    diffCatalogues(
        {pot: catalogue(base, "messages.pot"), json: catalogue(base, "yaffo/static/locales/en.json")},
        {pot: catalogue(head, "messages.pot"), json: catalogue(head, "yaffo/static/locales/en.json")},
    );

/**
 * Spans a guide page presents as UI: `**Apply Filters**` and `` `dog` ``.
 *
 * Matching is confined to these rather than the whole page. Bare substring search finds
 * half again as many hits, but nearly all of the excess is words like "All", "Year" and
 * "Move" appearing in ordinary prose — and a control written in bold is precisely the
 * one a reader is being told to click.
 */
export const emphasisedSpans = (markdown: string): Set<string> =>
    new Set([
        ...[...markdown.matchAll(/\*\*([^*\n]+)\*\*/g)].map((m) => m[1]),
        ...[...markdown.matchAll(/`([^`\n]+)`/g)].map((m) => m[1]),
    ]);

/** The changes a page actually quotes. */
export const changesQuotedBy = (markdown: string, changes: StringChange[]): StringChange[] => {
    const spans = emphasisedSpans(markdown);
    return changes.filter((change) => spans.has(change.was));
};
