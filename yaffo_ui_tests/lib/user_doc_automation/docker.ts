import {spawnSync} from "child_process";
import {mkdirSync} from "fs";
import {captureEnv} from "./env";
import {DOCS_DATA_DIR} from "./paths";

export const DOCS_CAPTURE_IMAGE = "yaffo-docs-capture:latest";

/**
 * The architecture every docs container runs as. CI runners are amd64, and Skia and
 * ONNX Runtime take different SIMD paths per architecture, so an arm64 container on
 * Apple Silicon renders and analyzes photos differently from CI. Overridable for a
 * quick local look when emulation is too slow; such captures are not comparable.
 */
export const DOCS_DOCKER_PLATFORM = process.env.DOCS_DOCKER_PLATFORM || "linux/amd64";

/** Where the repo is mounted inside the container. */
const CONTAINER_REPO = "/app";
const CONTAINER_CWD = `${CONTAINER_REPO}/yaffo_ui_tests`;
/**
 * Staging inside the container. A sibling of the content tree, matching the host — see
 * the note on STAGING_DIR in paths.ts for why it is not a child of it.
 */
const CONTAINER_STAGING = `${CONTAINER_CWD}/.doc-staging/captures`;

/**
 * The private network the app and capture containers share. The capture reaches the
 * app by its container name, so nothing has to bind beyond loopback on the host.
 */
export const DOCS_NETWORK = "yaffo-docs";
export const DOCS_APP_CONTAINER = "yaffo-docs-app";
export const DOCS_APP_PORT = 5002;
/** The sandbox as the capture container addresses it. */
export const CONTAINER_BASE_URL = `http://${DOCS_APP_CONTAINER}:${DOCS_APP_PORT}`;

/**
 * The fixture and asset paths *inside* the app container. Fixed, so paths the guide
 * shows (Settings, folder pickers) are identical on every machine, and free of the
 * macOS /private/tmp alias.
 */
export const CONTAINER_DOCS_DATA_DIR = "/tmp/yaffo-docs";
export const CONTAINER_ASSET_DIR = "/tmp/yaffo-assets";
/** The app's home, created empty by the image. See the Dockerfile. */
export const CONTAINER_APP_HOME = "/home/yaffo";
/** The project's dependencies, installed by the image. Never the host's venv. */
const CONTAINER_VENV = "/opt/venv";

/**
 * What backs the fixture and asset directories. Named volumes by default: SQLite in
 * WAL mode over a macOS bind mount is slow and its locking is unreliable. CI sets
 * DOCS_DATA_MOUNT/DOCS_ASSET_MOUNT to host paths so actions/cache can save them.
 */
export const docsMounts = (source: NodeJS.ProcessEnv = process.env): {data: string; assets: string} => ({
    data: source.DOCS_DATA_MOUNT || "yaffo-docs-data",
    assets: source.DOCS_ASSET_MOUNT || "yaffo-docs-assets",
});

/** A bind mount is a path; anything else is a named volume Docker creates root-owned. */
export const isNamedVolume = (mount: string): boolean => !mount.startsWith("/") && !mount.startsWith(".");

/**
 * Run containers as the invoking user so bind-mounted captures stay writable by
 * the host-side encoder. Docker Desktop masks this ownership boundary, but native
 * Linux otherwise leaves root-owned page directories behind and Pillow cannot write
 * the WebP files beside the captured PNGs.
 */
export const hostUserArgs = (identity: {uid?: number; gid?: number} = {
    uid: typeof process.getuid === "function" ? process.getuid() : undefined,
    gid: typeof process.getgid === "function" ? process.getgid() : undefined,
}): string[] => identity.uid === undefined || identity.gid === undefined
    ? []
    : ["--user", `${identity.uid}:${identity.gid}`];

