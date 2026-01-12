/**
 * Test Generator - Converts YAML specs to Playwright tests using Claude API
 *
 * Supports MCP integration for filesystem access to read source code context.
 */
import "dotenv/config";
import {join, basename, resolve} from "path";
import {parseSpecFile} from "@lib/test_generator/spec_parser";
import * as fs from "node:fs";
import {testGeneratorOrchestratorFactory} from "@lib/test_generator/test_generator_orchestrator";
import {generateTimestampString} from "@lib/test_generator/utils";
import {runIsolatedTests} from "@lib/test_generator/isolated_runner";
import {AnthropicModelAliasHaiku, AnthropicModelAliasOpus} from "@lib/test_generator/anthropic_model_client";

interface GenerateOptions {
    runTestEnvironment?: boolean;
    port?: number;
}

export async function generateTest(
    specPath: string,
    options: GenerateOptions = {}
) {
    const {runTestEnvironment = false, port = 5001} = options;

    try {
        const runId = generateTimestampString();
        const spec = parseSpecFile(specPath);
        const logPath = resolve(join(process.cwd(), "reports", "api_logs", spec.feature, runId));
        if (!fs.existsSync(logPath)) {
            fs.mkdirSync(logPath, {recursive: true});
        }
        const baseUrl = `http://127.0.0.1:${port}`;
        const testGenerator = await testGeneratorOrchestratorFactory(
            spec,
            logPath,
            'claude-haiku-4-5',
            baseUrl,
            runTestEnvironment,
            port
        );

        const result = await testGenerator.generate(
            specPath,
            baseUrl
        );

        if (!result.success) {
            console.error(`\n❌ Test generation failed: ${result.error}`);
            process.exit(1);
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


    await generateTest(specPath, {runTestEnvironment: runTests, port});
}

main().finally(() => {
    console.log("Exiting");
});