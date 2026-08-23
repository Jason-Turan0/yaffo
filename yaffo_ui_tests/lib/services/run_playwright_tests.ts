import {dirname, join, relative, resolve} from "path";
import {tmpdir} from "os";
import {spawn} from "child_process";
import {randomUUID} from "crypto";
import {appendFileSync, copyFileSync, existsSync, mkdirSync, readFileSync, realpathSync, rmSync} from "fs";
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

/** Playwright colourises error messages; escape codes are noise in markdown. */
// eslint-disable-next-line no-control-regex
const stripAnsi = (text: string): string => text.replace(/\[[0-9;]*m/g, "");

/** Escape a value going into a markdown table cell. */
const escapeCell = (text: string): string => text.replace(/\|/g, "\\|").replace(/\r?\n/g, " ");

/** Repo-relative path, so summaries do not leak the runner's directory layout. */
const relativeSpec = (file: string): string => {
    const relativePath = relative(UI_TESTS_DIR, file);
    return relativePath.startsWith("..") ? file : relativePath;
};

/**
 * Render results as a GitHub job summary: a counts table plus each failure with
 * its location and (ANSI-stripped) error. Written to $GITHUB_STEP_SUMMARY, this
 * shows up on the workflow run page, so a red build can be read without
 * downloading the HTML report artifact.
 */
export const formatTestResultsAsMarkdown = (result: TestRunResult, title = "Playwright results"): string => {
    const {total, passed, failed, skipped} = result.summary;
    const durationMs = result.tests.reduce((sum, test) => sum + test.duration, 0);
    const lines: string[] = [
        `## ${result.success ? "✅" : "❌"} ${title}`,
        "",
        "| Total | Passed | Failed | Skipped | Duration |",
        "| ---: | ---: | ---: | ---: | ---: |",
        `| ${total} | ${passed} | ${failed} | ${skipped} | ${(durationMs / 1000).toFixed(1)}s |`,
        "",
    ];

    const failures = result.tests.filter((test) => test.status === "failed" || test.status === "timedOut");
    if (failures.length > 0) {
        lines.push("### Failures", "");
        for (const test of failures) {
            const location = test.errors?.[0]?.location;
            const where = location?.file
                ? `${relativeSpec(location.file)}:${location.line ?? 0}`
                : relativeSpec(test.file);
            const message = stripAnsi(test.errors?.[0]?.message ?? test.error?.message ?? "No error message captured.");
            lines.push(
                `<details><summary><code>${escapeCell(test.testName)}</code></summary>`,
                "",
                `\`${where}\``,
                "",
                "```",
                // Long Playwright diffs bury the useful first lines and can blow
                // the 1 MiB summary budget across a big matrix.
                message.split("\n").slice(0, 30).join("\n"),
                "```",
                "",
                "</details>",
                "",
            );
        }
    }

    if (total === 0) {
        lines.push("_No tests ran — check the step log for a startup failure._", "");
    }

    return lines.join("\n");
};

/**
 * Render results as JUnit XML for dorny/test-reporter.
 *
 * The heal flow cannot use Playwright's own junit reporter: it runs the specs
 * through runPlaywrightTests repeatedly (once per heal iteration), so a
 * reporter-written file would be overwritten by every intermediate attempt.
 * Serialising a chosen TestRunResult instead lets the caller publish exactly
 * one report per spec — the state the tests ended in.
 */
export const formatTestResultsAsJUnit = (result: TestRunResult, suiteName: string): string => {
    const {total, failed, skipped} = result.summary;
    const seconds = (ms: number): string => (ms / 1000).toFixed(3);
    const totalMs = result.tests.reduce((sum, test) => sum + test.duration, 0);

    const lines: string[] = [
        `<?xml version="1.0" encoding="UTF-8"?>`,
        `<testsuites name="${escapeXml(suiteName)}" tests="${total}" failures="${failed}" skipped="${skipped}" errors="0" time="${seconds(totalMs)}">`,
        `  <testsuite name="${escapeXml(suiteName)}" tests="${total}" failures="${failed}" skipped="${skipped}" errors="0" time="${seconds(totalMs)}">`,
    ];

    for (const test of result.tests) {
        const classname = escapeXml(relativeSpec(test.file));
        const open = `    <testcase name="${escapeXml(test.testName)}" classname="${classname}" time="${seconds(test.duration)}">`;
        if (test.status === "failed" || test.status === "timedOut") {
            const first = test.errors?.[0];
            const message = stripAnsi(first?.message ?? test.error?.message ?? "Test failed");
            lines.push(
                open,
                `      <failure message="${escapeXml(message.split("\n")[0].slice(0, 300))}">${escapeXml(stripAnsi(test.error?.stack ?? message))}</failure>`,
                `    </testcase>`,
            );
        } else if (test.status === "skipped") {
            lines.push(open, `      <skipped/>`, `    </testcase>`);
        } else {
            lines.push(open, `    </testcase>`);
        }
    }

    lines.push(`  </testsuite>`, `</testsuites>`);
    return lines.join("\n") + "\n";
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

export interface RunOptions {
    /**
     * Playwright reporters, comma separated. Defaults to "json" alone, which is
     * all the heal/generate loops need. CI passes "json,html,list" to keep the
     * uploadable HTML report; "json" is always included regardless, since the
     * summary below is parsed from it.
     */
    reporters?: string;
    /**
     * Copy the raw Playwright JSON report here before the temp copy is deleted.
     * The auto-heal workflow discovers failed specs by scanning the uploaded
     * artifact for `test-results.json`, and a CLI --reporter overrides the
     * config's json outputFile — so CI has to name that path explicitly or
     * nothing downstream can see which specs failed.
     */
    jsonReportOut?: string;
    /** Per-run values such as SUITE and PEER_URL; safe for concurrent callers. */
    environment?: NodeJS.ProcessEnv;
    /** Stop the spawned Playwright process when the enclosing suite run is interrupted. */
    signal?: AbortSignal;
}

export const runPlaywrightTests = async (
    baseUrl: string,
    testFiles?: string[],
    options: RunOptions = {},
): Promise<TestRunResult> => {
    console.log(`\n🧪 Running Playwright tests...`);

    const jsonReportPath = join(tmpdir(), `playwright-report-${randomUUID()}.json`);
    // Resolved out here on purpose: inside the Promise below, `resolve` is the
    // promise's own resolver, not path.resolve.
    const jsonReportDestination = options.jsonReportOut
        ? resolve(UI_TESTS_DIR, options.jsonReportOut)
        : undefined;

    // The sharing suite lives in its own Playwright project, which only exists
    // when PEER_URL points at the second sandbox instance (see playwright.config.ts).
    const isSharingSuite = (testFiles ?? []).some((file) => /generated_tests[/\\]sharing[/\\]/.test(resolve(file)));
    const sourceEnv = {...process.env, ...options.environment};
    if (isSharingSuite && !sourceEnv.PEER_URL) {
        throw new Error(
            "The sharing tests need the two-instance sandbox: start it with { withPeer: true } " +
            "(or npm run isolatedEnvironment:start:sharing) and set PEER_URL to instance B."
        );
    }

    // A CLI --reporter replaces the config's reporter list wholesale, so the
    // json one has to survive whatever the caller asked for or the run comes
    // back with an empty summary.
    const reporters = (options.reporters ?? "json").split(",").map((r) => r.trim()).filter(Boolean);
    if (!reporters.includes("json")) reporters.push("json");

    const args = [
        "playwright", "test",
        `--project=${isSharingSuite ? "sharing" : "chromium"}`,
        `--reporter=${reporters.join(",")}`,
    ];

    if (testFiles && testFiles.length > 0) {
        args.push(...testFiles);
    }

    // The spawned process runs model-generated test code, so pass an explicit
    // env allowlist instead of inheriting process.env — provider API keys and
    // CI credentials must never reach it. SKIP_DOTENV stops playwright.config
    // from re-loading .env (and the keys in it) inside the test process.
    // PLAYWRIGHT_HTML_OUTPUT_DIR only names where the html reporter writes; a
    // path outside the writable roots is refused by the sandbox anyway.
    // GITHUB_WORKSPACE is the checkout path (not a secret) — the `github`
    // reporter needs it to emit annotation paths relative to the repo root
    // rather than to yaffo_ui_tests, or they never attach to the PR diff.
    // Note what is deliberately absent: GITHUB_TOKEN, GITHUB_STEP_SUMMARY and
    // the provider API keys never reach the generated test code.
    const SPAWN_ENV_ALLOWLIST = [
        "PATH", "HOME", "SHELL", "TMPDIR", "USER", "LOGNAME", "LANG", "LC_ALL", "TERM",
        "CI", "SUITE", "PEER_URL", "TEST_SANDBOX", "PLAYWRIGHT_BROWSERS_PATH",
        "PLAYWRIGHT_HTML_OUTPUT_DIR", "PLAYWRIGHT_JUNIT_OUTPUT_FILE", "GITHUB_WORKSPACE",
    ];
    const env: NodeJS.ProcessEnv = {};
    for (const key of SPAWN_ENV_ALLOWLIST) {
        if (sourceEnv[key] !== undefined) env[key] = sourceEnv[key];
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
        let settled = false;
        const finish = (exitCode: number) => {
            if (settled) return;
            settled = true;
            options.signal?.removeEventListener("abort", abort);
            const {tests, summary} = parsePlaywrightJson(jsonReportPath);
            if (jsonReportDestination && existsSync(jsonReportPath)) {
                mkdirSync(dirname(jsonReportDestination), {recursive: true});
                copyFileSync(jsonReportPath, jsonReportDestination);
            }
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
        };
        const abort = () => testProcess.kill("SIGTERM");
        options.signal?.addEventListener("abort", abort, {once: true});
        if (options.signal?.aborted) abort();

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

        testProcess.on("error", (error) => {
            output += `${error.message}\n`;
            finish(1);
        });
        testProcess.on("close", (code) => finish(code ?? 1));
    });
};

/**
 * Run if executed directly — the way to run specs through the OS sandbox
 * by hand. `npm test` and `npm run test:spec` spawn Playwright themselves and
 * are NOT sandboxed; only this path, the generator and the healer are.
 *
 *   npm run test:sandboxed -- [--url <baseUrl>] [--reporter <list>] [spec...]
 *   npx tsx lib/services/run_playwright_tests.ts [--json-out <file>] [spec...]
 *
 * The base URL comes from --url, else BASE_URL (which the npm script points at
 * the isolated environment), else the default port. With no spec arguments
 * every generated test runs. Exits non-zero if any test fails, so it composes
 * in scripts.
 *
 * Under GitHub Actions ($GITHUB_STEP_SUMMARY set) it also appends a markdown
 * result table to the job summary. Deliberately only on this path: the healer
 * calls runPlaywrightTests dozens of times per spec and would bury the heal
 * summary under its own re-runs.
 */
const isDirectRun = process.argv[1]?.includes("run_playwright_tests");
if (isDirectRun) {
    const args = process.argv.slice(2);
    // Track which indices a flag consumed, so a flag's value is never mistaken
    // for a spec path (and an absent flag never swallows argument zero).
    const consumed = new Set<number>();
    const flagValue = (...names: string[]): string | undefined => {
        const i = args.findIndex((a) => names.includes(a));
        if (i === -1 || args[i + 1] === undefined) return undefined;
        consumed.add(i).add(i + 1);
        return args[i + 1];
    };

    // Every flag must be consumed before the positional args are collected.
    const baseUrl = flagValue("--url", "-u") || process.env.BASE_URL || "http://127.0.0.1:5001";
    const reporters = flagValue("--reporter", "-r");
    const title = flagValue("--title");
    const jsonReportOut = flagValue("--json-out");
    const testFiles = args.filter((a, i) => !consumed.has(i) && !a.startsWith("-"));

    const summaryTitle = title || testFiles.map(relativeSpec).join(", ") || "Playwright results";

    runPlaywrightTests(baseUrl, testFiles.length > 0 ? testFiles : undefined, {reporters, jsonReportOut})
        .then((result) => {
            console.log(formatTestResultsAsXml(result));
            const stepSummary = process.env.GITHUB_STEP_SUMMARY;
            if (stepSummary) {
                appendFileSync(stepSummary, formatTestResultsAsMarkdown(result, summaryTitle) + "\n");
            }
            process.exit(result.success ? 0 : 1);
        })
        .catch((e) => {
            console.error(`Fatal error: ${e instanceof Error ? e.message : String(e)}`);
            process.exit(1);
        });
}
