/**
 * Triage the changes a capture run found, and act on them.
 *
 *   npm run docs:capture           # capture, compare, stage
 *   npm run docs:heal              # triage what changed
 *   npm run docs:heal -- --apply   # …and act on it
 *   npm run docs:heal -- --page library-basics/browsing-filtering --apply --docker
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
import {pathToFileURL} from "url";
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
import {YAFFO_APP_ROOT} from "@lib/types";
import {changedDependencies} from "./dependency_changes";
import type {DependencyChange, PageLock} from "./dependency_changes";

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

/**
 * Environment noise below this fraction may become the new baseline when the model
 * confirms that it has no semantic or prose impact. 0.001 is 0.1% of the image.
 */
export const MINOR_VARIATION_MAX_RATIO = 0.001;

export const acceptMinorEnvironmentVariation = (
    triage: Triage,
    shot: WalkthroughResult["shots"][number]
): Triage => {
    const diff = shot.diff;
    if (
        triage.classification !== "environment_instability" ||
        triage.recommendedAction !== "quarantine" ||
        triage.proseImpact.length > 0 ||
        diff?.reason === "size" ||
        diff?.ratio === undefined ||
        diff.ratio > MINOR_VARIATION_MAX_RATIO
    ) return triage;

    return {
        ...triage,
        recommendedAction: "promote",
        reasoning: `${triage.reasoning}\n` +
            `Accepted as a minor environment variation: ` +
            `${(diff.ratio * 100).toFixed(4)}% of pixels changed, within the 0.1% limit, ` +
            "with no reframing or prose impact.",
    };
};

const spec = (): {pages?: Record<string, {covers?: string; also_depends_on?: string[]}>} =>
    parseYaml(readFileSync(join(CONTENT_DIR, "spec.yaml"), "utf8"));

const covers = (page: string): string | undefined => spec()?.pages?.[page]?.covers;

