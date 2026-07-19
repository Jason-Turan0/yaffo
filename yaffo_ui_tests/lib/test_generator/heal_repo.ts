/**
 * Repo-level auto-heal entry point.
 *
 * Reads a Playwright JSON report, finds failed generated spec files, and runs
 * the existing single-file healer for each failed file.
 */
import "dotenv/config";
import {existsSync, readFileSync} from "fs";
import {join, resolve} from "path";
import {Command} from "commander";
import {healTest} from "@lib/test_generator/heal_test";

interface PlaywrightJsonReport {
    suites: PlaywrightSuite[];
}

interface PlaywrightSuite {
    file: string;
    specs?: PlaywrightSpec[];
    suites?: PlaywrightSuite[];
}

interface PlaywrightSpec {
    title: string;
    tests: PlaywrightTest[];
}

interface PlaywrightTest {
    status: "expected" | "unexpected" | "skipped" | "flaky";
}

interface HealRepoOptions {
    reportDir?: string;
    reportJson?: string;
    port: number;
    model: string;
    maxFiles?: number;
    dryRun?: boolean;
}

function resolveReportJson(options: HealRepoOptions): string {
    if (options.reportJson) {
        return resolve(options.reportJson);
    }

    const reportDir = resolve(options.reportDir || "reports");
    // Reports are written per-suite (reports/<suite>/results/test-results.json),
    // so check the suite subdirs as well as the plain layout for back-compat and
    // for a report-dir already pointed at a single suite.
    const candidates = [
        join(reportDir, "results", "test-results.json"),
        join(reportDir, "test-results.json"),
        join(reportDir, "core", "results", "test-results.json"),
        join(reportDir, "sharing", "results", "test-results.json"),
    ];

    const found = candidates.find(candidate => existsSync(candidate));
    if (!found) {
        throw new Error(`No Playwright JSON report found in ${reportDir}`);
    }
    return found;
}

function failedSpecFilesFromReport(reportJsonPath: string): string[] {
    const report = JSON.parse(readFileSync(reportJsonPath, "utf-8")) as PlaywrightJsonReport;
    const failed = new Set<string>();

    const visitSuite = (suite: PlaywrightSuite): void => {
        for (const spec of suite.specs || []) {
            if (spec.tests.some(test => test.status === "unexpected")) {
                failed.add(resolve(join("generated_tests", suite.file)));
            }
        }
        for (const nested of suite.suites || []) {
            visitSuite(nested);
        }
    };

    for (const suite of report.suites || []) {
        visitSuite(suite);
    }

    return [...failed].sort();
}

export async function healRepo(options: HealRepoOptions): Promise<void> {
    const reportJson = resolveReportJson(options);
    let failedFiles = failedSpecFilesFromReport(reportJson);

    if (options.maxFiles && options.maxFiles > 0) {
        failedFiles = failedFiles.slice(0, options.maxFiles);
    }

    if (failedFiles.length === 0) {
        console.log(`No unexpected Playwright failures found in ${reportJson}`);
        return;
    }

    console.log(`Found ${failedFiles.length} failed spec file(s):`);
    for (const file of failedFiles) {
        console.log(`  - ${file}`);
    }

    if (options.dryRun) {
        console.log("\nDry run enabled - not invoking auto-heal.");
        return;
    }

    const failures: string[] = [];
    for (const file of failedFiles) {
        console.log(`\nAuto-healing failed spec: ${file}`);
        const result = await healTest(file, {
            port: options.port,
            model: options.model,
        });

        if (!result.success) {
            failures.push(`${file}: ${result.error || result.classification || "unknown failure"}`);
        }
    }

    if (failures.length > 0) {
        throw new Error(`Auto-heal failed for ${failures.length} file(s):\n${failures.join("\n")}`);
    }
}

const program = new Command()
    .name("heal-repo")
    .description("Auto-heal failed generated Playwright specs from a Playwright report")
    .option("--report-dir <dir>", "Directory containing Playwright report artifacts", "reports")
    .option("--report-json <path>", "Path to Playwright test-results.json")
    .option("-p, --port <port>", "Port for isolated Flask server", (value) => parseInt(value, 10), 5002)
    .option("-m, --model <model>", "Model alias", "claude-sonnet-4-5")
    .option("--max-files <count>", "Maximum failed spec files to heal", (value) => parseInt(value, 10))
    .option("--dry-run", "Print failed spec files without invoking auto-heal", false);

const isDirectRun = process.argv[1]?.includes("heal_repo");
if (isDirectRun) {
    program.parseAsync().then(async () => {
        await healRepo(program.opts<HealRepoOptions>());
    }).catch((e) => {
        console.error(`Fatal error: ${e instanceof Error ? e.message : String(e)}`);
        process.exit(1);
    });
}
