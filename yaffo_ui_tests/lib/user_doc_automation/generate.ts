import {existsSync, readFileSync, mkdirSync, writeFileSync} from "fs";
import {dirname, join, relative, resolve} from "path";
import {z} from "zod";
import {zodToJsonSchema} from "zod-to-json-schema";
import {
    createModelClient,
    DEFAULT_MODEL,
    supportsNativeStructuredOutput,
} from "@lib/model_clients/model_client_factory";
import {supportsVision, toTextPart, visionModelFor} from "@lib/model_clients/model_client.interface";
import type {ModelAlias} from "@lib/model_clients/model_client.interface";
import type {ToolProvider} from "@lib/tool_providers/toolprovider.types";
import {parseAnswer, runToolLoop} from "./tool_loop";

/**
 * Output budget for a generate turn. Two whole files plus the reasoning that decides
 * what goes in them; the default is sized for a short structured answer.
 */
export const GENERATE_MAX_OUTPUT_TOKENS = 48000;

/** Generation reads far more of the app than a fix does, so it gets more turns. */
const MAX_GENERATE_ROUNDS = 45;

/**
 * Write a guide page and the walkthrough that captures its screenshots.
 *
 * The specification is the page's charter in spec.yaml — what it is *obliged* to
 * cover. From that the agent decides what the page should say, which screenshots it
 * needs, and how to capture them, by reading the app and driving the running sandbox.
 *
 * The two outputs must agree: every image the markdown references has to be produced
 * by the walkthrough, or the docs build fails on a missing file. That mutual
 * consistency is what the verification gate checks.
 *
 * Output follows the same convention as the test generator and the fix turn: whole
 * files returned as the model's answer, written by this module.
 */
export const GenerateSchema = z.object({
    files: z.array(z.object({
        filename: z.string().describe(
            "Repo-relative path. The walkthrough belongs at " +
            "yaffo_ui_tests/user_doc_automation/{area}/{page}/{page}.ts"),
        code: z.string().describe("The complete file contents"),
        description: z.string().optional().describe("What this file does"),
    })).describe("The walkthrough, and any helper it needs"),
    pageContext: z.string().optional().describe(
        "What a future run should know about this page: state that must be pinned, " +
        "controls that cannot be driven the obvious way, fixture quirks"),
    explanation: z.string().optional().describe("How the walkthrough was arrived at"),
    confidence: z.number().optional().describe("Confidence, from 0 (low) to 1 (high)"),
}).strict();

export type Generated = z.infer<typeof GenerateSchema>;

/** A shot the page needs, taken from its own markdown. */
export interface RequiredShot {
    /** Filename the walkthrough must produce, e.g. gallery-home.webp */
    filename: string;
    /** The page's own caption — what the shot has to show. */
    alt: string;
}

/**
 * What the page asks for. Image references are the specification: the path names the
 * shot and the alt text says what it must depict.
 */
export const requiredShots = (markdown: string): RequiredShot[] =>
    [...markdown.matchAll(/!\[([^\]]*)\]\(([^)]+)\)/g)].map((match) => ({
        alt: match[1],
        filename: match[2].split("/").pop() as string,
    }));

const SYSTEM_PROMPT = `You write capture walkthroughs for a user guide documenting a local
photo-organizer app called Yaffo.

You write two things per page: the page itself, and the walkthrough that captures its
screenshots. A walkthrough is deterministic TypeScript — it declares the screenshots the
page shows and drives the app to produce them, and it drives the flows the page describes
but does not illustrate, so their routes and templates are recorded as that page's
dependencies.

Write documentation a reader can follow: what the screen is for, what each control does,
and what to do when it does not behave as expected. Describe only what you have actually
seen in the app.

Work out how to reach each view by reading the app's routes and templates and by driving
the running sandbox with the browser tools. Prefer a URL that pins state over clicking
through to it: anything persisted server-side will otherwise leak between runs.`;

