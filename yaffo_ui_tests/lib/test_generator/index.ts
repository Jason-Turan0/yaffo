/**
 * Test Generator - Converts YAML specs to Playwright tests using Claude API
 *
 * Supports MCP integration for filesystem access to read source code context.
 */
import "dotenv/config";
import {join, basename, resolve} from "path";
import {parseSpecFile} from "@lib/test_generator/spec_parser";
import * as fs from "node:fs";
import {testGeneratorOrchestratorFactory} from "@lib/test_generator/testGeneratorOrchestrator";
import {generateTimestampString} from "@lib/test_generator/utils";
import {runIsolatedTests} from "@lib/test_generator/isolated_runner";

interface GenerateOptions {
    runTests?: boolean;
    port?: number;
}

const GENERATED_TESTS_DIR = resolve(join(process.cwd(), "generated_tests"));

export async function generateTest(
    specPath: string,
    options: GenerateOptions = {}
) {
    const {runTests = false, port = 5001} = options;

    try {
        const runId = generateTimestampString();
        const spec = parseSpecFile(specPath);
        const logPath = resolve(join(process.cwd(), "reports", "api_logs", spec.feature, runId));
        if (!fs.existsSync(logPath)) {
            fs.mkdirSync(logPath, {recursive: true});
        }
        const testGenerator = await testGeneratorOrchestratorFactory(
            spec,
            logPath,
        );

        const result = await testGenerator.generate(
            specPath,
            "http://127.0.0.1:5000",
        );

        if (!result.success) {
            console.error(`\n❌ Test generation failed: ${result.error}`);
            process.exit(1);
        }

        console.log(`\n✅ Test generation completed successfully`);

        if (runTests) {
            console.log(`\n${"=".repeat(60)}`);
            console.log(`Running generated tests against isolated environment...`);
            console.log(`${"=".repeat(60)}`);

            const jsonPath = join(GENERATED_TESTS_DIR, `${spec.feature}.json`);
            let testFiles: string[] = [];

            if (fs.existsSync(jsonPath)) {
                const generatedResponse = JSON.parse(fs.readFileSync(jsonPath, "utf-8"));
                testFiles = generatedResponse.files.map((f: { filename: string }) =>
                    join(GENERATED_TESTS_DIR, basename(f.filename))
                );
            }

            const testResult = await runIsolatedTests(testFiles, port);

            if (testResult.success) {
                console.log(`\n✅ All tests passed!`);
            } else {
                console.log(`\n❌ Tests failed with exit code: ${testResult.exitCode}`);
                process.exit(testResult.exitCode);
            }
        }

        process.exit(0);
    } catch (e) {
        const errorMessage = e instanceof Error ? e.message : String(e);
        console.error(`Fatal Error: ${errorMessage}`);
        process.exit(1);
    }
}

// CLI entry point
async function main() {
    const args = process.argv.slice(2);

    const runTests = args.includes("--run-tests") || args.includes("-r");
    const portIndex = args.findIndex(a => a === "--port" || a === "-p");
    const port = portIndex !== -1 && args[portIndex + 1]
        ? parseInt(args[portIndex + 1], 10)
        : 5001;

    const filteredArgs = args.filter((a, i) =>
        !a.startsWith("--") && !a.startsWith("-") &&
        (portIndex === -1 || i !== portIndex + 1)
    );

    if (filteredArgs.length === 0) {
        console.error("Usage: npx tsx lib/test_generator/index.ts <spec-path> [options]");
        console.error("");
        console.error("Options:");
        console.error("  -r, --run-tests    Run generated tests against isolated environment");
        console.error("  -p, --port <port>  Port for isolated Flask server (default: 5001)");
        console.error("");
        process.exit(1);
    }

    const specPath = filteredArgs[0];

    console.log(`Generating test from: ${specPath}`);
    if (runTests) {
        console.log(`Will run tests after generation on port ${port}`);
    }

    await generateTest(specPath, {runTests, port});
}

main().finally(() => {
    console.log("Exiting");
});