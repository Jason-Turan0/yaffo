import {ChildProcess, execSync, spawn} from "child_process";
import {join, resolve} from "path";
import {cpSync, existsSync, mkdirSync, readFileSync, rmSync, writeFileSync} from "fs";
import {tmpdir} from "os";

export interface IsolatedEnvironment {
    tempDir: string;
    port: number;
    baseUrl: string;
    flaskProcess: ChildProcess | null;
    taskqProcess: ChildProcess | null;
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
    errors?: {
        location?: {
            file?: string,
            column?: number,
            line?: number
        },
        message?: string
    }[]
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

    // Face assignment (and other write flows) enqueue background tasks; without
    // the taskq host they'd sit in the queue forever and the UI would never
    // reflect the change.
    console.log(`\n⚙️ Starting taskq host...`);
    const taskqProcess = spawn(
        "python",
        ["-m", "yaffo.taskq.host"],
        {
            env,
            cwd: YAFFO_DIR,
            stdio: ["ignore", "pipe", "pipe"],
        }
    );

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
        taskqProcess.kill();
        throw new Error("Flask server failed to start");
    }

    console.log(`   ✅ Flask is ready at ${baseUrl}`);

    const cleanup = async () => {
        console.log(`\n🧹 Cleaning up isolated environment...`);
        if (flaskProcess && !flaskProcess.killed) {
            flaskProcess.kill();
            console.log(`   ✅ Stopped Flask server`);
        }
        if (taskqProcess && !taskqProcess.killed) {
            taskqProcess.kill();
            console.log(`   ✅ Stopped taskq host`);
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
        taskqProcess,
        cleanup,
    };
};

// CLI entry point
async function main() {
    const args = process.argv.slice(2);
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
