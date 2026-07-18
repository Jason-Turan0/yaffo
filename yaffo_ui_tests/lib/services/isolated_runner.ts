import {ChildProcess, execSync, spawn} from "child_process";
import {join, resolve} from "path";
import {cpSync, existsSync, mkdirSync, readFileSync, realpathSync, renameSync, rmSync, writeFileSync} from "fs";
import {tmpdir} from "os";

// Resolved temp root: on macOS tmpdir() is /var/folders/… but /var is a symlink
// to /private/var. The p2p grant query prefix-matches media_dir.path.resolve()
// against MediaItem.full_file_path, so the data dir must be symlink-free or a
// media-dir grant matches zero files.
const TEMP_ROOT = realpathSync(tmpdir());

export interface IsolatedInstance {
    tempDir: string;
    port: number;
    baseUrl: string;
    flaskProcess: ChildProcess | null;
    taskqProcess: ChildProcess | null;
}

export interface IsolatedEnvironment extends IsolatedInstance {
    /** Second instance ("instance B" in specs/sharing.yaml) when started with withPeer. */
    peer?: IsolatedInstance;
    cleanup: () => Promise<void>;
}

export interface IsolatedEnvironmentOptions {
    /**
     * Also start a second, minimally-seeded instance (schema + download
     * directory, no media library) on port+1, and enable LAN-only p2p sharing
     * on BOTH instances: YAFFO_P2P_ENABLED=1, distinct YAFFO_P2P_PORT UDP
     * ports, an unreachable YAFFO_HUB_URL (the sharing tests pair over mDNS,
     * hubless by design), and YAFFO_P2P_EPHEMERAL_IDENTITY=1 so throwaway
     * instances never write device keys into the real OS keychain.
     */
    withPeer?: boolean;
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

// UDP ports for the p2p QUIC transport, well away from the web ports.
const quicPortFor = (webPort: number): number => webPort + 10_000;
// ws:// to a discard port: connects fail fast, so the instances behave as
// "no hub reachable" (the sharing spec's required environment).
const UNREACHABLE_HUB_URL = "ws://127.0.0.1:9";

const generateTimestamp = (): string => {
    const now = new Date();
    return now.toISOString().replace(/[-:]/g, "").replace("T", "_").slice(0, 15);
};

// kill() only sends the signal; the process can still be running (and holding
// files in the temp dir) when it returns. Resolve once the child has actually
// exited, escalating to SIGKILL if it ignores SIGTERM.
const waitForExit = (child: ChildProcess | null, timeoutMs = 10_000): Promise<void> => {
    if (!child || child.exitCode !== null || child.signalCode !== null) {
        return Promise.resolve();
    }
    return new Promise((resolve) => {
        const forceKill = setTimeout(() => child.kill("SIGKILL"), timeoutMs);
        child.once("exit", () => {
            clearTimeout(forceKill);
            resolve();
        });
    });
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

interface StartInstanceOptions {
    label: string;
    port: number;
    tempDir: string;
    /** Copy the sample library and run the full seed (photos, faces, album, …). */
    seedLibrary: boolean;
    /**
     * Move two photos into organized/shared_trip so the folder-share grant
     * tests have a real subfolder. Ids stay stable: the seed indexes by
     * BASENAME order, which a move does not change.
     */
    sharedSubfolder?: boolean;
    /**
     * Env vars for the FLASK process only (the p2p wiring). The seed script and
     * the taskq host also call create_app; giving them YAFFO_P2P_ENABLED too
     * makes three processes race for the instance's one QUIC UDP port, and the
     * web process — the only one whose engine the sharing UI can use — can lose.
     */
    flaskOnlyEnv?: Record<string, string>;
}

// The photos carved out into organized/shared_trip for folder-share tests.
export const SHARED_TRIP_PHOTOS = ["whitehouse_2014_01282014.jpg", "whitehouse_2014_03012014.jpg"];

const startInstance = async (options: StartInstanceOptions): Promise<IsolatedInstance> => {
    const {label, port, tempDir, seedLibrary, sharedSubfolder = false, flaskOnlyEnv = {}} = options;

    console.log(`\n🔧 Setting up isolated instance ${label}...`);
    console.log(`   Temp directory: ${tempDir}`);

    mkdirSync(join(tempDir, "organized"), {recursive: true});
    mkdirSync(join(tempDir, "thumbnails"), {recursive: true});
    mkdirSync(join(tempDir, "temp"), {recursive: true});
    mkdirSync(join(tempDir, "duplicates"), {recursive: true});

    if (seedLibrary) {
        const testPhotosDir = join(UI_TESTS_DIR, "test_data", "photos");
        const testVideoDir = join(UI_TESTS_DIR, "test_data", "mp4");
        if (existsSync(testPhotosDir)) {
            cpSync(testPhotosDir, join(tempDir, "organized"), {recursive: true});
            cpSync(testVideoDir, join(tempDir, "organized"), {recursive: true});
            console.log(`   ✅ Copied test photos/videos`);
        }
        if (sharedSubfolder) {
            const subfolder = join(tempDir, "organized", "shared_trip");
            mkdirSync(subfolder, {recursive: true});
            for (const name of SHARED_TRIP_PHOTOS) {
                const source = join(tempDir, "organized", name);
                if (existsSync(source)) {
                    renameSync(source, join(subfolder, name));
                }
            }
            console.log(`   ✅ Carved out organized/shared_trip (${SHARED_TRIP_PHOTOS.length} photos)`);
        }

        const testDbPath = join(UI_TESTS_DIR, "test_data", "database", "yaffo.db");
        if (existsSync(testDbPath)) {
            cpSync(testDbPath, join(tempDir, "yaffo.db"));
            console.log(`   ✅ Copied test database`);
        } else {
            writeFileSync(join(tempDir, "yaffo.db"), "");
            console.log(`   ✅ Created empty database`);
        }
    } else {
        writeFileSync(join(tempDir, "yaffo.db"), "");
        console.log(`   ✅ Created empty peer database`);
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
    const flaskEnv = {...env, ...flaskOnlyEnv};

    const seedScript = seedLibrary ? "seed_database.py" : "seed_peer_database.py";
    console.log(`\n📦 Seeding instance ${label} (${seedScript})...`);
    try {
        execSync(`python "${join(SCRIPTS_DIR, seedScript)}"`, {
            env,
            cwd: YAFFO_DIR,
            stdio: "inherit",
        });
    } catch (e) {
        console.error(`   ⚠️ Warning: ${seedScript} failed: ${e}`);
    }

    // Face assignment (and other write flows) enqueue background tasks; without
    // the taskq host they'd sit in the queue forever and the UI would never
    // reflect the change.
    console.log(`\n⚙️ Starting taskq host for instance ${label}...`);
    const taskqProcess = spawn(
        "python",
        ["-m", "yaffo.taskq.host"],
        {
            env,
            cwd: YAFFO_DIR,
            stdio: ["ignore", "pipe", "pipe"],
        }
    );

    console.log(`\n🚀 Starting Flask for instance ${label} on port ${port}...`);
    const flaskProcess = spawn(
        "python",
        ["-m", "flask", "run", "--host=127.0.0.1", `--port=${port}`, "--no-reload"],
        {
            env: flaskEnv,
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
    console.log(`   Waiting for Flask (instance ${label}) to be ready...`);

    const isReady = await waitForServer(baseUrl);
    if (!isReady) {
        console.error(`   ❌ Flask (instance ${label}) failed to start. Output:`);
        console.error(flaskOutput);
        flaskProcess.kill();
        taskqProcess.kill();
        throw new Error(`Flask server (instance ${label}) failed to start`);
    }

    console.log(`   ✅ Instance ${label} is ready at ${baseUrl}`);
    return {tempDir, port, baseUrl, flaskProcess, taskqProcess};
};

const stopInstance = async (instance: IsolatedInstance | undefined): Promise<void> => {
    if (!instance) {
        return;
    }
    if (instance.flaskProcess && !instance.flaskProcess.killed) {
        instance.flaskProcess.kill();
    }
    if (instance.taskqProcess && !instance.taskqProcess.killed) {
        instance.taskqProcess.kill();
    }
    // Don't delete the temp dir out from under live processes — they hold
    // the SQLite DBs inside it open (that's a disk I/O error waiting to happen).
    await Promise.all([waitForExit(instance.flaskProcess), waitForExit(instance.taskqProcess)]);
    if (existsSync(instance.tempDir)) {
        rmSync(instance.tempDir, {recursive: true, force: true, maxRetries: 5, retryDelay: 100});
    }
};

export const startIsolatedEnvironment = async (
    port = 5001,
    options: IsolatedEnvironmentOptions = {},
): Promise<IsolatedEnvironment> => {
    const timestamp = generateTimestamp();
    const tempDir = join(TEMP_ROOT, `yaffo_test_${timestamp}`);
    const {withPeer = false} = options;

    // With a peer, BOTH instances run the p2p engine so they can pair over the
    // LAN (mDNS). Ephemeral identities keep device keys out of the OS keychain.
    const p2pEnv = (webPort: number): Record<string, string> => ({
        YAFFO_P2P_ENABLED: "1",
        YAFFO_P2P_PORT: String(quicPortFor(webPort)),
        YAFFO_HUB_URL: UNREACHABLE_HUB_URL,
        YAFFO_P2P_EPHEMERAL_IDENTITY: "1",
    });

    const primary = await startInstance({
        label: "A",
        port,
        tempDir,
        seedLibrary: true,
        sharedSubfolder: withPeer,
        flaskOnlyEnv: withPeer ? p2pEnv(port) : {},
    });

    let peer: IsolatedInstance | undefined;
    if (withPeer) {
        const peerPort = port + 1;
        try {
            peer = await startInstance({
                label: "B (peer)",
                port: peerPort,
                tempDir: join(TEMP_ROOT, `yaffo_test_${timestamp}_peer`),
                seedLibrary: false,
                flaskOnlyEnv: p2pEnv(peerPort),
            });
        } catch (e) {
            await stopInstance(primary);
            throw e;
        }
        console.log(`\n🔗 Sharing sandbox up: A=${primary.baseUrl} B=${peer.baseUrl} (hub unreachable, LAN pairing only)`);
    }

    const cleanup = async () => {
        console.log(`\n🧹 Cleaning up isolated environment...`);
        await Promise.all([stopInstance(primary), stopInstance(peer)]);
        console.log(`   ✅ Stopped Flask server(s) and taskq host(s), removed temp dir(s)`);
    };

    return {
        ...primary,
        peer,
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
    const withPeer = args.includes("--peer");
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
        environment = await startIsolatedEnvironment(port, {withPeer});
        if (environment.peer) {
            console.log(`\nRun the sharing tests with: BASE_URL=${environment.baseUrl} PEER_URL=${environment.peer.baseUrl} npx playwright test generated_tests/sharing/sharing.spec.ts`);
        }
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
