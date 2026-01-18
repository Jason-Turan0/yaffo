import {spawn, ChildProcess, execSync} from "child_process";
import {join, resolve} from "path";
import {mkdirSync, existsSync, cpSync, writeFileSync, rmSync, readFileSync} from "fs";
import {tmpdir} from "os";

export interface IsolatedEnvironment {
    tempDir: string;
    port: number;
    baseUrl: string;
    flaskProcess: ChildProcess | null;
    cleanup: () => Promise<void>;
}

export interface TestResult {
    file: string;
    testName: string;
    status: "passed" | "failed" | "skipped" | "timedOut";
    duration: number;
    error?: {
        message: string;
        stack?: string;
    };
}

export interface TestRunResult {
    success: boolean;
    exitCode: number;
    output: string;
    summary: {
        total: number;
        passed: number;
        failed: number;
        skipped: number;
    };
    tests: TestResult[];
}

const UI_TESTS_DIR = resolve(process.cwd());
const YAFFO_DIR = resolve(join(UI_TESTS_DIR, ".."));
const SCRIPTS_DIR = join(UI_TESTS_DIR, "scripts");

const generateTimestamp = (): string => {
    const now = new Date();
    return now.toISOString().replace(/[-:]/g, "").replace("T", "_").slice(0, 15);
};

const waitForServer = async (url: string, maxAttempts = 30): Promise<boolean> => {
    for (let i = 0; i < maxAttempts; i++) {
        try {
            const response = await fetch(url);
            if (response.ok) {
                return true;
            }
        } catch {
            // Server not ready yet
        }
        await new Promise(r => setTimeout(r, 1000));
    }
    return false;
};

export const startIsolatedEnvironment = async (port = 5001): Promise<IsolatedEnvironment> => {
    const timestamp = generateTimestamp();
    const tempDir = join(tmpdir(), `yaffo_test_${timestamp}`);

    console.log(`\n🔧 Setting up isolated test environment...`);
    console.log(`   Temp directory: ${tempDir}`);

    mkdirSync(join(tempDir, "organized"), {recursive: true});
    mkdirSync(join(tempDir, "thumbnails"), {recursive: true});
    mkdirSync(join(tempDir, "temp"), {recursive: true});
    mkdirSync(join(tempDir, "duplicates"), {recursive: true});

    const testPhotosDir = join(UI_TESTS_DIR, "test_data", "photos");
    if (existsSync(testPhotosDir)) {
        cpSync(testPhotosDir, join(tempDir, "organized"), {recursive: true});
        console.log(`   ✅ Copied test photos`);
    }

    const testDbPath = join(UI_TESTS_DIR, "test_data", "database", "yaffo.db");
    if (existsSync(testDbPath)) {
        cpSync(testDbPath, join(tempDir, "yaffo.db"));
        console.log(`   ✅ Copied test database`);
    } else {
        writeFileSync(join(tempDir, "yaffo.db"), "");
        console.log(`   ✅ Created empty database`);
    }
    writeFileSync(join(tempDir, "yaffo-huey.db"), "");

    const env = {
        ...process.env,
        YAFFO_DATA_DIR: tempDir,
        FLASK_APP: "yaffo.app:create_app",
        FLASK_ENV: "testing",
        VIRTUAL_ENV: join(YAFFO_DIR, "venv"),
        PATH: `${join(YAFFO_DIR, "venv", "bin")}:${process.env.PATH}`,
    };

    console.log(`\n📦 Indexing test photos...`);
    try {
        execSync(`python "${join(SCRIPTS_DIR, "seed_database.py")}"`, {
            env,
            cwd: YAFFO_DIR,
            stdio: "inherit",
        });
    } catch (e) {
        console.error(`   ⚠️ Warning: seed_database.py failed: ${e}`);
    }

    console.log(`\n🚀 Starting Flask on port ${port}...`);
    const flaskProcess = spawn(
        "python",
        ["-m", "flask", "run", "--host=127.0.0.1", `--port=${port}`, "--no-reload"],
        {
            env,
            cwd: YAFFO_DIR,
            stdio: ["ignore", "pipe", "pipe"],
        }
    );

    let flaskOutput = "";
    flaskProcess.stdout?.on("data", (data) => {
        flaskOutput += data.toString();
    });
    flaskProcess.stderr?.on("data", (data) => {
        flaskOutput += data.toString();
    });

    const baseUrl = `http://127.0.0.1:${port}`;
    console.log(`   Waiting for Flask to be ready...`);

    const isReady = await waitForServer(baseUrl);
    if (!isReady) {
        console.error(`   ❌ Flask failed to start. Output:`);
        console.error(flaskOutput);
        flaskProcess.kill();
        throw new Error("Flask server failed to start");
    }

    console.log(`   ✅ Flask is ready at ${baseUrl}`);

    const cleanup = async () => {
        console.log(`\n🧹 Cleaning up isolated environment...`);
        if (flaskProcess && !flaskProcess.killed) {
            flaskProcess.kill();
            console.log(`   ✅ Stopped Flask server`);
        }
        if (existsSync(tempDir)) {
            rmSync(tempDir, {recursive: true, force: true});
            console.log(`   ✅ Removed temp directory`);
        }
    };

    return {
        tempDir,
        port,
        baseUrl,
        flaskProcess,
        cleanup,
    };
};

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
            lines.push(`            <duration>${test.duration}ms</duration>`);
            if (test.error) {
                lines.push("            <error>");
                lines.push(`                <message>${escapeXml(test.error.message)}</message>`);
                if (test.error.stack) {
                    lines.push(`                <stack>${escapeXml(test.error.stack)}</stack>`);
                }
                lines.push("            </error>");
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
        "--reporter=json,list",
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

// CLI entry point
async function main() {
    const args = process.argv.slice(2);

    if (args.includes("--help") || args.includes("-h")) {
        console.log("Usage: npx tsx lib/test_generator/isolated_runner.ts [test-files...] [options]");
        console.log("");
        console.log("Run Playwright tests against an isolated Flask instance.");
        console.log("");
        console.log("Arguments:");
        console.log("  [test-files...]    Optional test files to run (default: all tests)");
        console.log("");
        console.log("Options:");
        console.log("  -p, --port <port>  Port for isolated Flask server (default: 5001)");
        console.log("  -h, --help         Show this help message");
        console.log("");
        console.log("Examples:");
        console.log("  npx tsx lib/test_generator/isolated_runner.ts");
        console.log("  npx tsx lib/test_generator/isolated_runner.ts generated_tests/photo-gallery.spec.ts");
        console.log("  npx tsx lib/test_generator/isolated_runner.ts --port 5002");
        process.exit(0);
    }

    const portIndex = args.findIndex(a => a === "--port" || a === "-p");
    const port = portIndex !== -1 && args[portIndex + 1]
        ? parseInt(args[portIndex + 1], 10)
        : 5001;
    let environment: IsolatedEnvironment | undefined;
    const handleCleanup = async () => {
        if (environment) {
            await environment?.cleanup();
        }
        process.exit(0);
    };
    process.on('SIGINT', handleCleanup);
    process.on('SIGTERM', handleCleanup);

    try {
        environment = await startIsolatedEnvironment(port);
    } catch (e) {
        console.error("failed to start environment", e);
        process.exit(1);
    }
}

// Run if executed directly
const isDirectRun = process.argv[1]?.includes("isolated_runner");
if (isDirectRun) {
    main().catch((e) => {
        console.error(`Fatal error: ${e instanceof Error ? e.message : String(e)}`);
        process.exit(1);
    });
}