export interface CaptureContainerOptions {
    /** Repo root on the host — the parent of yaffo_ui_tests. */
    repoDir: string;
    /**
     * Capture output directory on the host. Bind-mounted writable; everything else is
     * read-only. Deliberately the captures subdirectory rather than all of staging —
     * a capture empties this before it starts, and the run's API logs are staging's
     * other occupant.
     */
    stagingDir: string;
    /** Page ids to capture; empty means every walkthrough. */
    pages?: string[];
    /** Skip dependency-only flows during a stability recapture. */
    shotsOnly?: boolean;
    /**
     * The docker CLI's own settings, snapshotted before the environment was scrubbed.
     *
     * These cannot live in `CAPTURE_ENV_ALLOWLIST`: that allowlist is what
     * model-generated walkthroughs run with, and `DOCKER_HOST` in it would hand them
     * the daemon socket — which is root on the host, and would make the container
     * pointless. So the launcher carries them separately and the capture never sees
     * them.
     */
    dockerEnv?: Record<string, string>;
}

/** Settings the docker CLI needs to find its daemon. Rancher Desktop sets DOCKER_HOST. */
export const DOCKER_CLI_VARS = ["DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG", "DOCKER_CERT_PATH", "DOCKER_TLS_VERIFY"];

/** Snapshot the docker CLI's settings. Call before `scrubProcessEnv`. */
export const snapshotDockerEnv = (source: NodeJS.ProcessEnv = process.env): Record<string, string> =>
    Object.fromEntries(DOCKER_CLI_VARS.filter((k) => source[k]).map((k) => [k, source[k] as string]));

/**
 * The argv for a containerized capture.
 *
 * Two things differ from the MCP filesystem container, both deliberately:
 *
 *  * The network is not `none`. Capture exists to drive a running app, so it joins the
 *    app container's network. Confinement here comes from the environment allowlist
 *    and the read-only mount, not from isolation.
 *  * The repo is mounted read-only with a single writable hole at the staging
 *    directory. Walkthroughs are model-generated code; staging is the only place they
 *    have any business writing, and the guide itself is promoted into by the host,
 *    after the images have been compared.
 *
 * `node_modules` gets an anonymous volume so the image's Linux build masks the host's
 * darwin one, which would not execute here.
 */
export const buildCaptureArgs = (options: CaptureContainerOptions): string[] => [
    "run", "--rm", "--init",
    "--platform", DOCS_DOCKER_PLATFORM,
    ...hostUserArgs(),
    "--network", DOCS_NETWORK,
    "-v", `${options.repoDir}:${CONTAINER_REPO}:ro`,
    "-v", `${CONTAINER_CWD}/node_modules`,
    "-v", `${options.stagingDir}:${CONTAINER_STAGING}`,
    "-w", CONTAINER_CWD,
    // Only what capture needs. Notably absent: every provider key.
    "-e", `DOCS_BASE_URL=${CONTAINER_BASE_URL}`,
    // The capture directory itself, not staging — see CAPTURE_DIR in paths.ts.
    "-e", `DOCS_CAPTURE_DIR=${CONTAINER_STAGING}`,
    // The fixture as the app container sees it. Walkthroughs pass it back through
    // the UI when a docs flow needs one of the fixture directories.
    "-e", `YAFFO_DOCS_DATA_DIR=${CONTAINER_DOCS_DATA_DIR}`,
    "-e", "SKIP_DOTENV=1",
    // tsx and Chromium both want a writable home; the repo mount is read-only.
    "-e", "HOME=/tmp",
    "-e", "TMPDIR=/tmp",
    DOCS_CAPTURE_IMAGE,
    "npx", "tsx", "lib/user_doc_automation/capture_worker.ts",
    ...(options.shotsOnly ? ["--shots-only"] : []),
    ...(options.pages ?? []),
];

