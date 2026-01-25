import {join, resolve} from "path";
import {tmpdir} from "os";
import {spawn} from "child_process";
import fs, {existsSync, readFileSync, rmSync} from "fs";
import {TestResult, TestRunResult} from "@lib/test_generator/isolated_runner";

const UI_TESTS_DIR = resolve(process.cwd());

interface PlaywrightJsonReport {
    suites: PlaywrightSuite[];
    stats: {
        total: number;
        expected: number;
        unexpected: number;
        skipped: number;
        duration: number;
    };
}

interface PlaywrightSuite {
    title: string;
    file: string;
    specs: PlaywrightSpec[];
    suites?: PlaywrightSuite[];
}

interface PlaywrightSpec {
    title: string;
    tests: PlaywrightTest[];
}

interface PlaywrightTest {
    status: "expected" | "unexpected" | "skipped" | "flaky";
    duration: number;
    results: PlaywrightTestResult[];
}

interface PlaywrightTestResult {
    status: "passed" | "failed" | "skipped" | "timedOut";
    duration: number;
    error?: {
        message: string;
        stack?: string;
    };
    errors?: {
        location?: {
            file?: string,
            column?: number,
            line?: number
        },
        message?: string
    }[]
}

const parsePlaywrightJson = (jsonPath: string): { tests: TestResult[]; summary: TestRunResult["summary"] } => {
    const tests: TestResult[] = [];
    const summary = {total: 0, passed: 0, failed: 0, skipped: 0};

    if (!existsSync(jsonPath)) {
        return {tests, summary};
    }

    try {
        const jsonContent = readFileSync(jsonPath, "utf-8");
        const report: PlaywrightJsonReport = JSON.parse(jsonContent);

        const extractTests = (suite: PlaywrightSuite): void => {
            for (const spec of suite.specs) {
                for (const test of spec.tests) {
                    const result = test.results[0];
                    const status = result?.status ?? "skipped";

                    const testResult: TestResult = {
                        file: resolve(join(UI_TESTS_DIR, suite.file)),
                        testName: `${suite.title} › ${spec.title}`,
                        status: status as TestResult["status"],
                        duration: result?.duration ?? 0,
                    };

                    if (result?.error) {
                        testResult.error = {
                            message: result.error.message,
                            stack: result.error.stack,
                        };
                    }
                    if (result?.errors && result?.errors.length > 0) {
                        testResult.errors = (result?.errors || []).map(err => ({
                            location: err.location,
                            message: err.message
                        }));
                    }

                    tests.push(testResult);
                    summary.total++;

                    if (status === "passed") {
                        summary.passed++;
                    } else if (status === "failed" || status === "timedOut") {
                        summary.failed++;
                    } else if (status === "skipped") {
                        summary.skipped++;
                    }
                }
            }

            if (suite.suites) {
                for (const nestedSuite of suite.suites) {
                    extractTests(nestedSuite);
                }
            }
        };

        for (const suite of report.suites) {
            extractTests(suite);
        }
    } catch (e) {
        console.error(`Failed to parse Playwright JSON report: ${e instanceof Error ? e.message : String(e)}`);
    }

    return {tests, summary};
};

export const formatTestResultsAsXml = (result: TestRunResult): string => {
    const lines: string[] = [
        "<test_evaluation>",
        `    <status>${result.success ? "passed" : "failed"}</status>`,
        "    <summary>",
        `        <total>${result.summary.total}</total>`,
        `        <passed>${result.summary.passed}</passed>`,
        `        <failed>${result.summary.failed}</failed>`,
        `        <skipped>${result.summary.skipped}</skipped>`,
        "    </summary>",
    ];

    if (result.tests.length > 0) {
        lines.push("    <tests>");
        for (const test of result.tests) {
            lines.push(`        <test status="${test.status}">`);
            lines.push(`            <file>${test.file}</file>`);
            lines.push(`            <name>${test.testName}</name>`);
            lines.push(`            <status>${test.status}</status>`);
            lines.push(`            <duration>${test.duration}ms</duration>`);
            if (test.error) {
                lines.push("            <error>");
                lines.push(`                <message>${escapeXml(test.error.message)}</message>`);
                if (test.error.stack) {
                    lines.push(`                <stack>${escapeXml(test.error.stack)}</stack>`);
                }
                lines.push("            </error>");
            }
            if (test.errors) {
                lines.push("            <errors>");
                for (const error of test.errors) {
                    lines.push("            <error>");
                    lines.push(`            <file>${error?.location?.file}</file>`);
                    lines.push(`            <line>${error?.location?.line}</line>`);
                    lines.push(`            <column>${error?.location?.column}</column>`);
                    lines.push(`            <message>${error.message}</message>`);
                    lines.push("            </error>");
                }
                lines.push("            </errors>");
            }
            lines.push("        </test>");
        }
        lines.push("    </tests>");
    }

    lines.push("</test_evaluation>");
    return lines.join("\n");
};

const escapeXml = (str: string): string => {
    return str
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&apos;");
};


export const runPlaywrightTests = async (
    baseUrl: string,
    testFiles?: string[]
): Promise<TestRunResult> => {
    console.log(`\n🧪 Running Playwright tests...`);

    const timestamp = Date.now();
    const jsonReportPath = join(tmpdir(), `playwright-report-${timestamp}.json`);

    const args = [
        "playwright", "test",
        "--project=chromium",
        "--reporter=json",
    ];

    if (testFiles && testFiles.length > 0) {
        args.push(...testFiles);
    }

    const env = {
        ...process.env,
        BASE_URL: baseUrl,
        PLAYWRIGHT_JSON_OUTPUT_NAME: jsonReportPath,
    };

    return new Promise((resolve) => {
        const testProcess = spawn("npx", args, {
            env,
            cwd: UI_TESTS_DIR,
            stdio: ["ignore", "pipe", "pipe"],
        });

        let output = "";
        testProcess.stdout?.on("data", (data) => {
            const text = data.toString();
            output += text;
            process.stdout.write(text);
        });
        testProcess.stderr?.on("data", (data) => {
            const text = data.toString();
            output += text;
            process.stderr.write(text);
        });

        testProcess.on("close", (code) => {
            const exitCode = code ?? 1;
            const {tests, summary} = parsePlaywrightJson(jsonReportPath);
            //console.log(fs.readFileSync(jsonReportPath, "utf8"));
            if (existsSync(jsonReportPath)) {
                rmSync(jsonReportPath, {force: true});
            }

            resolve({
                success: exitCode === 0,
                exitCode,
                output,
                summary,
                tests,
            });
        });
    });
};

// Run if executed directly
const isDirectRun = process.argv[1]?.includes("run_playwright_tests");
if (isDirectRun) {
    runPlaywrightTests('http://127.0.0.1:5001').catch((e) => {
        console.error(`Fatal error: ${e instanceof Error ? e.message : String(e)}`);
        process.exit(1);
    }).then(
        result => {
            //console.log(JSON.stringify(result, null, 2));
            console.log(formatTestResultsAsXml(result));
        }
    );
}
