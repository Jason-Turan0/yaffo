import {execFileSync} from "child_process";
import {existsSync, readFileSync, writeFileSync} from "fs";
import {join, resolve} from "path";
import {z} from "zod";
import {toTextPart} from "@lib/model_clients/model_client.interface";
import type {ToolProvider} from "@lib/tool_providers/toolprovider.types";
import {parseAnswer, runToolLoop} from "./tool_loop";
import type {Evidence} from "./evidence";
import type {Session} from "./triage";

/**
 * The fix turn: having classified a change as intended, bring the page into line with
 * it.
 *
 * The agent owns the page markdown and the walkthrough outright — it may rewrite
 * prose, add a section, or change which screenshots the page carries. Nothing here
 * polices how much it changed; the output is a PR a person reads, and a reviewer
 * judges whether the change was warranted better than a line counter can. The checks
 * that do run (see `validate`) exist to stop a *broken* PR, not an opinionated one.
 *
 * Edits go through the filesystem tool rather than structured patch data. The guide is
 * hard-wrapped at 80 columns, so substituting a word of different length leaves the
 * paragraph needing a reflow that a find/replace pair cannot perform.
 */
/**
 * Mirrors `GeneratedTestResponseSchema`: the model returns whole files as its result
 * and this module writes them. The filesystem provider deliberately never exposes
 * write tools — `getTools` filters WRITE_TOOLS unconditionally — so generated
 * artefacts arrive as output, not as side effects. Tools are for investigation only.
 *
 * Whole files rather than patches, because the guide is hard-wrapped at 80 columns:
 * substituting a word of different length leaves the paragraph needing a reflow that
 * a find/replace pair cannot perform.
 */
const DocFileSchema = z.object({
    filename: z.string().describe(
        "Repo-relative path of the file to write, for example " +
        "docs/guide/library-basics/browsing-filtering.md"),
    code: z.string().describe("The complete new contents of the file"),
    description: z.string().optional().describe("What changed in this file and why"),
}).strict();

export const FixSchema = z.object({
    files: z.array(DocFileSchema).describe(
        "Every file to write. Empty when the page is already accurate."),
    explanation: z.string().optional().describe(
        "One line for the PR body, plus anything deliberately left unchanged"),
    confidence: z.number().optional().describe(
        "Confidence in the edit, from 0 (low) to 1 (high)"),
}).strict();

export type Fix = z.infer<typeof FixSchema>;

export interface FixResult {
    fix?: Fix;
    /** Paths actually written. */
    written: string[];
    /** Failed correctness gates. Empty means the change is safe to leave in place. */
    failures: string[];
    /** Whether the working tree was rolled back because a gate failed. */
    reverted: boolean;
}

/** Model turns to allow before giving up, so a confused session cannot loop forever. */
const MAX_TOOL_ROUNDS = 12;

const REPO = resolve(join(process.cwd(), ".."));

const run = (command: string, args: string[], cwd = REPO): {ok: boolean; output: string} => {
    try {
        return {ok: true, output: execFileSync(command, args, {cwd, encoding: "utf8", stdio: "pipe"})};
    } catch (e) {
        const err = e as {stdout?: string; stderr?: string; message?: string};
        return {ok: false, output: `${err.stdout ?? ""}${err.stderr ?? ""}` || err.message || "failed"};
    }
};

const buildPrompt = (evidence: Evidence, baseUrl: string): string => `
The change is intended, so bring the page into line with it.

## Tools — all read-only

You cannot write files. Nothing you do with a tool changes anything on disk; edits are
returned as your answer and written for you (see *Your answer* below).

- **memory** — this page's notes, from earlier runs. **Read them first.** They hold
  what was already learned about this page: controls that cannot be driven the obvious
  way, state that has to be pinned, shots known to be unstable. When you learn
  something worth not rediscovering, record it there.
- **filesystem** — read any file you need. The two you own are given by absolute path
  below, so read those directly rather than searching for them.
- **browser** — the running app is at ${baseUrl}. Open it to check how something
  actually looks or behaves before describing it, or to work out how a shot should be
  framed.

## The files you own

1. ${evidence.markdownPath}
   The page itself — prose, structure, and headings. Change it freely.
2. ${evidence.walkthroughPath}
   The walkthrough that captures this page's screenshots, including which shots exist
   and how each is framed.

${evidence.stringChanges.length ? `## Controls this page names that the app has renamed

${evidence.stringChanges.map((c) => c.now !== undefined
    ? `- the page says **${c.was}**; the app now says **${c.now}**`
    : `- the page says **${c.was}**, which the app no longer has`).join("\n")}

Each of these is a control the reader is told to use by a name that no longer exists.
` : ""}
## What matters

- Anything the page says that the new screenshot contradicts must be corrected —
  control names, counts, described behaviour.
- The guide is hard-wrapped at 80 columns. Re-wrap any paragraph you edit so it still
  fits; do not leave an over-long line.
- Every image reference must point at a file that exists. Adding a screenshot means
  adding it to the walkthrough too, or the docs build fails.
- The walkthrough is TypeScript and must still compile if you change it.
- Leave the page alone where it is already accurate. Do not rewrite for its own sake.

## Your answer

Return the complete new contents of every file you want changed. Whole files, not
fragments — a partial file overwrites the rest with nothing.

Reply with JSON only:

{"files": [{"filename": "docs/guide/...", "code": "<the entire new file>",
            "description": "what changed"}],
 "explanation": "one line for the PR body",
 "confidence": 0.9}

Return an empty "files" list if nothing needs changing.
`.trim();