export interface AppContainerOptions {
    /** Repo root on the host — the parent of yaffo_ui_tests. */
    repoDir: string;
    /** `fixture` builds the documentation fixture; `serve` runs the sandbox on it. */
    mode: "fixture" | "serve";
    /** Serve in the background (CI). The foreground default streams logs until Ctrl-C. */
    detach?: boolean;
    /** Host port the sandbox is published on, at loopback only. */
    hostPort?: number;
    mounts?: {data: string; assets: string};
}

/**
 * The argv for the Yaffo side: building the fixture, or serving it.
 *
 * Both run the same isolated runner the host uses, so the fixture recipe stays in
 * one place. The repo is read-only here too; the app writes only to its data and
 * asset mounts. No provider key is passed, and keyring is the null backend: a slim
 * container has no OS keychain, and the docs sandbox stores no secrets.
 */
export const buildAppArgs = (options: AppContainerOptions): string[] => {
    const mounts = options.mounts ?? docsMounts();
    const serve = options.mode === "serve";
    // A detached sandbox is kept after it exits so `docker logs` can say why;
    // stopAppContainer removes it.
    const detach = serve && Boolean(options.detach);
    return [
        "run", ...(detach ? ["--detach"] : ["--rm"]), "--init",
        "--platform", DOCS_DOCKER_PLATFORM,
        ...hostUserArgs(),
        "--network", DOCS_NETWORK,
        ...(serve ? [
            "--name", DOCS_APP_CONTAINER,
            // Loopback only: the host's own tools (docs:heal's browser, your browser)
            // use it, and nothing else on the LAN should.
            "-p", `127.0.0.1:${options.hostPort ?? DOCS_APP_PORT}:${DOCS_APP_PORT}`,
        ] : []),
        "-v", `${options.repoDir}:${CONTAINER_REPO}:ro`,
        "-v", `${CONTAINER_CWD}/node_modules`,
        "-v", `${mounts.data}:${CONTAINER_DOCS_DATA_DIR}`,
        "-v", `${mounts.assets}:${CONTAINER_ASSET_DIR}`,
        "-w", CONTAINER_CWD,
        // Not /tmp: Yaffo shows paths under HOME as "~/...", which would turn the
        // fixture's /tmp/yaffo-docs into ~/yaffo-docs in every screenshot.
        "-e", `HOME=${CONTAINER_APP_HOME}`,
        "-e", "TMPDIR=/tmp",
        "-e", "TZ=UTC",
        "-e", "LANG=C.UTF-8",
        "-e", "SKIP_DOTENV=1",
        "-e", `YAFFO_VENV=${CONTAINER_VENV}`,
        // The host venv has Yaffo installed editable; the image has only its
        // dependencies, so the mounted source is put on the path instead.
        "-e", `PYTHONPATH=${CONTAINER_REPO}`,
        "-e", `YAFFO_DOCS_DATA_DIR=${CONTAINER_DOCS_DATA_DIR}`,
        "-e", `YAFFO_ASSET_DIR=${CONTAINER_ASSET_DIR}`,
        "-e", "PYTHON_KEYRING_BACKEND=keyring.backends.null.Keyring",
        ...(serve ? [
            // Inside the container, so 0.0.0.0 reaches only the docs network and the
            // loopback-published port above.
            "-e", "YAFFO_SANDBOX_HOST=0.0.0.0",
            // The capture container names the app by its container name; the app's
            // DNS-rebinding guard refuses any host it was not told about.
            "-e", `YAFFO_WEB_ALLOWED_HOSTS=${DOCS_APP_CONTAINER}`,
            "-e", "YAFFO_DOC_OBSERVER=1",
        ] : []),
        DOCS_CAPTURE_IMAGE,
        "npx", "tsx", "lib/services/isolated_runner.ts",
        ...(serve
            ? ["--port", String(DOCS_APP_PORT), "--preseeded", "--docs"]
            : ["--build-seed", "--docs"]),
    ];
};

/**
 * Run the capture container, streaming its output. Returns its exit code; the caller
 * reads results from the staging mount, which both sides see as the same files.
 */
