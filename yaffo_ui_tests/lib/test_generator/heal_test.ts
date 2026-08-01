/**
 * Auto-Heal Test - Attempts to fix failing Playwright tests using a fresh Claude context
 *
 * Takes a YAML spec file; every generated test file for that spec is run and,
 * if failing, healed serially against one shared isolated environment.
 *
 * Usage:
 *   npx tsx lib/test_generator/heal_test.ts <spec-file-path> [options]
 *
 * Options:
 *   -p, --port <port>  Port for Flask server (default: 5001)
 */
import "dotenv/config";
import {join, basename, dirname, relative, resolve} from "path";
import {existsSync, mkdirSync, readdirSync, statSync, writeFileSync} from "fs";
import {autoHealTestOrchestratorFactory, DEFAULT_MAX_HEAL_ITERATIONS, HealResult} from "@lib/test_generator/auto_heal_orchestrator";
import {formatTestResultsAsJUnit, runPlaywrightTests} from "@lib/services/run_playwright_tests";
import {generateTimestampString} from "@lib/test_generator/utils";
import {startIsolatedEnvironment, IsolatedEnvironment} from "@lib/services/isolated_runner";
import {defaultModel, isKnownModel, KNOWN_MODEL_ALIASES} from "@lib/model_clients/model_client_factory";
import {preflightModel, PreflightError} from "@lib/model_clients/preflight";
import {createFilesystemClient} from "@lib/tool_providers/mcp_filesystem_client";
import {GENERATED_TESTS_ROOT, YAFFO_APP_ROOT} from "@lib/types";
import {recordTestResult} from "@lib/test_generator/test_result_history";
import {parseSpecFile} from "@lib/test_generator/prompt/spec_parser";
import {ModelAlias} from "@lib/model_clients/model_client.interface";

export const SPECS_DIR = resolve(join(process.cwd(), "specs"));

/** generated_tests directory a YAML spec's tests live in (specs/foo.yaml → generated_tests/foo). */
export function testDirForSpec(specFilePath: string): string {
    const rel = relative(SPECS_DIR, resolve(specFilePath)).replace(/\.ya?ml$/, "");
    return join(GENERATED_TESTS_ROOT, rel);
}

/** Inverse mapping: the YAML spec a generated test file was created from. */
export function specPathForTestFile(testFilePath: string): string {
    const rel = relative(GENERATED_TESTS_ROOT, dirname(resolve(testFilePath)));
    return join(SPECS_DIR, `${rel}.yaml`);
}

function collectTestFiles(dir: string): string[] {
    const found: string[] = [];
    for (const entry of readdirSync(dir)) {
        const full = resolve(dir, entry);
        if (statSync(full).isDirectory()) {
            found.push(...collectTestFiles(full));
        } else if (full.endsWith(".spec.ts")) {
            found.push(full);
        }
    }
    return found.sort();
}

/**
 * Default heal iteration budget: the HEAL_MAX_ITERATIONS environment variable
 * (from the shell or .env) when it parses to a positive integer, else 50.
 */
export function defaultMaxIterations(): number {
    const fromEnv = Number.parseInt(process.env.HEAL_MAX_ITERATIONS ?? "", 10);
    return Number.isInteger(fromEnv) && fromEnv > 0 ? fromEnv : DEFAULT_MAX_HEAL_ITERATIONS;
}

interface HealOptions {
    port?: number;
    model?: string;
    /** Serve the restored seed cache instead of re-seeding (CI fan-out). */
    preseeded?: boolean;
    /** Model-turn budget per test file (default: HEAL_MAX_ITERATIONS env var, else 50). */
    maxIterations?: number;
}

