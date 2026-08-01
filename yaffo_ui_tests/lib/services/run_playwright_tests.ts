import {join, resolve} from "path";
import {tmpdir} from "os";
import {spawn} from "child_process";
import {existsSync, mkdirSync, readFileSync, realpathSync, rmSync} from "fs";
import {TestResult, TestRunResult} from "@lib/services/isolated_runner";
import {detectSandboxKind, wrapWithSandbox, writableRootsFor} from "@lib/services/test_sandbox";

const UI_TESTS_DIR = resolve(process.cwd());
const GENERATED_TESTS_DIR = resolve(join(UI_TESTS_DIR, 'generated_tests'));

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
                        file: resolve(join(GENERATED_TESTS_DIR, suite.file)),
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


export type PlaywrightTestRunner = typeof runPlaywrightTests;

export const runPlaywrightTests = async (
    baseUrl: string,
    testFiles?: string[]
): Promise<TestRunResult> => {
    console.log(`\n🧪 Running Playwright tests...`);

    const timestamp = Date.now();
    const jsonReportPath = join(tmpdir(), `playwright-report-${timestamp}.json`);

    // The sharing suite lives in its own Playwright project, which only exists
    // when PEER_URL points at the second sandbox instance (see playwright.config.ts).
    const isSharingSuite = (testFiles ?? []).some((file) => /generated_tests[/\\]sharing[/\\]/.test(resolve(file)));
    if (isSharingSuite && !process.env.PEER_URL) {
        throw new Error(
            "The sharing tests need the two-instance sandbox: start it with { withPeer: true } " +
            "(or npm run isolatedEnvironment:start:sharing) and set PEER_URL to instance B."
        );
    }

    const args = [
        "playwright", "test",
        `--project=${isSharingSuite ? "sharing" : "chromium"}`,
        "--reporter=json",
    ];

    if (testFiles && testFiles.length > 0) {
        args.push(...testFiles);
    }

    // The spawned process runs model-generated test code, so pass an explicit
    // env allowlist instead of inheriting process.env — provider API keys and
    // CI credentials must never reach it. SKIP_DOTENV stops playwright.config
    // from re-loading .env (and the keys in it) inside the test process.
    const SPAWN_ENV_ALLOWLIST = [
        "PATH", "HOME", "SHELL", "TMPDIR", "USER", "LOGNAME", "LANG", "LC_ALL", "TERM",
        "CI", "SUITE", "PEER_URL", "TEST_SANDBOX", "PLAYWRIGHT_BROWSERS_PATH",
    ];
    const env: NodeJS.ProcessEnv = {};
    for (const key of SPAWN_ENV_ALLOWLIST) {
        if (process.env[key] !== undefined) env[key] = process.env[key];
    }
    env.BASE_URL = baseUrl;
    env.PLAYWRIGHT_JSON_OUTPUT_NAME = jsonReportPath;
    env.SKIP_DOTENV = "1";

    // Wrap the run in whatever OS sandbox this platform offers (bwrap on Linux,
    // sandbox-exec on macOS): filesystem read-only except the paths tests
    // legitimately write. Network stays shared so tests can reach the sandbox
    // app on host loopback — egress is covered by the env scrub + code audit
    // rather than the sandbox. TEST_SANDBOX overrides the detection.
    const sandboxKind = detectSandboxKind();
    // Playwright writes into reports/ from the first moment; the dir has to
    // exist before it can be made writable inside the sandbox.
    mkdirSync(join(UI_TESTS_DIR, "reports"), {recursive: true});
    const writableRoots = writableRootsFor(UI_TESTS_DIR, {tempRoot: realpathSync(tmpdir())});
    // Pass the resolved kind down so playwright.config.ts knows whether
    // Chromium can still create its own nested sandbox.
    env.TEST_SANDBOX = sandboxKind;
    console.log(`   sandbox: ${sandboxKind}`);

    const [command, commandArgs] = wrapWithSandbox({
        kind: sandboxKind,
        command: "npx",
        args,
        writableRoots,
    });

    return new Promise((resolve) => {
        const testProcess = spawn(command, commandArgs, {
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

/**
 * Run if executed directly — the way to run specs through the OS sandbox
 * by hand. `npm test` and `npm run test:spec` spawn Playwright themselves and
 * are NOT sandboxed; only this path, the generator and the healer are.
 *
 *   npm run test:sandboxed -- [--url <baseUrl>] [spec...]
 *   npx tsx lib/services/run_playwright_tests.ts [--url <baseUrl>] [spec...]
 *
 * The base URL comes from --url, else BASE_URL (which the npm script points at
 * the isolated environment), else the default port. With no spec arguments
 * every generated test runs. Exits non-zero if any test fails, so it composes
 * in scripts.
 */
const isDirectRun = process.argv[1]?.includes("run_playwright_tests");
if (isDirectRun) {
    const args = process.argv.slice(2);
    const urlIndex = args.findIndex((a) => a === "--url" || a === "-u");
    const baseUrl = urlIndex !== -1 && args[urlIndex + 1]
        ? args[urlIndex + 1]
        : process.env.BASE_URL || "http://127.0.0.1:5001";
    // Skip the value that belongs to --url, but only when --url was actually
    // given: urlIndex of -1 would otherwise exclude the first spec argument.
    const testFiles = args.filter((a, i) => !a.startsWith("-") && !(urlIndex !== -1 && i === urlIndex + 1));

    runPlaywrightTests(baseUrl, testFiles.length > 0 ? testFiles : undefined)
        .then((result) => {
            console.log(formatTestResultsAsXml(result));
            process.exit(result.success ? 0 : 1);
        })
        .catch((e) => {
            console.error(`Fatal error: ${e instanceof Error ? e.message : String(e)}`);
            process.exit(1);
        });
}
