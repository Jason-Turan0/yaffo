/**
 * Auto-Heal Test - Attempts to fix failing Playwright tests using a fresh Claude context
 *
 * Usage:
 *   npx tsx lib/test_generator/heal_test.ts <test-file-path> [options]
 *
 * Options:
 *   -p, --port <port>  Port for Flask server (default: 5001)
 */
import "dotenv/config";
import {join, basename, dirname, resolve} from "path";
import {existsSync, mkdirSync, readdirSync} from "fs";
import {autoHealTestOrchestratorFactory, HealResult} from "@lib/test_generator/auto_heal_orchestrator";
import {runPlaywrightTests} from "@lib/services/run_playwright_tests";
import {generateTimestampString} from "@lib/test_generator/utils";
import {startIsolatedEnvironment, IsolatedEnvironment} from "@lib/services/isolated_runner";
import {createFilesystemClient} from "@lib/tool_providers/mcp_filesystem_client";
import {YAFFO_ROOT} from "@lib/types";

const SPECS_DIR = resolve(join(process.cwd(), "specs"));

function inferSpecPath(testFilePath: string): string {
    const testDir = dirname(resolve(testFilePath));
    const featureName = basename(testDir);
    const specFile = join(SPECS_DIR, `${featureName}.yaml`);
    //TODO handle nested specfiles
    if (!existsSync(specFile)) {
        throw new Error(`Spec file not found: ${specFile}`);
    }
    return specFile;
}

interface HealOptions {
    port?: number;
}

export async function healTest(
    testFilePath: string,
    options: HealOptions = {}
): Promise<HealResult> {
    const {port = 5001} = options;
    const specPath = inferSpecPath(testFilePath);
    const baseUrl = `http://127.0.0.1:${port}`;

    let isolatedEnvironment: IsolatedEnvironment | null = null;

    try {
        const absoluteTestPath = resolve(testFilePath);
        if (!existsSync(absoluteTestPath)) {
            throw new Error(`Test file not found: ${absoluteTestPath}`);
        }

        const testName = basename(absoluteTestPath, ".spec.ts");
        const runId = generateTimestampString();
        const logPath = resolve(join(process.cwd(), "reports", "api_logs", `heal_${testName}`, runId));
        if (!existsSync(logPath)) {
            mkdirSync(logPath, {recursive: true});
        }

        const outputDir = dirname(absoluteTestPath);

        console.log(`\n🔧 Starting isolated environment for healing...`);
        isolatedEnvironment = await startIsolatedEnvironment(port);

        console.log(`\n🧪 Running initial test to capture failures...`);
        const initialResult = await runPlaywrightTests(baseUrl, [absoluteTestPath]);

        if (initialResult.success) {
            console.log(`\n✅ Test already passes - no healing needed.`);
            return {
                success: true,
                testFilePath: absoluteTestPath,
                logPath,
                iterations: 0,
            };
        }

        console.log(`\n❌ Test failed with ${initialResult.summary.failed} failure(s)`);
        console.log(`\n🩹 Starting auto-heal process...`);


        const allowedDirectories = [YAFFO_ROOT, outputDir, isolatedEnvironment.tempDir];
        const healer = await autoHealTestOrchestratorFactory(
            absoluteTestPath,
            logPath,
            outputDir,
            "claude-sonnet-4-5",
            baseUrl,
            allowedDirectories,
            await createFilesystemClient(allowedDirectories),
            undefined
        );
        return await healer.healTest(initialResult, specPath);

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

    const filteredArgs = args.filter((a, i) =>
        !a.startsWith("--") && !a.startsWith("-") &&
        (portIndex === -1 || i !== portIndex + 1)
    );

    if (filteredArgs.length === 0) {
        console.error("Usage: npx tsx lib/test_generator/heal_test.ts <test-file-path> [options]");
        console.error("");
        console.error("Options:");
        console.error("  -p, --port <port>   Port for isolated Flask server (default: 5001)");
        console.error("");
        process.exit(1);
    }

    const testFilePath = filteredArgs[0];

    console.log(`\n🩹 Auto-healing test: ${testFilePath}`);

    const result = await healTest(testFilePath, {port});

    if (result.success) {
        console.log(`\n✅ Test healed successfully after ${result.iterations} iteration(s)`);
        console.log(`   Log path: ${result.logPath}`);
        process.exit(0);
    } else {
        console.error(`\n❌ Auto-heal failed: ${result.error}`);
        console.error(`   Iterations attempted: ${result.iterations}`);
        console.error(`   Log path: ${result.logPath}`);
        process.exit(1);
    }
}

const isDirectRun = process.argv[1]?.includes("heal_test");
if (isDirectRun) {
    main().catch((e) => {
        console.error(`Fatal error: ${e instanceof Error ? e.message : String(e)}`);
        process.exit(1);
    });
}