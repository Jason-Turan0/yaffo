/**
 * Test Generator - Converts YAML specs to Playwright tests using Claude API
 *
 * Supports MCP integration for filesystem access to read source code context.
 */
import "dotenv/config";
import {join, dirname, resolve} from "path";
import {parseSpecFile} from "@lib/test_generator/spec_parser";
import * as fs from "node:fs";
import {generateTestFromSpec} from "@lib/test_generator/model_client";
import {generateTimestampString} from "@lib/test_generator/utils";

export async function generateTest(
    specPath: string,
    outputDir: string
) {
    try {
        const runId = generateTimestampString();
        const spec = parseSpecFile(specPath);
        const logPath = resolve(join(process.cwd(), "reports", "api_logs", spec.feature, runId));
        if (!fs.existsSync(logPath)) {
            fs.mkdirSync(logPath, {recursive: true});
        }
        const generationResult = await generateTestFromSpec(
            {
                specPath,
                outputDir,
                baseUrl: "http://127.0.0.1:5000",
                model: "claude-sonnet-4-20250514",
            },
            spec,
            logPath
        );
        console.log(`✓ Generated: ${generationResult.outputPath}`);
        if (generationResult.logPath) {
            console.log(`📄 API log: ${generationResult.logPath}`);
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

    const filteredArgs = args.filter((a) => !a.startsWith("--"));

    if (filteredArgs.length === 0) {
        console.error("Usage: npx tsx lib/index.ts <spec-path> [output-dir]");
        console.error("");
        process.exit(1);
    }

    const specPath = filteredArgs[0];
    const outputDir = filteredArgs[1] || join(process.cwd(), "generated");

    console.log(`Generating test from: ${specPath}`);
    console.log(`Output directory: ${outputDir}`);

    await generateTest(specPath, outputDir);
}

main().finally(() => {
        console.log('Exiting')
    }
)