/**
 * The documentation sandbox, run from the pinned docs image.
 *
 *   npm run docker:build:docs-capture      # once, and after a dependency change
 *   npm run docs:sandbox:build             # build the fixture (slow; reused after)
 *   npm run docs:sandbox:start             # serve it; Ctrl-C stops it
 *   npm run docs:sandbox:start -- --detach # serve in the background, wait for ready
 *   npm run docs:sandbox:build -- --peer   # build both fixture libraries
 *   npm run docs:sandbox:start -- --peer --detach
 *   npm run docs:sandbox:stop
 *
 * The fixture is built and served by the same image on every machine so thumbnails,
 * face and label results, and fixture paths match CI, not only the browser. The app is
 * published on 127.0.0.1:5002 for the host's own tools; the capture container reaches
 * it over the private docs network by name.
 */
import {spawnSync} from "child_process";
import {pathToFileURL} from "url";
import {resolve} from "path";
import {
    appContainerRunning,
    appContainerHasPeer,
    DOCS_APP_CONTAINER,
    DOCS_APP_PORT,
    DOCS_PEER_PORT,
    docsMounts,
    dockerAvailable,
    prepareAppResources,
    runAppContainer,
    snapshotDockerEnv,
    stopAppContainer,
} from "./docker";
import {REPO} from "./paths";

const HOST_URL = `http://127.0.0.1:${DOCS_APP_PORT}`;
// Emulated amd64 on Apple Silicon starts slowly; CI is native and ready in seconds.
const READY_TIMEOUT_MS = 300_000;

const waitForReady = async (dockerEnv: Record<string, string>, withPeer: boolean): Promise<boolean> => {
    const deadline = Date.now() + READY_TIMEOUT_MS;
    while (Date.now() < deadline) {
        try {
            const urls = [HOST_URL, ...(withPeer ? [`http://127.0.0.1:${DOCS_PEER_PORT}`] : [])];
            if ((await Promise.all(urls.map(async url => (await fetch(url)).ok))).every(Boolean)) return true;
        } catch {
            // Not listening yet.
        }
        if (!appContainerRunning(dockerEnv)) return false;
        await new Promise((r) => setTimeout(r, 2000));
    }
    return false;
};

export const main = async (
    args: string[] = process.argv.slice(2),
    dockerEnv: Record<string, string> = snapshotDockerEnv(),
): Promise<number> => {
    const [command] = args;
    if (!["build", "start", "stop"].includes(command)) {
        console.error("Usage: sandbox.ts build [--peer] | start [--peer] [--detach] | stop");
        return 2;
    }
    const withPeer = args.includes("--peer");
    if (!dockerAvailable(dockerEnv)) {
        console.error("Docker is not reachable. Start Docker Desktop or Rancher Desktop, " +
            "then `npm run docker:build:docs-capture`. See README.md#docker.");
        return 1;
    }
    if (command === "stop") return stopAppContainer(dockerEnv);

    if (appContainerRunning(dockerEnv)) {
        if (command === "build") {
            console.error(`Stop ${DOCS_APP_CONTAINER} first (npm run docs:sandbox:stop): ` +
                "the fixture build replaces the database it is serving.");
            return 1;
        }
        if (withPeer && !appContainerHasPeer(dockerEnv)) {
            console.error("The docs sandbox is running without a peer. Stop it, then start with --peer.");
            return 1;
        }
        if (withPeer && !(await waitForReady(dockerEnv, true))) return 1;
        console.log(`${DOCS_APP_CONTAINER} is already running at ${HOST_URL}`);
        return 0;
    }

    // A detached sandbox that exited is kept for its logs, and holds the name.
    stopAppContainer(dockerEnv);
    const mounts = docsMounts();
    prepareAppResources(mounts, dockerEnv);
    if (command === "build") {
        return runAppContainer({repoDir: REPO, mode: "fixture", mounts, withPeer}, dockerEnv);
    }

    const detach = args.includes("--detach");
    const code = runAppContainer({repoDir: REPO, mode: "serve", detach, mounts, withPeer}, dockerEnv);
    if (!detach || code !== 0) return code;
    if (await waitForReady(dockerEnv, withPeer)) {
        console.log(`✅ ${DOCS_APP_CONTAINER} is ready at ${HOST_URL}` +
            (withPeer ? `; peer at http://127.0.0.1:${DOCS_PEER_PORT}` : ""));
        return 0;
    }
    console.error(`${DOCS_APP_CONTAINER} did not become ready. Its output:`);
    spawnSync("docker", ["logs", DOCS_APP_CONTAINER], {stdio: "inherit", env: {...process.env, ...dockerEnv}});
    stopAppContainer(dockerEnv);
    return 1;
};

const isDirectRun = process.argv[1] !== undefined &&
    import.meta.url === pathToFileURL(resolve(process.argv[1])).href;

if (isDirectRun) {
    main().then((code) => { process.exitCode = code; }).catch((error) => {
        console.error(error);
        process.exitCode = 1;
    });
}
