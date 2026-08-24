import {ChildProcess, execFileSync, execSync, spawn} from "child_process";
import {randomBytes} from "crypto";
import {join, resolve} from "path";
import {cpSync, existsSync, mkdirSync, realpathSync, rmSync, writeFileSync} from "fs";
import {tmpdir} from "os";
import {createServer} from "net";

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
    /** Preseeded cache dirs are shared/canonical, so cleanup leaves them intact. */
    keepData?: boolean;
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
    /**
     * Serve the canonical seed-cache data dirs (restored beforehand) instead of
     * seeding fresh. Skips the expensive indexing/face/label pipeline.
     */
    preseeded?: boolean;
    /**
     * Copy the preseeded cache into disposable data dirs before serving it.
     * Concurrent local suites need this: unlike CI jobs, local processes share
     * a filesystem and must not write to the same cached SQLite database.
     */
    copyPreseeded?: boolean;
    /** Serve the reproducible documentation fixture instead of the UI-test seed. */
    docsFixture?: boolean;
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

/**
 * Fixed across macOS and Linux so paths rendered by Settings have identical pixels.
 * The cache is restored here before serving; unlike ordinary test sandboxes it is not
 * an ephemeral timestamped directory.
 */
export const DOCS_DATA_DIR = process.env.YAFFO_DOCS_DATA_DIR ||
    (process.platform === "win32" ? join(TEMP_ROOT, "yaffo-docs") : "/tmp/yaffo-docs");
export const DOCS_MEDIA_DIR = join(DOCS_DATA_DIR, "Family Photos");
export const DOCS_DUPLICATE_SCAN_DIR = join(DOCS_DATA_DIR, "Duplicate Scan Samples");

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

/** Refuse to mistake an already-running local app for the process just spawned. */
export const isTcpPortAvailable = (port: number): Promise<boolean> => new Promise((resolvePromise) => {
    const server = createServer();
    server.unref();
    server.once("error", () => resolvePromise(false));
    server.listen({host: "127.0.0.1", port, exclusive: true}, () => {
        server.close(() => resolvePromise(true));
    });
});

const portInUseError = (port: number): Error => new Error(
    `Port ${port} is already in use; choose another sandbox range with ` +
    "TEST_SANDBOX_BASE_PORT or --base-port"
);

