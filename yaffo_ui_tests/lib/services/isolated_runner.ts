import {ChildProcess, execSync, spawn} from "child_process";
import {randomBytes} from "crypto";
import {join, resolve} from "path";
import {cpSync, existsSync, mkdirSync, realpathSync, rmSync, writeFileSync} from "fs";
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
     * Also start a second instance seeded with the Obama fixture library on
     * port+1, and enable LAN-only p2p sharing
     * on BOTH instances: YAFFO_P2P_ENABLED=1, distinct YAFFO_P2P_PORT UDP
     * ports, an unreachable YAFFO_HUB_URL (the sharing tests pair over mDNS,
     * hubless by design), and YAFFO_P2P_EPHEMERAL_IDENTITY=1 so throwaway
     * instances never write device keys into the real OS keychain.
     */
    withPeer?: boolean;
    /**
     * Serve the isolated instances with Yaffo's public-demo boundary enabled.
     * Instance A uses the source role and an optional instance B uses receiver.
     * Demo instances intentionally omit the taskq host.
     */
    demoMode?: boolean;
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
    /** Fixture directory copied into organized before running the full seed. */
    fixtureDir: string;
    /** Selects fixture-specific database records created by the seed script. */
    seedProfile: "bennett" | "obama";
    /** Include the separate video fixtures in this instance. */
    includeVideos?: boolean;
    /** Public-demo role. When omitted, the instance runs in normal test mode. */
    demoRole?: "source" | "receiver";
    /**
     * Env vars for the FLASK process only (the p2p wiring). The seed script and
     * the taskq host also call create_app; giving them YAFFO_P2P_ENABLED too
     * makes three processes race for the instance's one QUIC UDP port, and the
     * web process — the only one whose engine the sharing UI can use — can lose.
     */
    flaskOnlyEnv?: Record<string, string>;
}

export const PRIMARY_FIXTURE_DIR = join(UI_TESTS_DIR, "test_data", "bennett");
export const PEER_FIXTURE_DIR = join(UI_TESTS_DIR, "test_data", "obama", "images");

// The Bennett fixture already has a real trip folder, so sharing tests can exercise a
// scoped folder grant without mutating the copied fixture tree.
export const SHARED_TRIP_FOLDER = "2015_chicago_baby_trip";
export const SHARED_TRIP_PHOTOS = [
    "2015-10-09_103400_chicago-riverwalk.png",
    "2015-10-09_151800_lakefront.png",
    "2015-10-10_110700_neighborhood-walk.png",
    "2015-10-11_085600_family-breakfast.png",
];

const startInstance = async (options: StartInstanceOptions): Promise<IsolatedInstance> => {
    const {
        label,
        port,
        tempDir,
        fixtureDir,
        seedProfile,
        includeVideos = false,
        demoRole,
        flaskOnlyEnv = {},
    } = options;

    console.log(`\n🔧 Setting up isolated instance ${label}...`);
    console.log(`   Temp directory: ${tempDir}`);

    mkdirSync(join(tempDir, "organized"), {recursive: true});
    mkdirSync(join(tempDir, "thumbnails"), {recursive: true});
    mkdirSync(join(tempDir, "temp"), {recursive: true});
    mkdirSync(join(tempDir, "duplicates"), {recursive: true});

    if (!existsSync(fixtureDir)) {
        throw new Error(`Fixture directory does not exist: ${fixtureDir}`);
    }
    cpSync(fixtureDir, join(tempDir, "organized"), {recursive: true});
    console.log(`   ✅ Copied photo fixture: ${fixtureDir}`);
    if (includeVideos) {
        const testVideoDir = join(UI_TESTS_DIR, "test_data", "mp4");
        cpSync(testVideoDir, join(tempDir, "organized"), {recursive: true});
        console.log(`   ✅ Copied video fixtures`);
    }

    writeFileSync(join(tempDir, "yaffo.db"), "");
    console.log(`   ✅ Created empty database`);
    writeFileSync(join(tempDir, "yaffo-huey.db"), "");

    const env = {
        ...process.env,
        YAFFO_DATA_DIR: tempDir,
        YAFFO_SEED_PROFILE: seedProfile,
        FLASK_APP: "yaffo.app:create_app",
        FLASK_ENV: "testing",
        // Demo mode applies to the served app, not fixture construction. These
        // overrides also prevent an ambient shell variable from changing how
        // the seed script or normal isolated environments behave.
        YAFFO_DEMO_MODE: "0",
        YAFFO_DEMO_ROLE: "",
        VIRTUAL_ENV: join(YAFFO_DIR, "venv"),
        PATH: `${join(YAFFO_DIR, "venv", "bin")}:${process.env.PATH}`,
    };
    const demoEnv = demoRole ? {
        YAFFO_DEMO_MODE: "1",
        YAFFO_DEMO_ROLE: demoRole,
        // Demo startup rejects the repository's development secret. Each
        // disposable instance gets a different process-local signing key.
        SECRET_KEY: randomBytes(32).toString("hex"),
    } : {};
    const flaskEnv = {...env, ...flaskOnlyEnv, ...demoEnv};

    const seedScript = "seed_database.py";
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

    let taskqProcess: ChildProcess | null = null;
    if (demoRole) {
        console.log(`\n🚫 Demo mode: taskq host omitted for instance ${label}`);
    } else {
        // Face assignment (and other write flows) enqueue background tasks;
        // without the taskq host they'd sit in the queue forever and the UI
        // would never reflect the change.
        console.log(`\n⚙️ Starting taskq host for instance ${label}...`);
        taskqProcess = spawn(
            "python",
            ["-m", "yaffo.taskq.host"],
            {
                env,
                cwd: YAFFO_DIR,
                stdio: ["ignore", "pipe", "pipe"],
            }
        );
    }

    const modeLabel = demoRole ? ` in demo mode (${demoRole})` : "";
    console.log(`\n🚀 Starting Flask for instance ${label}${modeLabel} on port ${port}...`);
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
        taskqProcess?.kill();
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
    const {withPeer = false, demoMode = false} = options;

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
        fixtureDir: PRIMARY_FIXTURE_DIR,
        seedProfile: "bennett",
        includeVideos: true,
        demoRole: demoMode ? "source" : undefined,
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
                fixtureDir: PEER_FIXTURE_DIR,
                seedProfile: "obama",
                demoRole: demoMode ? "receiver" : undefined,
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
        console.log(`   ✅ Stopped isolated process(es), removed temp dir(s)`);
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
    const demoMode = args.includes("--demo");
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
        environment = await startIsolatedEnvironment(port, {withPeer, demoMode});
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
