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
import {applyFix, buildEvidence, triageShot} from "./index";
import type {Triage, WalkthroughResult} from "./index";
import {createFilesystemClient} from "@lib/tool_providers/mcp_filesystem_client";
import {createPlaywrightClient} from "@lib/tool_providers/mcp_playwright_client";
import {localFilesystemMemoryToolFactory} from "@lib/tool_providers/local_filesystem_memory_tool";
import type {ToolProvider} from "@lib/tool_providers/toolprovider.types";
import type {ModelAlias} from "@lib/model_clients/model_client.interface";

// Authored spec, generated walkthroughs, and transient staging live in the
// content tree; this module is infrastructure and lives under lib/.
const CONTENT_DIR = resolve(join(process.cwd(), "user_doc_automation"));
const GUIDE_DIR = resolve(process.env.GUIDE_DIR || join(CONTENT_DIR, "..", "..", "docs", "guide"));
const STAGING_DIR = join(CONTENT_DIR, ".staging");
// The sandbox the walkthroughs were captured against; the agent inspects the same
// instance when deciding how a shot should be framed.
// The sandbox to drive. Deliberately its own variable: BASE_URL is overloaded in
// this repo — .env points it at the dev app on :5000 for other tooling, and a docs
// run against the wrong instance fails in confusing ways.
const BASE_URL = process.env.DOCS_BASE_URL || "http://127.0.0.1:5002";
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

const walkthroughPath = (page: string): string => {
    const [area, name] = [page.slice(0, page.indexOf("/")), page.slice(page.indexOf("/") + 1)];
    return join(CONTENT_DIR, area, name, `${name}.ts`);
};

const walkthroughSource = (page: string): string => {
    const path = walkthroughPath(page);
    return existsSync(path) ? readFileSync(path, "utf8") : "(no walkthrough on file)";
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

    // The same three providers the test generator gets, for the same reasons: the
    // filesystem to read the page and its walkthrough, Playwright to inspect the
    // running app when deciding how a shot should be framed, and memory so what it
    // learns about a page outlives the run.
    //
    // Only opened under --apply, since triage alone needs no tools.
    const providers: ToolProvider[] = [];
    if (apply) {
        providers.push(await createFilesystemClient([GUIDE_DIR, CONTENT_DIR], {readonly: false}));
        providers.push(await createPlaywrightClient({
            headless: true,
            baseUrl: BASE_URL,
            browser: "chromium",
            artifacts: {outputDir: runLogDir, saveVideo: false, saveSession: false},
        }));
    }

    const verdicts: Array<{page: string; target: string; triage: Triage}> = [];
    for (const {result, shot} of pending) {
        const evidence = buildEvidence(result, shot, {
            guideDir: GUIDE_DIR,
            stagingDir: STAGING_DIR,
            walkthroughSource: walkthroughSource(result.page),
            walkthroughPath: walkthroughPath(result.page),
            covers: covers(result.page),
        });

        // Memory is per page — the same scoping the generator uses for a feature.
        const [area, name] = [result.page.slice(0, result.page.indexOf("/")),
                              result.page.slice(result.page.indexOf("/") + 1)];
        const memory = apply
            ? localFilesystemMemoryToolFactory(join(CONTENT_DIR, area, name, "memories"))
            : undefined;
        const pageProviders = memory ? [...providers, memory] : providers;

        let session;
        try {
            session = await triageShot(evidence, {model, runLogDir, toolProviders: pageProviders});
        } catch (e) {
            console.error(`  ❌ ${shot.target}: ${e instanceof Error ? e.message : String(e)}`);
            continue;
        }
        const triage: Triage = session.triage;
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
                // Promote first: the fix turn reads the page against the screenshot
                // that is actually going to ship.
                mkdirSync(dirname(committed), {recursive: true});
                copyFileSync(shot.staged, committed);
                console.log("   → promoted");

                const outcome = await applyFix(session, evidence, {toolProviders: pageProviders, baseUrl: BASE_URL});
                if (outcome.fix) {
                    if (outcome.fix.explanation) console.log(`   → ${outcome.fix.explanation}`);
                    for (const file of outcome.written) console.log(`      wrote ${file}`);
                    if (!outcome.written.length) console.log("      (no file changes returned)");
                    
                } else {
                    console.log("   → no edits returned");
                }
                for (const failure of outcome.failures) {
                    console.error(`   ‼️  ${failure.split("\n")[0]}`);
                }
                if (outcome.reverted) console.error("   → edits reverted; gates failed");
            } else {
                console.log("   → would promote and update the page (re-run with --apply)");
            }
        } else {
            console.log(`   → ${triage.recommendedAction}: left for a human, nothing written`);
        }
        console.log();
    }

    await Promise.all(providers.map((provider) => provider.disconnect()));

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