interface StartInstanceOptions {
    label: string;
    port: number;
    tempDir: string;
    /** Fixture directory copied into Family Photos before running the full seed. */
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
    /**
     * The data dir is already seeded (restored from the seed cache): skip
     * provisioning and serve it directly. The dir must be at the same absolute
     * path it was seeded at, and is not deleted on cleanup.
     */
    preseeded?: boolean;
    /** Delete this already-seeded data dir when the instance stops. */
    disposableData?: boolean;
    /** Optional immutable model/binary root shared by copied seed sandboxes. */
    assetDir?: string;
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

// Baseline env for the seed script, taskq host, and Flask process of an
// instance. Kept identical between build-seed and serve so a data dir seeded in
// one job serves correctly in another (the seeded DB stores absolute
// full_file_path values under YAFFO_DATA_DIR).
const instanceEnv = (
    tempDir: string,
    seedProfile: "bennett" | "obama",
    assetDir?: string,
): Record<string, string> => ({
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
    ...(assetDir ? {YAFFO_ASSET_DIR: assetDir} : {}),
    VIRTUAL_ENV: join(YAFFO_DIR, "venv"),
    PATH: `${join(YAFFO_DIR, "venv", "bin")}:${process.env.PATH}`,
});

interface ProvisionOptions {
    label: string;
    tempDir: string;
    fixtureDir: string;
    seedProfile: "bennett" | "obama";
    includeVideos?: boolean;
    /** Index videos nested in the photo fixture, used by the docs composition. */
    recursiveVideos?: boolean;
    /** Fail fixture construction instead of preserving the UI-test runner's warning. */
    strictSeed?: boolean;
}

/**
 * Copy fixtures into a data dir and run the (expensive) seed: indexing, face
 * analysis, and label classification. This is the part the seed cache captures
 * so per-spec environments can skip it. The resulting data dir is self-contained
 * and portable only to the SAME absolute path it was built at, because the
 * seeded DB stores absolute media paths.
 */
export const provisionInstanceData = (options: ProvisionOptions): void => {
    const {label, tempDir, fixtureDir, seedProfile, includeVideos = false,
           recursiveVideos = false, strictSeed = false} = options;
    const mediaDir = join(tempDir, "Family Photos");

    console.log(`\n🔧 Provisioning data dir for instance ${label}...`);
    console.log(`   Data directory: ${tempDir}`);

    mkdirSync(mediaDir, {recursive: true});
    mkdirSync(join(tempDir, "thumbnails"), {recursive: true});
    mkdirSync(join(tempDir, "temp"), {recursive: true});
    mkdirSync(join(tempDir, "duplicates"), {recursive: true});

    if (!existsSync(fixtureDir)) {
        throw new Error(`Fixture directory does not exist: ${fixtureDir}`);
    }
    cpSync(fixtureDir, mediaDir, {recursive: true});
    console.log(`   ✅ Copied photo fixture: ${fixtureDir}`);
    if (includeVideos) {
        const testVideoDir = join(UI_TESTS_DIR, "test_data", "mp4");
        cpSync(testVideoDir, mediaDir, {recursive: true});
        console.log(`   ✅ Copied video fixtures`);
    }

    writeFileSync(join(tempDir, "yaffo.db"), "");
    console.log(`   ✅ Created empty database`);
    writeFileSync(join(tempDir, "yaffo-huey.db"), "");

    const seedScript = "seed_database.py";
    console.log(`\n📦 Seeding instance ${label} (${seedScript})...`);
    try {
        execSync(`python "${join(SCRIPTS_DIR, seedScript)}"`, {
            env: {
                ...instanceEnv(tempDir, seedProfile),
                YAFFO_SEED_RECURSIVE_VIDEOS: recursiveVideos ? "1" : "0",
            },
            cwd: YAFFO_DIR,
            stdio: "inherit",
        });
    } catch (e) {
        console.error(`   ⚠️ Warning: ${seedScript} failed: ${e}`);
        if (strictSeed) throw e;
    }
};

/**
 * Build the docs-grade fixture without the synthetic duplicate-test videos. The
 * Bennett fixture already contains a short real beach video, so capture still covers
 * video cards and playback while the gallery remains presentable.
 *
 * Duplicate review needs something useful to find, but putting duplicates inside
 * Family Photos would pollute the gallery and index. Stage two pairs of real images
 * beside the indexed library instead: the utility can scan them explicitly, while
 * every other guide page continues to see one canonical copy of each photo.
 */
export const buildDocumentationFixture = (dataDir = DOCS_DATA_DIR): string => {
    rmSync(dataDir, {recursive: true, force: true});
    provisionInstanceData({
        label: "docs",
        tempDir: dataDir,
        fixtureDir: PRIMARY_FIXTURE_DIR,
        seedProfile: "bennett",
        includeVideos: false,
        recursiveVideos: true,
        strictSeed: true,
    });
    const mediaDir = join(dataDir, "Family Photos");
    const duplicateDir = join(dataDir, "Duplicate Scan Samples");
    mkdirSync(duplicateDir, {recursive: true});
    const samples = [
        {
            source: join(mediaDir, "2017_third_birthday", "2017-09-12_162200_blowing-candles.png"),
            names: ["01-birthday-a.png", "01-birthday-b.png"],
        },
        {
            source: join(mediaDir, "2021_gulf_beach_trip", "2021-07-10_134200_family-sandcastle.png"),
            names: ["02-beach-a.png", "02-beach-b.png"],
        },
    ];
    for (const sample of samples) {
        for (const name of sample.names) cpSync(sample.source, join(duplicateDir, name));
    }
    console.log(`   ✅ Staged ${samples.length} duplicate-review groups outside the indexed library`);
    return dataDir;
};

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
        preseeded = false,
        disposableData = false,
        assetDir,
    } = options;

    if (!(await isTcpPortAvailable(port))) {
        throw portInUseError(port);
    }

    if (preseeded) {
        // Serve a data dir that was already seeded (restored from the seed
        // cache). It must live at the same absolute path it was built at.
        if (!existsSync(join(tempDir, "yaffo.db"))) {
            throw new Error(`Preseeded data dir has no yaffo.db: ${tempDir}`);
        }
        console.log(`\n♻️  Instance ${label}: serving preseeded data dir ${tempDir}`);
    } else {
        provisionInstanceData({label, tempDir, fixtureDir, seedProfile, includeVideos});
    }

    const env = instanceEnv(tempDir, seedProfile, assetDir);
    const demoEnv = demoRole ? {
        YAFFO_DEMO_MODE: "1",
        YAFFO_DEMO_ROLE: demoRole,
        // Demo startup rejects the repository's development secret. Each
        // disposable instance gets a different process-local signing key.
        SECRET_KEY: randomBytes(32).toString("hex"),
    } : {};
    const flaskEnv = {...env, ...flaskOnlyEnv, ...demoEnv};

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
        // Loopback by default. Containerized docs capture sets YAFFO_SANDBOX_HOST=0.0.0.0
        // so the container can reach the app through host.docker.internal — on macOS a
        // container cannot see the host's loopback, and --network host joins the Linux
        // VM rather than the Mac. Opt-in, because 0.0.0.0 exposes the sandbox on the LAN.
        ["-m", "flask", "run", `--host=${process.env.YAFFO_SANDBOX_HOST || "127.0.0.1"}`,
         `--port=${port}`, "--no-reload"],
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
    // A different process on the requested port can make the HTTP probe pass;
    // the child itself must still be alive before this is our environment.
    await new Promise(r => setTimeout(r, 50));
    if (!isReady || flaskProcess.exitCode !== null || flaskProcess.signalCode !== null) {
        console.error(`   ❌ Flask (instance ${label}) failed to start. Output:`);
        console.error(flaskOutput);
        flaskProcess.kill();
        taskqProcess?.kill();
        await Promise.all([waitForExit(flaskProcess), waitForExit(taskqProcess)]);
        if (disposableData && existsSync(tempDir)) {
            rmSync(tempDir, {recursive: true, force: true, maxRetries: 5, retryDelay: 100});
        }
        throw new Error(`Flask server (instance ${label}) failed to start`);
    }

    console.log(`   ✅ Instance ${label} is ready at ${baseUrl}`);
    return {tempDir, port, baseUrl, flaskProcess, taskqProcess, keepData: preseeded && !disposableData};
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
    // Preseeded cache dirs are canonical and reused; only ephemeral temp dirs
    // are removed.
    if (!instance.keepData && existsSync(instance.tempDir)) {
        rmSync(instance.tempDir, {recursive: true, force: true, maxRetries: 5, retryDelay: 100});
    }
};

