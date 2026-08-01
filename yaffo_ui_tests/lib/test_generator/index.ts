import "dotenv/config";
import {join, resolve, relative} from "path";
import {Command} from "commander";
import {parseSpecFile} from "@lib/test_generator/prompt/spec_parser";
import * as fs from "node:fs";
import {testGeneratorOrchestratorFactory} from "@lib/test_generator/test_generator_orchestrator";
import {generateTimestampString} from "@lib/test_generator/utils";
import {ModelAlias} from "@lib/model_clients/model_client.interface";
import {defaultModel} from "@lib/model_clients/model_client_factory";

const SPECS_DIR = resolve(join(process.cwd(), "specs"));
const GENERATED_TESTS_DIR = resolve(join(process.cwd(), "generated_tests"));

function computeOutputDir(specPath: string): string {
    const absoluteSpecPath = resolve(specPath);
    const relativeToSpecs = relative(SPECS_DIR, absoluteSpecPath);
    const withoutExtension = relativeToSpecs.replace(/\.(yaml|yml)$/, "");
    return resolve(join(GENERATED_TESTS_DIR, withoutExtension));
}

interface GenerateOptions {
    runTestEnvironment?: boolean;
    port?: number;
    model: string
}

export async function generateTest(
    specPath: string,
    options: GenerateOptions = {model: defaultModel()}
) {
    const {runTestEnvironment = false, port = 5001} = options;

    try {
        const runId = generateTimestampString();
        const spec = parseSpecFile(specPath);
        const logPath = resolve(join(process.cwd(), "reports", "api_logs", spec.feature, runId));
        if (!fs.existsSync(logPath)) {
            fs.mkdirSync(logPath, {recursive: true});
        }

        const outputDir = computeOutputDir(specPath);
        if (!fs.existsSync(outputDir)) {
            fs.mkdirSync(outputDir, {recursive: true});
        }

        const baseUrl = `http://127.0.0.1:${port}`;
        const testGenerator = await testGeneratorOrchestratorFactory(
            spec,
            logPath,
            outputDir,
            options.model as ModelAlias,
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

const program = new Command()
    .name("generate")
    .description("Converts YAML specs to Playwright tests using AI models")
    .argument("<spec-path>", "path to the YAML spec file")
    .option("-r, --run-tests", "run generated tests against isolated environment", false)
    .option("-p, --port <port>", "port for isolated Flask server", parseInt, 5001)
    .option("-m, --model <model>", "model alias (default: MODEL_ALIAS env var, else claude-sonnet-5)", defaultModel())
    .action(async (specPath: string, opts: { runTests: boolean; port: number; model: string }) => {
        console.log(`Generating test from: ${specPath}`);
        await generateTest(specPath, {
            runTestEnvironment: opts.runTests,
            port: opts.port,
            model: opts.model,
        });
    });

program.parseAsync().finally(() => {
    console.log("Exiting");
});
