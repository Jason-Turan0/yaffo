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
import {applyFix, buildEvidence, openSession, triageShot} from "./index";
import {changedStrings, changesQuotedBy} from "./strings";
import type {StringChange} from "./strings";
import type {Triage, WalkthroughResult} from "./index";
import {createFilesystemClient} from "@lib/tool_providers/mcp_filesystem_client";
import {createPlaywrightClient} from "@lib/tool_providers/mcp_playwright_client";
import {localFilesystemMemoryToolFactory} from "@lib/tool_providers/local_filesystem_memory_tool";
import type {ToolProvider} from "@lib/tool_providers/toolprovider.types";
import type {ModelAlias} from "@lib/model_clients/model_client.interface";

// Authored spec, generated walkthroughs, and transient staging live in the
// content tree; this module is infrastructure and lives under lib/.
// The sandbox the walkthroughs were captured against; the agent inspects the same
// instance when deciding how a shot should be framed.
// The sandbox to drive. Deliberately its own variable: BASE_URL is overloaded in
// this repo — .env points it at the dev app on :5000 for other tooling, and a docs
// run against the wrong instance fails in confusing ways.
import {BASE_URL, CAPTURE_DIR, CONTENT_DIR, GUIDE_DIR, newRunLogDir, STAGING_DIR} from "./paths";

const REPORT = join(CAPTURE_DIR, "report.json");

const MARK: Record<Triage["classification"], string> = {
    intended_change: "✅",
    walkthrough_defect: "🔧",
    application_regression: "🐛",
    environment_instability: "🌫️",
};

const spec = (): {pages?: Record<string, {covers?: string}>} =>
    parseYaml(readFileSync(join(CONTENT_DIR, "spec.yaml"), "utf8"));

const covers = (page: string): string | undefined => spec()?.pages?.[page]?.covers;

/** The commit this page was last confirmed to match; null until it has been promoted. */
const watermark = (page: string): string | null => {
    const [area, name] = [page.slice(0, page.indexOf("/")), page.slice(page.indexOf("/") + 1)];
    const lock = join(CONTENT_DIR, area, name, `${name}.lock.json`);
    if (!existsSync(lock)) return null;
    return JSON.parse(readFileSync(lock, "utf8")).lastVerifiedSha ?? null;
};

const walkthroughPath = (page: string): string => {
    const [area, name] = [page.slice(0, page.indexOf("/")), page.slice(page.indexOf("/") + 1)];
    return join(CONTENT_DIR, area, name, `${name}.ts`);
};

const walkthroughSource = (page: string): string => {
    const path = walkthroughPath(page);
    return existsSync(path) ? readFileSync(path, "utf8") : "(no walkthrough on file)";
};

/**
 * An evidence packet for a page with no changed screenshot. Same shape as the visual
 * one so the fix turn needs no special case, with the image fields left empty.
 */