// With a peer, BOTH instances run the p2p engine so they can pair over the LAN
// (mDNS). Ephemeral identities keep device keys out of the OS keychain.
const p2pEnv = (webPort: number): Record<string, string> => ({
    YAFFO_P2P_ENABLED: "1",
    YAFFO_P2P_PORT: String(quicPortFor(webPort)),
    YAFFO_HUB_URL: UNREACHABLE_HUB_URL,
    YAFFO_P2P_EPHEMERAL_IDENTITY: "1",
});

// Canonical seed-cache locations. The seed writes absolute media paths into the
// DB, so a cached data dir only serves correctly when restored to the very path
// it was built at — hence fixed, symlink-free constants rather than temp dirs.
// YAFFO_SEED_CACHE_ROOT pins the root explicitly (CI) so build and restore jobs
// agree regardless of TMPDIR; it defaults to a stable path under the temp root.
const SEED_CACHE_ROOT = process.env.YAFFO_SEED_CACHE_ROOT || join(TEMP_ROOT, "yaffo-seed");
export const seedCacheDir = (role: "primary" | "peer"): string =>
    join(SEED_CACHE_ROOT, role === "primary" ? "a" : "b");

/**
 * Make a writable, disposable copy of one canonical seed cache. The clone
 * helper also rebases the absolute paths stored in yaffo.db from the cache
 * root to the clone root.
 */
export const copySeedCache = (role: "primary" | "peer", destination: string): string => {
    const source = seedCacheDir(role);
    if (!existsSync(join(source, "yaffo.db"))) {
        throw new Error(`Preseeded data dir has no yaffo.db: ${source}`);
    }
    execFileSync("python", [join(SCRIPTS_DIR, "clone_seed_cache.py"), source, destination], {
        cwd: YAFFO_DIR,
        stdio: "inherit",
    });
    return destination;
};

/**
 * Build the seed cache: provision + seed the primary (bennett) data dir, and
 * optionally the peer (obama) one, into their canonical locations, then return
 * without serving. Runs the real indexing/face/label pipeline, so the cache is
 * only rebuilt when its inputs change.
 */
export const buildSeedCache = (options: {withPeer?: boolean; docsFixture?: boolean} = {}): {primary: string; peer?: string} => {
    const {withPeer = false, docsFixture = false} = options;
    if (docsFixture) {
        if (withPeer) throw new Error("The documentation fixture does not have a peer instance");
        return {primary: buildDocumentationFixture()};
    }
    const primaryDir = seedCacheDir("primary");
    rmSync(primaryDir, {recursive: true, force: true});
    provisionInstanceData({
        label: "A",
        tempDir: primaryDir,
        fixtureDir: PRIMARY_FIXTURE_DIR,
        seedProfile: "bennett",
        includeVideos: true,
    });
    if (!withPeer) {
        return {primary: primaryDir};
    }
    const peerDir = seedCacheDir("peer");
    rmSync(peerDir, {recursive: true, force: true});
    provisionInstanceData({
        label: "B (peer)",
        tempDir: peerDir,
        fixtureDir: PEER_FIXTURE_DIR,
        seedProfile: "obama",
    });
    return {primary: primaryDir, peer: peerDir};
};