export const runCaptureContainer = (options: CaptureContainerOptions): number => {
    // Docker would create this root-owned if it were missing.
    mkdirSync(options.stagingDir, {recursive: true});
    const args = buildCaptureArgs(options);
    console.log(`  docker ${args.slice(0, 2).join(" ")} … ${DOCS_CAPTURE_IMAGE}`);
    const result = spawnSync("docker", args, {
        stdio: "inherit",
        env: {...captureEnv(), ...(options.dockerEnv ?? {})},
    });
    if (result.error) throw result.error;
    return result.status ?? 1;
};

const docker = (args: string[], dockerEnv: Record<string, string>, inherit = false) => {
    const result = spawnSync("docker", args, {
        stdio: inherit ? "inherit" : "pipe",
        encoding: "utf8",
        env: {...captureEnv(), ...dockerEnv},
    });
    if (result.error) throw result.error;
    return result;
};

/** Whether the documentation sandbox container is up. */
export const appContainerRunning = (dockerEnv: Record<string, string> = {}): boolean => {
    const result = docker(["inspect", "--format", "{{.State.Running}}", DOCS_APP_CONTAINER], dockerEnv);
    return result.status === 0 && result.stdout.trim() === "true";
};

/**
 * The network, and named volumes the invoking user can write. Docker creates a named
 * volume owned by root, and the app runs as the invoking user so a bind-mounted CI
 * fixture stays owned by the runner (and cacheable).
 */
export const prepareAppResources = (
    mounts: {data: string; assets: string} = docsMounts(),
    dockerEnv: Record<string, string> = {},
): void => {
    if (docker(["network", "inspect", DOCS_NETWORK], dockerEnv).status !== 0) {
        const created = docker(["network", "create", DOCS_NETWORK], dockerEnv);
        if (created.status !== 0) throw new Error(`Could not create the ${DOCS_NETWORK} network: ${created.stderr}`);
    }
    const named = [[mounts.data, CONTAINER_DOCS_DATA_DIR], [mounts.assets, CONTAINER_ASSET_DIR]]
        .filter(([mount]) => isNamedVolume(mount));
    const owner = hostUserArgs()[1];
    if (!named.length || !owner) return;
    const chown = docker([
        "run", "--rm", "--platform", DOCS_DOCKER_PLATFORM, "--user", "0",
        ...named.flatMap(([mount, target]) => ["-v", `${mount}:${target}`]),
        DOCS_CAPTURE_IMAGE, "chown", owner, ...named.map(([, target]) => target),
    ], dockerEnv);
    if (chown.status !== 0) throw new Error(`Could not prepare the docs volumes: ${chown.stderr}`);
};

/** Run the app side. Foreground runs stream output and return when it exits. */
export const runAppContainer = (options: AppContainerOptions, dockerEnv: Record<string, string> = {}): number => {
    const args = buildAppArgs(options);
    console.log(`  docker ${args.slice(0, 2).join(" ")} … ${DOCS_CAPTURE_IMAGE} (${options.mode})`);
    return docker(args, dockerEnv, true).status ?? 1;
};

/** Stop and remove the sandbox, including a detached one that already exited. */
export const stopAppContainer = (dockerEnv: Record<string, string> = {}): number => {
    const stopped = docker(["stop", DOCS_APP_CONTAINER], dockerEnv);
    docker(["rm", "--force", DOCS_APP_CONTAINER], dockerEnv);
    if (stopped.status !== 0 && !/No such container/i.test(stopped.stderr)) {
        console.error(stopped.stderr.trim());
        return stopped.status ?? 1;
    }
    return 0;
};

/** Whether a usable Docker daemon is reachable, for a clear error instead of a stack. */
export const dockerAvailable = (dockerEnv: Record<string, string> = {}): boolean =>
    spawnSync("docker", ["info", "--format", "{{.ServerVersion}}"], {
        stdio: "ignore",
        env: {...captureEnv(), ...dockerEnv},
    }).status === 0;