const proseEvidence = (page: string, changes: StringChange[]) => ({
    page,
    target: "",
    baselinePath: "",
    candidatePath: "",
    diffSummary: "No screenshot changed. This page names a control the app has renamed.",
    markdown: readFileSync(join(GUIDE_DIR, `${page}.md`), "utf8"),
    markdownPath: join(GUIDE_DIR, `${page}.md`),
    walkthroughPath: walkthroughPath(page),
    covers: covers(page),
    walkthroughSource: walkthroughSource(page),
    codeDiff: "(not gathered: this page was flagged by a catalogue change, not a code diff)",
    stringChanges: changes,
});

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

    // Detector B, per page. A renamed toast or button label may move no pixels at all,
    // so a page can be stale with nothing staged against it — without this, that whole
    // class of staleness never reaches the agent.
    const stringChanges = new Map<string, StringChange[]>();
    const diffCache = new Map<string, StringChange[]>();
    for (const page of Object.keys(spec().pages ?? {})) {
        const base = watermark(page);
        if (!base) continue;
        if (!diffCache.has(base)) diffCache.set(base, changedStrings(base));
        const changes = diffCache.get(base) as StringChange[];
        if (!changes.length) continue;
        const markdownPath = join(GUIDE_DIR, `${page}.md`);
        if (!existsSync(markdownPath)) continue;
        const quoted = changesQuotedBy(readFileSync(markdownPath, "utf8"), changes);
        if (quoted.length) stringChanges.set(page, quoted);
    }

    // Pages Detector B flagged that no staged shot already covers.
    const proseOnly = [...stringChanges.keys()]
        .filter((page) => !pending.some(({result}) => result.page === page));

    for (const result of results.filter((r) => r.error)) {
        // A walkthrough that threw produced no images, so there is nothing to look at;
        // it is a defect by construction rather than something to classify.
        console.log(`🔧 ${result.page}\n   walkthrough failed: ${result.error}`);
    }

    if (!pending.length && !proseOnly.length) {
        console.log(results.some((r) => r.error)
            ? "\nNo changed shots to triage."
            : "Nothing to triage — every shot matched what is committed.");
        return;
    }

    // Its own directory per run — see newRunLogDir.
    const runLogDir = newRunLogDir("heal-logs");
    console.log(`   logs: ${runLogDir}`);
    console.log(`Triaging ${pending.length} changed shot(s)` +
        (proseOnly.length ? `, and ${proseOnly.length} page(s) flagged by renamed controls` : "") + "\n");

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
            stagingDir: CAPTURE_DIR,
            walkthroughSource: walkthroughSource(result.page),
            walkthroughPath: walkthroughPath(result.page),
            covers: covers(result.page),
            stringChanges: stringChanges.get(result.page) ?? [],
        });

        // Memory is per page — the same scoping the generator uses for a feature.
        const [area, name] = [result.page.slice(0, result.page.indexOf("/")),
                              result.page.slice(result.page.indexOf("/") + 1)];
        const memory = apply
            // The factory appends "memories" itself; give it the page directory.
            ? localFilesystemMemoryToolFactory(join(CONTENT_DIR, area, name))
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
                if (outcome.attempts > 1) console.log(`   (${outcome.attempts} attempts)`);
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

    // Pages stale only because a control was renamed. There is no screenshot to
    // classify — the catalogue diff already establishes the staleness — so these skip
    // triage and go straight to the fix turn.
    for (const page of proseOnly) {
        const changes = stringChanges.get(page) as StringChange[];
        console.log(`✏️  ${page}`);
        for (const change of changes) {
            console.log(change.now !== undefined
                ? `   "${change.was}" is now "${change.now}"`
                : `   "${change.was}" no longer exists`);
        }
        if (!apply) {
            console.log("   → would update the page (re-run with --apply)\n");
            continue;
        }

        const [area, name] = [page.slice(0, page.indexOf("/")), page.slice(page.indexOf("/") + 1)];
        // The factory appends "memories" itself; give it the page directory.
        const memory = localFilesystemMemoryToolFactory(join(CONTENT_DIR, area, name));
        const evidence = proseEvidence(page, changes);
        try {
            const session = openSession(evidence, {
                model, runLogDir, toolProviders: [...providers, memory],
            });
            const outcome = await applyFix(session, evidence, {
                toolProviders: [...providers, memory], baseUrl: BASE_URL,
            });
            if (outcome.fix?.explanation) console.log(`   → ${outcome.fix.explanation}`);
            for (const file of outcome.written) console.log(`      wrote ${file}`);
            if (!outcome.written.length) console.log("      (no file changes returned)");
            if (outcome.attempts > 1) console.log(`   (${outcome.attempts} attempts)`);
            for (const failure of outcome.failures) console.error(`   ‼️  ${failure.split("\n")[0]}`);
            if (outcome.reverted) console.error("   → edits reverted; gates failed");
        } catch (e) {
            console.error(`   ❌ ${e instanceof Error ? e.message : String(e)}`);
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