export const startIsolatedEnvironment = async (
    port = 5001,
    options: IsolatedEnvironmentOptions = {},
): Promise<IsolatedEnvironment> => {
    const {
        withPeer = false,
        demoMode = false,
        preseeded = false,
        copyPreseeded = false,
        docsFixture = false,
    } = options;
    if (docsFixture && withPeer) {
        throw new Error("The documentation fixture does not have a peer instance");
    }
    if (docsFixture && !preseeded) {
        throw new Error("Build the documentation fixture first, then serve it with preseeded=true");
    }
    if (copyPreseeded && !preseeded) {
        throw new Error("copyPreseeded requires preseeded=true");
    }
    if (copyPreseeded && docsFixture) {
        throw new Error("The documentation fixture cannot be copied as a UI-test seed cache");
    }
    // Check the whole web-port slot before copying a potentially large cache.
    // startInstance checks again immediately before spawn to close the race as
    // much as a bind-probe can without holding the port itself.
    if (!(await isTcpPortAvailable(port))) {
        throw portInUseError(port);
    }
    if (withPeer && !(await isTcpPortAvailable(port + 1))) {
        throw portInUseError(port + 1);
    }
    const runId = `${generateTimestamp()}_${randomBytes(4).toString("hex")}`;
    // Preseeded runs serve the canonical cache dirs directly; a normal run seeds
    // fresh temp dirs.
    let tempDir = docsFixture
        ? DOCS_DATA_DIR
        : preseeded ? seedCacheDir("primary") : join(TEMP_ROOT, `yaffo_test_${runId}`);
    if (copyPreseeded) {
        tempDir = copySeedCache("primary", join(TEMP_ROOT, `yaffo_test_${runId}`));
    }

    let primary: IsolatedInstance;
    try {
        primary = await startInstance({
            label: "A",
            port,
            tempDir,
            fixtureDir: PRIMARY_FIXTURE_DIR,
            seedProfile: "bennett",
            includeVideos: true,
            demoRole: demoMode ? "source" : undefined,
            flaskOnlyEnv: withPeer ? p2pEnv(port) : {},
            preseeded,
            disposableData: copyPreseeded,
            assetDir: copyPreseeded ? seedCacheDir("primary") : undefined,
        });
    } catch (error) {
        if (copyPreseeded && existsSync(tempDir)) {
            rmSync(tempDir, {recursive: true, force: true, maxRetries: 5, retryDelay: 100});
        }
        throw error;
    }

    let peer: IsolatedInstance | undefined;
    if (withPeer) {
        const peerPort = port + 1;
        let peerTempDir = preseeded
            ? seedCacheDir("peer")
            : join(TEMP_ROOT, `yaffo_test_${runId}_peer`);
        try {
            if (copyPreseeded) {
                peerTempDir = copySeedCache("peer", join(TEMP_ROOT, `yaffo_test_${runId}_peer`));
            }
            peer = await startInstance({
                label: "B (peer)",
                port: peerPort,
                tempDir: peerTempDir,
                fixtureDir: PEER_FIXTURE_DIR,
                seedProfile: "obama",
                demoRole: demoMode ? "receiver" : undefined,
                flaskOnlyEnv: p2pEnv(peerPort),
                preseeded,
                disposableData: copyPreseeded,
                assetDir: copyPreseeded ? seedCacheDir("peer") : undefined,
            });
        } catch (e) {
            if (copyPreseeded && existsSync(peerTempDir)) {
                rmSync(peerTempDir, {recursive: true, force: true, maxRetries: 5, retryDelay: 100});
            }
            await stopInstance(primary);
            throw e;
        }
        console.log(`\n🔗 Sharing sandbox up: A=${primary.baseUrl} B=${peer.baseUrl} (hub unreachable, LAN pairing only)`);
    }

    const cleanup = async () => {
        console.log(`\n🧹 Cleaning up isolated environment...`);
        await Promise.all([stopInstance(primary), stopInstance(peer)]);
        console.log(`   ✅ Stopped isolated process(es)`);
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
    const preseeded = args.includes("--preseeded");
    const docsFixture = args.includes("--docs");

    // Build-seed mode: seed the canonical cache dir(s) and exit without serving.
    // Used by the CI seed-cache job so per-spec jobs can restore and skip seeding.
    if (args.includes("--build-seed")) {
        const {primary, peer} = buildSeedCache({withPeer, docsFixture});
        console.log(`\n✅ Seed cache built: ${primary}${peer ? ` and ${peer}` : ""}`);
        process.exit(0);
    }

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
        environment = await startIsolatedEnvironment(port, {withPeer, demoMode, preseeded, docsFixture});
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
