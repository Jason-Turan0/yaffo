import {spawn, ChildProcess, execSync} from "child_process";
import {join, resolve} from "path";
import {mkdirSync, existsSync, cpSync, writeFileSync, rmSync} from "fs";
import {tmpdir} from "os";

export interface IsolatedEnvironment {
    tempDir: string;
    port: number;
    baseUrl: string;
    flaskProcess: ChildProcess | null;
    cleanup: () => void;
}

export interface TestRunResult {
    success: boolean;
    exitCode: number;
    output: string;
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

    const cleanup = () => {
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

export const runPlaywrightTests = async (
    baseUrl: string,
    testFiles?: string[]
): Promise<TestRunResult> => {
    console.log(`\n🧪 Running Playwright tests...`);

    const args = ["playwright", "test", "--project=chromium"];

    if (testFiles && testFiles.length > 0) {
        args.push(...testFiles);
    }

    const env = {
        ...process.env,
        BASE_URL: baseUrl,
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
            resolve({
                success: exitCode === 0,
                exitCode,
                output,
            });
        });
    });
};

export const runIsolatedTests = async (
    testFiles?: string[],
    port = 5001
): Promise<TestRunResult> => {
    let environment: IsolatedEnvironment | null = null;

    try {
        environment = await startIsolatedEnvironment(port);
        const result = await runPlaywrightTests(environment.baseUrl, testFiles);
        return result;
    } finally {
        environment?.cleanup();
    }
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

    const testFiles = args.filter((a, i) =>
        !a.startsWith("-") &&
        (portIndex === -1 || (i !== portIndex && i !== portIndex + 1))
    );

    console.log(`Running isolated tests on port ${port}`);
    if (testFiles.length > 0) {
        console.log(`Test files: ${testFiles.join(", ")}`);
    } else {
        console.log(`Running all tests`);
    }

    const result = await runIsolatedTests(testFiles.length > 0 ? testFiles : undefined, port);

    if (result.success) {
        console.log(`\n✅ All tests passed!`);
        process.exit(0);
    } else {
        console.log(`\n❌ Tests failed with exit code: ${result.exitCode}`);
        process.exit(result.exitCode);
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