const pageLock = (page: string): PageLock | null => {
    const [area, name] = [page.slice(0, page.indexOf("/")), page.slice(page.indexOf("/") + 1)];
    const lock = join(CONTENT_DIR, area, name, `${name}.lock.json`);
    if (!existsSync(lock)) return null;
    return JSON.parse(readFileSync(lock, "utf8")) as PageLock;
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
const proseEvidence = (
    page: string,
    changes: StringChange[],
    dependencies: DependencyChange[]
) => ({
    page,
    target: "",
    baselinePath: "",
    candidatePath: "",
    diffSummary: dependencies.length
        ? "No screenshot changed, but one or more page dependency fingerprints changed."
        : "No screenshot changed. This page names a control the app has renamed.",
    markdown: readFileSync(join(GUIDE_DIR, `${page}.md`), "utf8"),
    markdownPath: join(GUIDE_DIR, `${page}.md`),
    walkthroughPath: walkthroughPath(page),
    covers: covers(page),
    walkthroughSource: walkthroughSource(page),
    codeDiff: dependencies.length
        ? `Dependency contents changed:\n${dependencies.map(({path}) => `- ${path}`).join("\n")}`
        : "(not gathered: this page was flagged by a catalogue change, not a code diff)",
    stringChanges: changes,
});

const walkthroughFailureEvidence = (result: WalkthroughResult) => ({
    page: result.page,
    target: "",
    baselinePath: "",
    candidatePath: "",
    walkthroughError: result.error as string,
    diffSummary: `Walkthrough failed before capture completed:\n${result.error}`,
    markdown: readFileSync(join(GUIDE_DIR, `${result.page}.md`), "utf8"),
    markdownPath: join(GUIDE_DIR, `${result.page}.md`),
    walkthroughPath: walkthroughPath(result.page),
    covers: covers(result.page),
    walkthroughSource: walkthroughSource(result.page),
    codeDiff: "(capture failed; inspect the walkthrough and running app)",
    stringChanges: [],
});

interface Verdict {
    page: string;
    target: string;
    triage: Triage;
    /** True when a non-promote action was completed and passed every gate. */
    resolved?: boolean;
}

export const main = async (args: string[] = process.argv.slice(2)): Promise<number> => {
    const apply = args.includes("--apply");
    const useDocker = args.includes("--docker");
    const pageArg = args.indexOf("--page");
    const selectedPage = pageArg !== -1 ? args[pageArg + 1] : undefined;
    if (pageArg !== -1 && !selectedPage) {
        console.error("--page requires a page id");
        return 1;
    }
    const modelArg = args.indexOf("--model");
    const model = modelArg !== -1 ? (args[modelArg + 1] as ModelAlias) : undefined;

    if (!existsSync(REPORT)) {
        console.error(`No capture to triage at ${REPORT}.\nRun: npm run docs:capture`);
        return 1;
    }
    let results = JSON.parse(readFileSync(REPORT, "utf8")).results as WalkthroughResult[];
    if (selectedPage) {
        results = results.filter((result) => result.page === selectedPage);
        if (!results.length) {
            console.error(`Capture report has no result for ${selectedPage}.`);
            return 1;
        }
    }

    const failedCaptures = results.filter((result) => result.error);
    const unstableCaptures = results.flatMap((result) => result.shots
        .filter((shot) => shot.status === "unstable")
        .map((shot) => ({result, shot})));
    // Do not promote partial shots from a walkthrough that later failed. Repair and
    // verify the whole walkthrough first; its gate will perform a clean promotion.
    const pending = results.flatMap((result) => result.error
        ? []
        : result.shots
            .filter((shot) => shot.status === "new" || shot.status === "changed")
            .map((shot) => ({result, shot})));

    // Cheap per-page checks. A renamed label or changed prose dependency may move no
    // pixels at all, so a page can be stale with nothing staged against it.
    const stringChanges = new Map<string, StringChange[]>();
    const dependencyChanges = new Map<string, DependencyChange[]>();
    const diffCache = new Map<string, StringChange[]>();
    for (const [page, entry] of Object.entries(spec().pages ?? {})) {
        if (selectedPage && page !== selectedPage) continue;
        const lock = pageLock(page);
        if (lock) {
            const changed = changedDependencies(lock, entry.also_depends_on ?? []);
            if (changed.length) dependencyChanges.set(page, changed);
        }
        const base = lock?.lastVerifiedSha ?? null;
        if (!base) continue;
        if (!diffCache.has(base)) diffCache.set(base, changedStrings(base));
        const changes = diffCache.get(base) as StringChange[];
        if (!changes.length) continue;
        const markdownPath = join(GUIDE_DIR, `${page}.md`);
        if (!existsSync(markdownPath)) continue;
        const quoted = changesQuotedBy(readFileSync(markdownPath, "utf8"), changes);
        if (quoted.length) stringChanges.set(page, quoted);
    }

    // Pages a non-visual check flagged that no staged shot already covers.
    const proseOnly = [...new Set([...stringChanges.keys(), ...dependencyChanges.keys()])]
        .filter((page) => !pending.some(({result}) => result.page === page))
        .filter((page) => !failedCaptures.some((result) => result.page === page));

    if (!pending.length && !proseOnly.length && !failedCaptures.length && !unstableCaptures.length) {
        console.log("Nothing to triage — every shot matched what is committed.");
        return 0;
    }

    // Its own directory per run — see newRunLogDir.
    const runLogDir = newRunLogDir("heal-logs");
    console.log(`   logs: ${runLogDir}`);
    console.log(`Triaging ${pending.length} changed shot(s)` +
        (proseOnly.length ? `, ${proseOnly.length} page(s) flagged by renamed controls` : "") +
        (failedCaptures.length ? `, ${failedCaptures.length} failed walkthrough(s)` : "") +
        (unstableCaptures.length ? `, ${unstableCaptures.length} unstable shot(s)` : "") + "\n");

    // The same three providers the test generator gets, for the same reasons: the
    // filesystem to read the page and its walkthrough, Playwright to inspect the
    // running app when deciding how a shot should be framed, and memory so what it
    // learns about a page outlives the run.
    //
    // Only opened under --apply, since triage alone needs no tools.
    const providers: ToolProvider[] = [];
    try {
        if (apply) {
            providers.push(await createFilesystemClient(
                [YAFFO_APP_ROOT, GUIDE_DIR, CONTENT_DIR], {readonly: false}));
            providers.push(await createPlaywrightClient({
                headless: true,
                baseUrl: BASE_URL,
                browser: "chromium",
                artifacts: {outputDir: runLogDir, saveVideo: false, saveSession: false},
            }));
        }

        const verdicts: Verdict[] = [];
        let failed = 0;
        for (const {result, shot} of unstableCaptures) {
            const diff = shot.stability?.diff;
            const detail = diff
                ? `${diff.diffPixels} pixel(s) differed between the two candidates`
                : shot.stability?.reason ?? "the second capture did not produce the shot";
            verdicts.push({
                page: result.page,
                target: shot.target,
                triage: {
                    classification: "environment_instability",
                    confidence: "high",
                    summary: "The screenshot change did not reproduce on an immediate recapture.",
                    reasoning: `${detail}. The committed image was left unchanged.`,
                    proseImpact: [],
                    recommendedAction: "quarantine",
                },
            });
            console.log(`🌫️ ${shot.target}`);
            console.log(`   environment_instability (high confidence) — change did not reproduce`);
            console.log(`   ${detail}; committed image left unchanged\n`);
        }

        for (const result of failedCaptures) {
            const evidence = walkthroughFailureEvidence(result);
            const triage: Triage = {
                classification: "walkthrough_defect",
                confidence: "high",
                summary: "The documentation walkthrough failed before capture completed.",
                reasoning: result.error as string,
                proseImpact: [],
                recommendedAction: "fix_walkthrough",
            };
            const verdict: Verdict = {
                page: result.page,
                target: walkthroughPath(result.page),
                triage,
            };
            verdicts.push(verdict);
            console.log(`🔧 ${result.page}`);
            console.log(`   walkthrough failed: ${result.error}`);

            if (!apply) {
                console.log("   → would send the failure to the repair agent (re-run with --apply)\n");
                continue;
            }

            const [area, name] = [result.page.slice(0, result.page.indexOf("/")),
                                  result.page.slice(result.page.indexOf("/") + 1)];
            const memory = localFilesystemMemoryToolFactory(join(CONTENT_DIR, area, name));
            const pageProviders = [...providers, memory];
            try {
                const session = openSession(evidence, {
                    model, runLogDir, toolProviders: pageProviders,
                });
                const outcome = await applyFix(session, evidence, {
                    toolProviders: pageProviders, baseUrl: BASE_URL, useDocker,
                });
                if (outcome.fix?.explanation) {
                    console.log(`   → ${outcome.fix.explanation}`);
                    triage.reasoning += `\n\nRepair agent: ${outcome.fix.explanation}`;
                }
                for (const file of outcome.written) console.log(`      wrote ${file}`);
                if (!outcome.written.length) console.log("      (no file changes returned)");
                if (outcome.attempts > 1) console.log(`   (${outcome.attempts} attempts)`);
                for (const failure of outcome.failures) {
                    console.error(`   ‼️  ${failure.split("\n")[0]}`);
                }
                verdict.resolved = !outcome.failures.length && !outcome.reverted;
                if (verdict.resolved) {
                    console.log("   → repaired and verified");
                } else {
                    console.error("   → walkthrough remains unresolved");
                    failed++;
                }
            } catch (e) {
                console.error(`   ❌ ${e instanceof Error ? e.message : String(e)}`);
                failed++;
            }
            console.log();
        }

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
                failed++;
                continue;
            }
            const triage = acceptMinorEnvironmentVariation(session.triage, shot);
            // The fix turn continues this session, so give it the effective action
            // rather than the model's pre-policy recommendation.
            session.triage = triage;
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

                    const outcome = await applyFix(session, evidence, {
                        toolProviders: pageProviders,
                        baseUrl: BASE_URL,
                        useDocker,
                    });
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
                    if (outcome.reverted) {
                        console.error("   → edits reverted; gates failed");
                        failed++;
                    }
                } else {
                    console.log("   → would promote and update the page (re-run with --apply)");
                }
            } else {
                console.log(`   → ${triage.recommendedAction}: left for a human, nothing written`);
            }
            console.log();
        }

        // Pages selected without a changed screenshot. The mechanical dependency or
        // catalogue comparison already establishes drift, so these skip visual triage
        // and go straight to the fix turn.
        for (const page of proseOnly) {
            const changes = stringChanges.get(page) ?? [];
            const dependencies = dependencyChanges.get(page) ?? [];
            console.log(`✏️  ${page}`);
            for (const dependency of dependencies) {
                console.log(`   dependency changed: ${dependency.path}`);
            }
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
            const evidence = proseEvidence(page, changes, dependencies);
            try {
                const session = openSession(evidence, {
                    model, runLogDir, toolProviders: [...providers, memory],
                });
                const outcome = await applyFix(session, evidence, {
                    toolProviders: [...providers, memory], baseUrl: BASE_URL, useDocker,
                });
                if (outcome.fix?.explanation) console.log(`   → ${outcome.fix.explanation}`);
                for (const file of outcome.written) console.log(`      wrote ${file}`);
                if (!outcome.written.length) console.log("      (no file changes returned)");
                if (outcome.attempts > 1) console.log(`   (${outcome.attempts} attempts)`);
                for (const failure of outcome.failures) console.error(`   ‼️  ${failure.split("\n")[0]}`);
                if (outcome.reverted) {
                    console.error("   → edits reverted; gates failed");
                    failed++;
                }
            } catch (e) {
                console.error(`   ❌ ${e instanceof Error ? e.message : String(e)}`);
                failed++;
            }
            console.log();
        }

        writeFileSync(join(STAGING_DIR, "triage.json"),
            JSON.stringify({triagedAt: new Date().toISOString(), verdicts}, null, 2));

        // Classification explains the source; action and resolution decide whether a
        // human is needed. Minor environment noise can promote without pretending it
        // was intentional, while a repaired walkthrough retains its honest class.
        const blocking = verdicts.filter((v) =>
            v.triage.recommendedAction !== "promote" && !v.resolved);
        console.log(`${verdicts.length} triaged, ${blocking.length} unresolved.`);
        return blocking.length ? 2 : failed ? 1 : 0;
    } finally {
        await Promise.all(providers.map((provider) => provider.disconnect()));
    }
};

export const runCli = async (args: string[] = process.argv.slice(2)): Promise<void> => {
    try {
        process.exitCode = await main(args);
    } catch (e) {
        console.error(e);
        process.exitCode = 1;
    }
};

const isDirectRun = process.argv[1] !== undefined &&
    import.meta.url === pathToFileURL(resolve(process.argv[1])).href;

if (isDirectRun) void runCli();