/**
 * Correctness gates. Deliberately not style gates: nothing counts changed lines or
 * checks whether headings moved.
 */
const validate = (evidence: Evidence, walkthroughTouched: boolean): string[] => {
    const failures: string[] = [];

    const docs = run("venv/bin/mkdocs", ["build", "--strict", "--site-dir", "/tmp/docs-fix-check"]);
    if (!docs.ok) failures.push(`mkdocs build --strict failed:\n${docs.output.slice(-800)}`);

    if (walkthroughTouched) {
        const types = run("npx", ["tsc", "--noEmit"], join(REPO, "yaffo_ui_tests"));
        if (!types.ok) failures.push(`walkthrough does not typecheck:\n${types.output.slice(-800)}`);
    }

    return failures;
};

/** The two trees the agent owns. Anything outside them is refused, not clamped. */
const OWNED = ["docs/guide/", "yaffo_ui_tests/user_doc_automation/"];

const isOwned = (repoRelative: string): boolean => {
    const normalised = resolve(REPO, repoRelative);
    return OWNED.some((dir) => normalised.startsWith(resolve(REPO, dir) + "/"));
};

export interface FixOptions {
    toolProviders: ToolProvider[];
    /** The sandbox the agent can inspect while deciding what to write. */
    baseUrl: string;
    /** Roll the working tree back when a gate fails. */
    revertOnFailure?: boolean;
}


/**
 * Keep the page's catalog in step with what was just written.
 *
 * The catalog is the living record of this page's generated artifacts, the same role
 * `{feature}.json` plays for a generated test — and the test healer updates it on
 * every heal for exactly this reason. Both artifacts the agent owns belong in it: the
 * walkthrough *and* the page markdown.
 *
 * Entries are keyed by repo-relative path, and upserted rather than only updated: the
 * markdown may have no entry yet on a page whose catalog predates its first fix.
 *
 * It records what the automation last produced, not what is on disk now — a human who
 * edits the page afterwards leaves the catalog describing the previous generation,
 * which is the honest thing for it to say.
 */
const updateCatalog = (page: string, written: string[], fix: Fix): void => {
    const [area, name] = [page.slice(0, page.indexOf("/")), page.slice(page.indexOf("/") + 1)];
    const catalogPath = join(REPO, "yaffo_ui_tests", "user_doc_automation", area, name, `${name}.json`);
    if (!existsSync(catalogPath)) return;

    try {
        const catalog = JSON.parse(readFileSync(catalogPath, "utf8")) as {
            files: Array<{filename: string; code: string; description?: string}>;
            [key: string]: unknown;
        };
        for (const path of written) {
            const returned = fix.files.find((file) => file.filename === path);
            if (!returned) continue;
            const existing = catalog.files.findIndex((file) => file.filename === path);
            const entry = {
                filename: path,
                code: returned.code,
                description: returned.description ?? catalog.files[existing]?.description,
            };
            if (existing === -1) catalog.files.push(entry);
            else catalog.files[existing] = entry;
        }
        if (fix.explanation) catalog.explanation = fix.explanation;
        if (fix.confidence !== undefined) catalog.confidence = fix.confidence;
        writeFileSync(catalogPath, JSON.stringify(catalog, null, 4) + "\n");
    } catch (e) {
        // A malformed catalog must not undo a good edit; report and move on.
        console.warn(`   ⚠️  could not update the catalog: ${e instanceof Error ? e.message : String(e)}`);
    }
};

export const applyFix = async (
    session: Session,
    evidence: Evidence,
    options: FixOptions
): Promise<FixResult> => {
    const {client} = session;
    client.setOutputSchema(FixSchema);
    client.addUserMessage([toTextPart(buildPrompt(evidence, options.baseUrl))]);

    let fix: Fix | undefined;
    let answerErrors: string[] = [];
    try {
        const answer = await runToolLoop(client, options.toolProviders);
        const parsed = parseAnswer(FixSchema, answer);
        fix = parsed.value;
        answerErrors = parsed.errors;
    } catch (e) {
        // A session that runs out of turns is a failed fix, not a crash: the gates and
        // the revert below still have to run.
        answerErrors = [e instanceof Error ? e.message : String(e)];
    }

    const failures: string[] = answerErrors.map((e) => `malformed response: ${e}`);
    const written: string[] = [];
    for (const file of fix?.files ?? []) {
        if (!isOwned(file.filename)) {
            failures.push(`refused to write outside the agent's trees: ${file.filename}`);
            continue;
        }
        writeFileSync(resolve(REPO, file.filename), file.code);
        written.push(file.filename);
    }

    failures.push(...validate(evidence, written.some((f) => f.endsWith(".ts"))));

    // Only once the gates pass: a catalog that recorded reverted content would claim
    // the page holds something it does not.
    if (!failures.length && fix && written.length) updateCatalog(evidence.page, written, fix);

    let reverted = false;
    if (failures.length && written.length && options.revertOnFailure !== false) {
        // The files are tracked, so restoring them is exact — no need to reconstruct
        // what the agent overwrote.
        for (const file of written) run("git", ["checkout", "--", file]);
        reverted = true;
    }

    return {fix, written, failures, reverted};
};
