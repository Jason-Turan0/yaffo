/**
 * Triage the changes a capture run found, and act on them.
 *
 *   npm run docs:capture           # capture, compare, stage
 *   npm run docs:heal              # triage what changed
 *   npm run docs:heal -- --apply   # …and act on it
 *
 * Reads the staging report rather than recapturing, so triage always judges the same
 * images a human can open. Without --apply nothing is written.
 *
 * Unlike test healing, the common verdict here is "the app changed on purpose, adopt
 * the new screenshot" — see "The agentic loop" in docs/documentation_automation.md.
 */
import "dotenv/config";
import {copyFileSync, existsSync, mkdirSync, readFileSync, writeFileSync} from "fs";
import {dirname, join, resolve} from "path";
import {parse as parseYaml} from "yaml";
import {buildEvidence} from "./index";
import type {Triage, WalkthroughResult} from "./index";
import {triageShot} from "./index";
import type {ModelAlias} from "@lib/model_clients/model_client.interface";

// Authored spec, generated walkthroughs, and transient staging live in the
// content tree; this module is infrastructure and lives under lib/.
const CONTENT_DIR = resolve(join(process.cwd(), "user_doc_automation"));
const GUIDE_DIR = resolve(process.env.GUIDE_DIR || join(CONTENT_DIR, "..", "..", "docs", "guide"));
const STAGING_DIR = join(CONTENT_DIR, ".staging");
const REPORT = join(STAGING_DIR, "report.json");

const MARK: Record<Triage["classification"], string> = {
    intended_change: "✅",
    walkthrough_defect: "🔧",
    application_regression: "🐛",
    environment_instability: "🌫️",
};

const covers = (page: string): string | undefined => {
    const spec = parseYaml(readFileSync(join(CONTENT_DIR, "spec.yaml"), "utf8"));
    return spec?.pages?.[page]?.covers;
};

const walkthroughSource = (page: string): string => {
    const spec = parseYaml(readFileSync(join(CONTENT_DIR, "spec.yaml"), "utf8"));
    const name = spec?.pages?.[page]?.walkthrough;
    const path = name ? join(CONTENT_DIR, "walkthroughs", `${name}.ts`) : undefined;
    return path && existsSync(path) ? readFileSync(path, "utf8") : "(no walkthrough on file)";
};

const main = async (): Promise<void> => {
    const args = process.argv.slice(2);
    const apply = args.includes("--apply");
    const modelArg = args.indexOf("--model");
    const model = modelArg !== -1 ? (args[modelArg + 1] as ModelAlias) : undefined;

    if (!existsSync(REPORT)) {
        console.error(`No capture to triage at ${REPORT}.\nRun: npm run docs:capture`);
        process.exit(1);
    }
    const results = JSON.parse(readFileSync(REPORT, "utf8")).results as WalkthroughResult[];

    const pending = results.flatMap((result) =>
        result.shots.filter((shot) => shot.status !== "unchanged").map((shot) => ({result, shot})));

    for (const result of results.filter((r) => r.error)) {
        // A walkthrough that threw produced no images, so there is nothing to look at;
        // it is a defect by construction rather than something to classify.
        console.log(`🔧 ${result.page}\n   walkthrough failed: ${result.error}`);
    }

    if (!pending.length) {
        console.log(results.some((r) => r.error)
            ? "\nNo changed shots to triage."
            : "Nothing to triage — every shot matched what is committed.");
        return;
    }

    const runLogDir = join(STAGING_DIR, "heal-logs");
    mkdirSync(runLogDir, {recursive: true});
    console.log(`Triaging ${pending.length} changed shot(s)\n`);

    const verdicts: Array<{page: string; target: string; triage: Triage}> = [];
    for (const {result, shot} of pending) {
        const evidence = buildEvidence(result, shot, {
            guideDir: GUIDE_DIR,
            stagingDir: STAGING_DIR,
            walkthroughSource: walkthroughSource(result.page),
            covers: covers(result.page),
        });

        let triage: Triage | undefined;
        try {
            triage = await triageShot(evidence, {model, runLogDir});
        } catch (e) {
            console.error(`  ❌ ${shot.target}: ${e instanceof Error ? e.message : String(e)}`);
            continue;
        }
        if (!triage) continue;
        verdicts.push({page: result.page, target: shot.target, triage});

        console.log(`${MARK[triage.classification]} ${shot.target}`);
        console.log(`   ${triage.classification} (${triage.confidence} confidence) — ${triage.summary}`);
        console.log(`   ${triage.reasoning.replace(/\n/g, "\n   ")}`);
        for (const impact of triage.proseImpact) {
            console.log(`   prose: "${impact.quote}"\n          ${impact.issue}`);
        }

        if (triage.recommendedAction === "promote") {
            const committed = join(GUIDE_DIR, shot.target);
            if (apply) {
                mkdirSync(dirname(committed), {recursive: true});
                copyFileSync(shot.staged, committed);
                console.log("   → promoted");
            } else {
                console.log("   → would promote (re-run with --apply)");
            }
        } else {
            console.log(`   → ${triage.recommendedAction}: left for a human, nothing written`);
        }
        console.log();
    }

    writeFileSync(join(STAGING_DIR, "triage.json"),
        JSON.stringify({triagedAt: new Date().toISOString(), verdicts}, null, 2));

    const blocking = verdicts.filter((v) => v.triage.classification !== "intended_change");
    console.log(`${verdicts.length} triaged, ${blocking.length} needing a human.`);
    if (blocking.length) process.exit(2);
};

main().catch((e) => {
    console.error(e);
    process.exit(1);
});