const buildPrompt = (options: {
    page: string;
    covers?: string;
    markdown: string;
    shots: RequiredShot[];
    reference: string;
    types: string;
    target: string;
    baseUrl: string;
    sandboxFacts: string;
    memoryNote: string;
    appRoot: string;
    markdownPath: string;
    markdownTarget: string;
    area: string;
    name: string;
}): string => `
Write the walkthrough for the guide page **${options.page}**.

## What the page is obliged to cover

${options.covers ?? "(no charter recorded — use the existing page as the guide)"}

This is the specification. The page must cover it; anything it already covers well
should be left alone.

## The page as it stands

${options.markdown || "(this page does not exist yet)"}

${options.shots.length
    ? `It currently references these screenshots:\n\n${options.shots
        .map((shot) => `- \`${shot.filename}\` — ${shot.alt}`).join("\n")}`
    : "It references no screenshots yet."}

## Screenshots

You decide which screenshots the page needs. For each one:

- the walkthrough declares it by filename, and the runner writes it to
  \`docs/guide/${options.area}/assets/${options.name}/<filename>\`;
- the markdown references it **relative to the page**, as
  \`![alt](assets/${options.name}/<filename>)\`;
- the alt text says what the reader is looking at.

The two must match exactly. A reference with no file behind it fails the docs build,
and a captured file nothing references is dead weight.

## The types you are writing against

\`\`\`typescript
${options.types}
\`\`\`

## A worked example — the reference walkthrough for another page

\`\`\`typescript
${options.reference}
\`\`\`


${options.sandboxFacts}

## Tools — all read-only

You cannot write files. Edits are returned as your answer and written for you.

- **memory** — this page's notes. ${options.memoryNote}
- **filesystem** — read the app to find selectors and URLs. Absolute paths, so you do
  not have to search for them:
  - routes: \`${options.appRoot}/routes/\`
  - templates: \`${options.appRoot}/templates/\`
  - static JS/CSS: \`${options.appRoot}/static/\`
  - this page: \`${options.markdownPath}\`
- **browser** — the running app is at ${options.baseUrl}. Drive it to confirm a
  selector exists and a shot frames well before committing to it.

Start from the templates and routes. Do not search the filesystem to find them.

## What matters

- Clip to the element that carries the meaning, never the whole page.
- Pin every piece of server-persisted state in the URL. The library view is the known
  example: it is saved server-side and rewritten by the timeline scrubber.
- Grids should end on a whole row.
- \`flows\` should drive the sections the page describes but does not picture, so their
  templates are recorded. A page with no shots still needs them.
- Comment *why*, not what — especially anything non-obvious you discovered.

## Your answer

Return **both** complete files. Reply with JSON only:

{"files": [
   {"filename": "${options.markdownTarget}", "code": "<the entire guide page>",
    "description": "what the page covers"},
   {"filename": "${options.target}", "code": "<the entire walkthrough>",
    "description": "what it captures"}],
 "pageContext": "what a future run should know about this page",
 "explanation": "how you worked it out",
 "confidence": 0.8}

Whole files, not fragments — partial content overwrites the rest with nothing. The
guide is hard-wrapped at 80 columns; keep it that way.
`.trim();

export interface GenerateOptions {
    model?: ModelAlias;
    runLogDir: string;
    toolProviders: ToolProvider[];
    baseUrl: string;
    /** docs/guide */
    guideDir: string;
    /** yaffo_ui_tests/user_doc_automation */
    contentDir: string;
    covers?: string;
    /** Whether this page's memories already hold anything. */
    hasMemories: boolean;
    /** Runtime facts about the sandbox, rendered into the prompt. */
    sandboxFacts?: string;
    /**
     * Correctness gates. Failures are handed back to the model to fix rather than
     * ending the run; see `Verify`.
     */
    verify?: Verify;
    /** Overrides MAX_FIX_ATTEMPTS, mainly for tests. */
    maxFixAttempts?: number;
}

export interface GenerateResult {
    generated?: Generated;
    written: string[];
    errors: string[];
    /** Model turns spent, including the first. 1 means it was right first time. */
    attempts: number;
}

/**
 * Correctness gates, run against what was just written. Empty means it passed.
 *
 * Returning failures rather than throwing is what lets them be handed back to the
 * model: the generator's job is to produce something that compiles and captures, and
 * a compiler error is information it can act on, not a reason to give up.
 */
export type Verify = (written: string[]) => string[] | Promise<string[]>;

/** How many times to hand failures back before giving up, after the first attempt. */
export const MAX_FIX_ATTEMPTS = 3;

/**
 * Ask for a corrected answer, in the same session so the model still has the page, the
 * tools it used, and its own previous attempt in context. Mirrors
 * `addCompileErrorMessage` in the test generator, for the same reason: a fresh session
 * would have to rediscover everything before it could fix anything.
 */
const buildFixPrompt = (failures: string[]): string => `
What you returned did not pass. Fix it and return the complete files again.

${failures.map((failure) => `- ${failure}`).join("\n")}

Return the same JSON shape as before, with every file you want written — whole files,
not fragments. Change only what these failures require; the rest of your answer was
accepted.
`.trim();

const REPO = resolve(join(process.cwd(), ".."));