export async function healTest(
    specFilePath: string,
    options: HealOptions = {}
): Promise<HealResult[]> {
    const {port = 5001, model = defaultModel(), preseeded = false, maxIterations = defaultMaxIterations()} = options;
    // Fail before starting the (expensive) environment if the alias is unknown.
    if (!isKnownModel(model)) {
        throw new Error(`Unknown model alias: ${model}. Known aliases: ${KNOWN_MODEL_ALIASES.join(", ")}`);
    }
    if (!Number.isInteger(maxIterations) || maxIterations < 1) {
        throw new Error(`maxIterations must be a positive integer, got: ${maxIterations}`);
    }
    const specPath = resolve(specFilePath);
    if (!existsSync(specPath)) {
        throw new Error(`Spec file not found: ${specPath}`);
    }
    const testDir = testDirForSpec(specPath);
    if (!existsSync(testDir)) {
        throw new Error(`No generated tests directory for spec ${specPath} (expected ${testDir})`);
    }
    const testFiles = collectTestFiles(testDir);
    if (testFiles.length === 0) {
        throw new Error(`No generated test files found under ${testDir}`);
    }

    // Preflight the provider (key present + a minimal call succeeds) so a missing
    // key or exhausted balance fails in ~1s instead of after the triage spend.
    try {
        await preflightModel(model);
    } catch (e) {
        if (e instanceof PreflightError) {
            return testFiles.map((testFile) => ({
                success: false,
                testFilePath: testFile,
                error: e.message,
                logPath: resolve(join(process.cwd(), "reports", "api_logs")),
                iterations: 0,
                classification: "environment_instability",
                costUsd: 0,
                apiCalls: 0,
            }));
        }
        throw e;
    }

    const baseUrl = `http://127.0.0.1:${port}`;
    let isolatedEnvironment: IsolatedEnvironment | null = null;

    try {
        console.log(`\n🔧 Starting isolated environment for healing...`);
        // The sharing suite needs the two-instance sandbox (a peer to pair with
        // and pull from). PEER_URL goes into process.env so every downstream
        // Playwright run — including the orchestrator's re-runs — inherits it
        // and the config's `sharing` project exists.
        const withPeer = basename(specPath, ".yaml") === "sharing";
        isolatedEnvironment = await startIsolatedEnvironment(port, {withPeer, preseeded});
        if (isolatedEnvironment.peer) {
            process.env.PEER_URL = isolatedEnvironment.peer.baseUrl;
        }

        const spec = parseSpecFile(specPath);
        const featureName = spec.feature;
        const results: HealResult[] = [];

        for (const absoluteTestPath of testFiles) {
            const testName = basename(absoluteTestPath, ".spec.ts");
            const runId = generateTimestampString();
            const logPath = resolve(join(process.cwd(), "reports", "api_logs", `heal_${testName}`, runId));
            if (!existsSync(logPath)) {
                mkdirSync(logPath, {recursive: true});
            }
            const outputDir = dirname(absoluteTestPath);

            console.log(`\n🧪 Running initial test to capture failures: ${testName}`);
            const initialResult = await runPlaywrightTests(baseUrl, [absoluteTestPath]);
            recordTestResult(outputDir, featureName, initialResult);

            if (initialResult.success) {
                console.log(`\n✅ ${testName} already passes - no healing needed.`);
                results.push({
                    success: true,
                    testFilePath: absoluteTestPath,
                    logPath,
                    iterations: 0,
                    finalTestRun: initialResult,
                });
                continue;
            }

            console.log(`\n❌ ${testName} failed with ${initialResult.summary.failed} failure(s)`);
            console.log(`\n🩹 Starting auto-heal process...`);

            const allowedDirectories = [YAFFO_APP_ROOT, GENERATED_TESTS_ROOT, outputDir, isolatedEnvironment.tempDir];
            // Fresh clients per file — the orchestrator disconnects its tool
            // providers when a heal finishes.
            const healer = await autoHealTestOrchestratorFactory(
                absoluteTestPath,
                logPath,
                outputDir,
                model as ModelAlias,
                baseUrl,
                allowedDirectories,
                await createFilesystemClient(allowedDirectories),
                undefined,
                maxIterations
            );
            const healResult = await healer.healTest(initialResult, specPath);
            results.push({
                ...healResult,
                costUsd: healer.getCost(),
                apiCalls: healer.getApiCallCount(),
                tokenUsage: healer.getTokenUsage(),
                finalTestRun: healer.getLastTestRun() ?? initialResult,
            });
        }

        return results;
    } finally {
        if (isolatedEnvironment) {
            await isolatedEnvironment.cleanup();
        }
    }
}

