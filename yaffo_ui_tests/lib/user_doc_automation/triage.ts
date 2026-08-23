import {readFileSync} from "fs";
import {z} from "zod";
import {zodToJsonSchema} from "zod-to-json-schema";
import {
    createModelClient,
    DEFAULT_MODEL,
    supportsNativeStructuredOutput,
} from "@lib/model_clients/model_client_factory";
import {extractJson} from "@lib/test_generator/prompt/json_parser";
import type {ModelAlias} from "@lib/model_clients/model_client.interface";
import {
    supportsVision,
    toImagePart,
    toTextPart,
    visionModelFor,
} from "@lib/model_clients/model_client.interface";
import type {Evidence} from "./evidence";

/**
 * What kind of change this is — not "why did it break".
 *
 * The asymmetry with test healing: here a diff is usually *correct*. The UI changed
 * on purpose and the docs should follow. Classification exists mainly to separate
 * that ordinary case from the three that must not be documented.
 */
export const TRIAGE_CLASSES = [
    "intended_change",
    "walkthrough_defect",
    "application_regression",
    "environment_instability",
] as const;

export const TriageSchema = z.object({
    classification: z.enum(TRIAGE_CLASSES),
    confidence: z.enum(["high", "medium", "low"]),
    /** One line, for the run output and the PR body. */
    summary: z.string(),
    /** What in the images or diffs led to the classification. */
    reasoning: z.string(),
    /** Sentences in the page whose accuracy the change undermines. */
    proseImpact: z.array(z.object({
        quote: z.string(),
        issue: z.string(),
    })),
    recommendedAction: z.enum(["promote", "fix_walkthrough", "report_regression", "quarantine"]),
});

export type Triage = z.infer<typeof TriageSchema>;

const SYSTEM_PROMPT = `You triage screenshot changes for a user guide that documents a
local photo-organizer app called Yaffo.

A screenshot has been recaptured and differs from the one committed in the guide. Decide
what kind of change it is. Look at the images — the baseline, the new capture, and an
overlay that paints the differing pixels magenta over a dimmed copy of the baseline.

Classify as exactly one of:

- intended_change — the app's UI legitimately changed and the guide should adopt the new
  screenshot. This is the ordinary case. Choose it when the new capture looks correct and
  the change is consistent with the code diff you were given.
- walkthrough_defect — the capture script no longer drives the app correctly: it landed on
  the wrong page or state, a filter or view is not what the shot intends, content is
  missing because a selector or wait broke. The app is fine; the capture is wrong.
- application_regression — the new capture shows something broken: an error state, missing
  or broken images, collapsed or overlapping layout, placeholder content where real content
  belongs. Never adopt a screenshot of a bug into the manual.
- environment_instability — the difference comes from the test fixture or environment
  rather than the product: different seeded media, non-reproducible content such as live
  map tiles, or noise that would differ again on the next run.

Then check the page's prose against the new screenshot. Report any sentence whose accuracy
the change undermines — a renamed control, a changed count, a described element that is no
longer present. Quote the sentence exactly as it appears. Report nothing if the prose still
holds; do not invent problems.

Be concrete and brief. Prefer "low" confidence over a confident guess.`;

const section = (title: string, body: string): string => `\n## ${title}\n\n${body}\n`;

const buildPrompt = (evidence: Evidence): string => [
    `A screenshot on the guide page "${evidence.page}" changed.`,
    section("Shot", `${evidence.target}\n${evidence.diffSummary}`),
    evidence.covers ? section("What this page is obliged to cover", evidence.covers) : "",
    section("Page markdown", evidence.markdown),
    section("Walkthrough that captured it", evidence.walkthroughSource),
    section("Changes to this page's observed dependencies", evidence.codeDiff),
    section("Changes to the user-visible string catalogue", evidence.catalogDiff),
    "\nThe images follow: the committed baseline, the new capture, then the diff overlay.",
].join("");

const imagePart = (path: string) =>
    toImagePart(new Uint8Array(readFileSync(path)), path.endsWith(".png") ? "image/png" : "image/webp");

export interface TriageOptions {
    model?: ModelAlias;
    runLogDir: string;
}

export const triageShot = async (
    evidence: Evidence,
    options: TriageOptions
): Promise<Triage | undefined> => {
    const requested = options.model ?? (DEFAULT_MODEL as ModelAlias);
    // Triage classifies a picture. A model that cannot receive one does not fail — it
    // answers from the surrounding text and sounds just as certain, which is worse
    // than an error. DeepSeek splits vision into a separate model, so substitute it
    // rather than refusing outright.
    const model = visionModelFor(requested);
    if (model !== requested) {
        console.log(`  ${requested} cannot receive images; using ${model} instead.`);
    }
    if (!supportsVision(model)) {
        throw new Error(
            `${model} cannot receive images, so it would classify this change without ` +
            `seeing it. Choose a model marked true in MODEL_VISION_SUPPORT.`);
    }

    const systemPrompt = supportsNativeStructuredOutput(model)
        ? SYSTEM_PROMPT
        : `${SYSTEM_PROMPT}\n\nRespond with JSON matching this schema and nothing else:\n` +
          `${JSON.stringify(zodToJsonSchema(TriageSchema), null, 2)}`;

    const client = createModelClient(
        options.runLogDir,
        model,
        systemPrompt,
        [],            // no tools: the whole evidence packet is in the prompt
        TriageSchema,
    );

    // Each image is labelled by the text part before it, so the model is never left
    // guessing which capture it is looking at.
    client.addUserMessage([
        toTextPart(buildPrompt(evidence)),
        toTextPart("Committed baseline:"),
        imagePart(evidence.baselinePath),
        toTextPart("New capture:"),
        imagePart(evidence.candidatePath),
        ...(evidence.overlayPath
            ? [toTextPart("Diff overlay (magenta = changed pixels):"), imagePart(evidence.overlayPath)]
            : []),
    ]);

    const response = await client.callModelApi();
    if (!response) {
        throw new Error(client.lastError ?? "the model returned no response");
    }
    // Tolerant extraction: a non-native provider may wrap the JSON in prose or a
    // fenced block.
    const parsed = TriageSchema.safeParse(JSON.parse(extractJson(response.text)));
    if (!parsed.success) {
        throw new Error(`unparseable triage response: ${parsed.error.message}`);
    }
    return parsed.data;
};
