import {spawnSync} from "child_process";
import {mkdirSync} from "fs";
import {captureEnv} from "./env";

export const DOCS_CAPTURE_IMAGE = "yaffo-docs-capture:latest";

/** Where the repo is mounted inside the container. */
const CONTAINER_REPO = "/app";
const CONTAINER_CWD = `${CONTAINER_REPO}/yaffo_ui_tests`;
/**
 * Staging inside the container. A sibling of the content tree, matching the host — see
 * the note on STAGING_DIR in paths.ts for why it is not a child of it.
 */
const CONTAINER_STAGING = `${CONTAINER_CWD}/.doc-staging`;

/**
 * The host address, seen from inside the container. On macOS a container cannot reach
 * the host's loopback, and `--network host` would join the Linux VM rather than the
 * Mac, so this alias is the only route in.
 */
export const HOST_ALIAS = "host.docker.internal";

/**
 * `--add-host` arguments, which are wanted on Linux and actively harmful on macOS.
 *
 * Docker Desktop and Rancher Desktop already resolve the alias to the Mac as the VM
 * sees it (192.168.5.2 here). Mapping it to `host-gateway` *overrides* that with the
 * bridge gateway — 172.17.0.1, which is the VM itself — and every connection is
 * refused. Linux has no built-in alias, so there it is the thing that makes it work.
 */
export const addHostArgs = (platform: NodeJS.Platform = process.platform): string[] =>
    platform === "linux" ? ["--add-host", `${HOST_ALIAS}:host-gateway`] : [];

/** Point a loopback URL at the host as the container sees it. */
export const containerBaseUrl = (baseUrl: string): string =>
    baseUrl.replace(/\/\/(127\.0\.0\.1|localhost|0\.0\.0\.0)\b/, `//${HOST_ALIAS}`);

export interface CaptureContainerOptions {
    /** Repo root on the host — the parent of yaffo_ui_tests. */
    repoDir: string;
    /** Staging directory on the host. Bind-mounted writable; everything else is read-only. */
    stagingDir: string;
    /** The app to capture, as the *host* addresses it. Rewritten for the container. */
    baseUrl: string;
    /** Page ids to capture; empty means every walkthrough. */
    pages?: string[];
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
 *  * The network is not `none`. Capture exists to drive a running app, so it must be
 *    able to reach one. Confinement here comes from the environment allowlist and the
 *    read-only mount, not from isolation.
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
    "-v", `${options.repoDir}:${CONTAINER_REPO}:ro`,
    "-v", `${CONTAINER_CWD}/node_modules`,
    "-v", `${options.stagingDir}:${CONTAINER_STAGING}`,
    "-w", CONTAINER_CWD,
    ...addHostArgs(),
    // Only what capture needs. Notably absent: every provider key.
    "-e", `DOCS_BASE_URL=${containerBaseUrl(options.baseUrl)}`,
    "-e", `DOCS_STAGING_DIR=${CONTAINER_STAGING}`,
    "-e", "SKIP_DOTENV=1",
    // tsx and Chromium both want a writable home; the repo mount is read-only.
    "-e", "HOME=/tmp",
    "-e", "TMPDIR=/tmp",
    DOCS_CAPTURE_IMAGE,
    "npx", "tsx", "lib/user_doc_automation/capture_worker.ts",
    ...(options.pages ?? []),
];

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

/** Whether a usable Docker daemon is reachable, for a clear error instead of a stack. */
export const dockerAvailable = (dockerEnv: Record<string, string> = {}): boolean =>
    spawnSync("docker", ["info", "--format", "{{.ServerVersion}}"], {
        stdio: "ignore",
        env: {...captureEnv(), ...dockerEnv},
    }).status === 0;