async function main() {
    const args = process.argv.slice(2);

    const portIndex = args.findIndex(a => a === "--port" || a === "-p");
    const port = portIndex !== -1 && args[portIndex + 1]
        ? parseInt(args[portIndex + 1], 10)
        : 5001;
    const modelIndex = args.findIndex(a => a === "--model" || a === "-m");
    const model = modelIndex !== -1 && args[modelIndex + 1]
        ? args[modelIndex + 1]
        : defaultModel();
    const preseeded = args.includes("--preseeded");
    const assessmentIndex = args.findIndex(a => a === "--assessment-out");
    const assessmentOut = assessmentIndex !== -1 ? args[assessmentIndex + 1] : undefined;
    const junitIndex = args.findIndex(a => a === "--junit-out");
    const junitOut = junitIndex !== -1 ? args[junitIndex + 1] : undefined;
    const maxIterationsIndex = args.findIndex(a => a === "--max-iterations");
    const maxIterations = maxIterationsIndex !== -1 && args[maxIterationsIndex + 1]
        ? parseInt(args[maxIterationsIndex + 1], 10)
        : defaultMaxIterations();

    const filteredArgs = args.filter((a, i) =>
        !a.startsWith("--") && !a.startsWith("-") &&
        (portIndex === -1 || i !== portIndex + 1) &&
        (modelIndex === -1 || i !== modelIndex + 1) &&
        (assessmentIndex === -1 || i !== assessmentIndex + 1) &&
        (junitIndex === -1 || i !== junitIndex + 1) &&
        (maxIterationsIndex === -1 || i !== maxIterationsIndex + 1)
    );

    if (filteredArgs.length === 0) {
        console.error("Usage: npx tsx lib/test_generator/heal_test.ts <spec-file-path> [options]");
        console.error("");
        console.error("Options:");
        console.error("  -p, --port <port>   Port for isolated Flask server (default: 5001)");
        console.error("  -m, --model <model> Model alias (default: MODEL_ALIAS env var, else claude-sonnet-5)");
        console.error("  --preseeded         Serve the restored seed cache instead of re-seeding");
        console.error("  --assessment-out <dir>  Write one machine-readable assessment JSON per test file");
        console.error("  --junit-out <dir>       Write one JUnit XML per test file (post-heal state, for CI reporting)");
        console.error("  --max-iterations <n>    Model-turn budget per test file (default: HEAL_MAX_ITERATIONS env var, else 50)");
        console.error("");
        process.exit(1);
    }

    const specFilePath = filteredArgs[0];

    if (!isKnownModel(model)) {
        console.error(`✖ Unknown model alias: ${model}`);
        console.error(`  Known aliases: ${KNOWN_MODEL_ALIASES.join(", ")}`);
        process.exit(1);
    }

    console.log(`\n🩹 Auto-healing spec: ${specFilePath}`);

    if (!Number.isInteger(maxIterations) || maxIterations < 1) {
        console.error(`✖ --max-iterations must be a positive integer`);
        process.exit(1);
    }

    const results = await healTest(specFilePath, {port, model, preseeded, maxIterations});

    // The published assessments (job summary, PR body, issues) are built from
    // these. Written regardless of outcome so the caller's `|| true` still
    // records them.
    if (assessmentOut) {
        mkdirSync(assessmentOut, {recursive: true});
        for (const result of results) {
            const testName = basename(result.testFilePath, ".spec.ts");
            const outPath = join(assessmentOut, `${testName}.json`);
            writeFileSync(outPath, JSON.stringify({
                spec: relative(process.cwd(), result.testFilePath),
                spec_file: specFilePath,
                model,
                success: result.success,
                classification: result.classification ?? (result.success ? "already_passing" : "unknown"),
                iterations: result.iterations,
                cost_usd: result.costUsd ?? null,
                api_calls: result.apiCalls ?? null,
                // Token counts behind cost_usd. Cost is an estimate from
                // MODEL_PRICING, so publish the raw counts alongside it —
                // they stay meaningful if the price table drifts.
                tokens: result.tokenUsage ?? null,
                error: result.error ?? null,
                logPath: result.logPath,
                // The full triage the model wrote to <feature>.triage_analysis.json.
                // Inlined here (rather than left as a sibling file) so the job
                // summary, PR body and regression issues can quote the reasoning
                // and suggested action without re-reading generated_tests/.
                triage_analysis: result.triageAnalysis ?? null,
            }, null, 2) + "\n");
            console.log(`   Assessment written to ${outPath}`);
        }
    }

    // One JUnit report per test file, holding the run the spec ended on — the
    // healer re-runs each spec many times, so Playwright's own junit reporter
    // would leave only the last iteration of the last file behind.
    if (junitOut) {
        mkdirSync(junitOut, {recursive: true});
        for (const result of results) {
            if (!result.finalTestRun) continue;
            const testName = basename(result.testFilePath, ".spec.ts");
            const outPath = join(junitOut, `${testName}.xml`);
            writeFileSync(outPath, formatTestResultsAsJUnit(result.finalTestRun, testName));
            console.log(`   JUnit report written to ${outPath}`);
        }
    }

    let anyFailed = false;
    for (const result of results) {
        const testName = basename(result.testFilePath, ".spec.ts");
        if (result.success) {
            console.log(`\n✅ ${testName}: healed/passing after ${result.iterations} iteration(s)`);
            console.log(`   Log path: ${result.logPath}`);
        } else if (result.classification === "application_regression") {
            anyFailed = true;
            console.error(`\n🐛 ${testName}: application regression detected: ${result.error}`);
            console.error(`   The test is correct — the application has a bug.`);
            console.error(`   Log path: ${result.logPath}`);
        } else {
            anyFailed = true;
            console.error(`\n❌ ${testName}: auto-heal failed: ${result.error}`);
            console.error(`   Iterations attempted: ${result.iterations}`);
            console.error(`   Log path: ${result.logPath}`);
        }
    }
    process.exit(anyFailed ? 1 : 0);
}

const isDirectRun = process.argv[1]?.includes("heal_test");
if (isDirectRun) {
    main().catch((e) => {
        console.error(`Fatal error: ${e instanceof Error ? e.message : String(e)}`);
        process.exit(1);
    });
}