export const generateWalkthrough = async (
    page: string,
    options: GenerateOptions
): Promise<GenerateResult> => {
    const [area, name] = [page.slice(0, page.indexOf("/")), page.slice(page.indexOf("/") + 1)];
    const markdownPath = join(options.guideDir, `${page}.md`);
    if (!existsSync(markdownPath)) {
        return {written: [], errors: [`no such page: ${page}.md`], attempts: 0};
    }

    const markdown = readFileSync(markdownPath, "utf8");
    const target = relative(REPO, join(options.contentDir, area, name, `${name}.ts`));

    const requested = options.model ?? (DEFAULT_MODEL as ModelAlias);
    const model = visionModelFor(requested);
    if (!supportsVision(model)) {
        // Framing a screenshot is a visual judgement, so the same rule as triage.
        return {written: [], errors: [`${model} cannot see, so it cannot judge framing`],
                attempts: 0};
    }

    const schemaHint = supportsNativeStructuredOutput(model)
        ? SYSTEM_PROMPT
        : `${SYSTEM_PROMPT}\n\nRespond with JSON matching this schema and nothing else:\n` +
          `${JSON.stringify(zodToJsonSchema(GenerateSchema), null, 2)}`;

    const client = createModelClient(
        options.runLogDir, model, schemaHint,
        options.toolProviders.flatMap((provider) => provider.getTools()),
        GenerateSchema,
    );
    // This turn returns two complete files — a guide page and its walkthrough — and on
    // a reasoning model the budget is spent on hidden reasoning first. At the default
    // a DeepSeek run was observed burning all 16000 tokens on reasoning and returning
    // an empty string, which then surfaced as "response was not JSON".
    client.setMaxOutputTokens(GENERATE_MAX_OUTPUT_TOKENS);

    client.addUserMessage([toTextPart(buildPrompt({
        page,
        covers: options.covers,
        markdown,
        shots: requiredShots(markdown),
        reference: readFileSync(join(options.contentDir, "library-basics", "browsing-filtering",
            "browsing-filtering.ts"), "utf8"),
        types: readFileSync(join(REPO, "yaffo_ui_tests", "lib", "user_doc_automation", "types.ts"), "utf8"),
        target,
        baseUrl: options.baseUrl,
        appRoot: join(REPO, "yaffo"),
        markdownPath,
        markdownTarget: relative(REPO, markdownPath),
        area,
        name,
        sandboxFacts: options.sandboxFacts ?? "",
        memoryNote: options.hasMemories
            ? "**Read them first** — earlier runs left notes on this page."
            : "Empty so far. Record anything worth not rediscovering.",
    }))]);

    const maxAttempts = (options.maxFixAttempts ?? MAX_FIX_ATTEMPTS) + 1;
    let generated: Generated | undefined;
    let errors: string[] = [];
    let written: string[] = [];
    let attempts = 0;

    // Ask, write, check, and hand any failure back — the same loop the test generator
    // runs. A compile error or a walkthrough that captures nothing is information the
    // model can act on, and it still has the page, the tools, and its own previous
    // answer in context. Erroring out instead throws all of that away and leaves a
    // reverted tree for a human to start over from.
    while (attempts < maxAttempts) {
        attempts++;
        generated = undefined;
        errors = [];

        try {
            const answer = await runToolLoop(client, options.toolProviders, MAX_GENERATE_ROUNDS);
            const parsed = parseAnswer(GenerateSchema, answer);
            generated = parsed.value;
            errors = parsed.errors;
        } catch (e) {
            errors = [e instanceof Error ? e.message : String(e)];
        }

        written = [];
        for (const file of generated?.files ?? []) {
            const absolute = resolve(REPO, file.filename);
            const owned = absolute.startsWith(join(options.contentDir, area, name))
                || absolute === resolve(markdownPath);
            if (!owned) {
                errors.push(`refused to write outside the page's own files: ${file.filename}`);
                continue;
            }
            mkdirSync(dirname(absolute), {recursive: true});
            writeFileSync(absolute, file.code);
            written.push(file.filename);
        }

        // The gates only mean anything once something was written.
        if (!errors.length && written.length && options.verify) {
            errors = await options.verify(written);
        }

        if (!errors.length) break;

        if (attempts < maxAttempts) {
            console.log(`   ↻ attempt ${attempts} failed; handing ${errors.length} ` +
                `failure(s) back to the model`);
            for (const failure of errors) console.log(`      - ${failure.split("\n")[0]}`);
            client.addUserMessage([toTextPart(buildFixPrompt(errors))]);
        }
    }

    // Seed the catalog the same way a generated test's is seeded, so the fix turn has
    // something to keep in step. Only on success: a catalog describing files that are
    // about to be reverted would claim the page holds something it does not.
    if (generated && written.length && !errors.length) {
        const catalog = {
            files: generated.files.filter((file) => written.includes(file.filename)),
            pageContext: generated.pageContext ?? "",
            explanation: generated.explanation ?? "",
            confidence: generated.confidence ?? null,
        };
        writeFileSync(join(options.contentDir, area, name, `${name}.json`),
            JSON.stringify(catalog, null, 4) + "\n");
    }

    return {generated, written, errors, attempts};
};
