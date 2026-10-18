import {buildAppArgs, buildCaptureArgs, snapshotDockerEnv, isNamedVolume,
        CONTAINER_BASE_URL, DOCS_APP_CONTAINER, DOCS_CAPTURE_IMAGE, DOCS_DOCKER_PLATFORM,
        DOCS_NETWORK} from "../user_doc_automation/docker";
import {CAPTURE_ENV_ALLOWLIST} from "../user_doc_automation/env";

const args = (over: Partial<Parameters<typeof buildCaptureArgs>[0]> = {}): string[] =>
    buildCaptureArgs({repoDir: "/repo", stagingDir: "/repo/yaffo_ui_tests/.doc-staging/captures", ...over});

const appArgs = (over: Partial<Parameters<typeof buildAppArgs>[0]> = {}): string[] =>
    buildAppArgs({repoDir: "/repo", mode: "serve", mounts: {data: "docs-data", assets: "docs-assets"}, ...over});

const envNames = (argv: string[]): string[] =>
    argv.filter((a, i, all) => all[i - 1] === "-e").map((a) => a.split("=")[0]);

describe("buildCaptureArgs", () => {
    // Staging must stay a sibling of the content tree, never a child: the agent's
    // filesystem tool is granted user_doc_automation/, and a run's own API logs live
    // in staging. A generate run was observed reading back its own prompts.
    it("keeps staging outside the tree granted to the agent", () => {
        const staging = args().find((a) => a.includes(".doc-staging")) ?? "";
        expect(staging).not.toContain("user_doc_automation");
    });

    it("mounts the repo read-only", () => {
        expect(args()).toContain("/repo:/app:ro");
    });

    it("leaves exactly one writable hole, at staging", () => {
        const mounts = args().filter((a, i, all) => all[i - 1] === "-v");
        const writable = mounts.filter((m) => m.includes(":") && !m.endsWith(":ro"));
        expect(writable).toEqual(
            ["/repo/yaffo_ui_tests/.doc-staging/captures:" +
             "/app/yaffo_ui_tests/.doc-staging/captures"]);
    });

    it("masks the host's node_modules, which is built for darwin", () => {
        expect(args()).toContain("/app/yaffo_ui_tests/node_modules");
    });

    it("passes no secret to the container", () => {
        const passed = args().filter((a, i, all) => all[i - 1] === "-e").map((a) => a.split("=")[0]);
        expect(passed.some((k) => /KEY|TOKEN|SECRET|PASSWORD|DOCKER_HOST/i.test(k))).toBe(false);
    });

    it("gives the container a writable HOME, since the repo mount is read-only", () => {
        expect(args()).toContain("HOME=/tmp");
    });

    it("runs the same worker the host runs", () => {
        expect(args().slice(-3)).toEqual(["npx", "tsx", "lib/user_doc_automation/capture_worker.ts"]);
    });

    it("forwards the page filter", () => {
        expect(args({pages: ["library-basics/browsing-filtering"]}).slice(-1))
            .toEqual(["library-basics/browsing-filtering"]);
    });

    it("captures every walkthrough when no page is named", () => {
        expect(args().slice(-1)).toEqual(["lib/user_doc_automation/capture_worker.ts"]);
    });

    it("names the pinned image", () => {
        expect(args()).toContain(DOCS_CAPTURE_IMAGE);
    });

    // The app runs in its own container; the capture never reaches back to the host.
    it("drives the app container over the docs network", () => {
        expect(args()).toEqual(expect.arrayContaining(["--network", DOCS_NETWORK]));
        expect(args()).toContain(`DOCS_BASE_URL=${CONTAINER_BASE_URL}`);
        expect(args().join(" ")).not.toContain("host.docker.internal");
    });

    it("gives walkthroughs the fixture path the app container sees", () => {
        expect(args()).toContain("YAFFO_DOCS_DATA_DIR=/tmp/yaffo-docs");
    });

    it("runs on the pinned platform", () => {
        expect(args()).toEqual(expect.arrayContaining(["--platform", DOCS_DOCKER_PLATFORM]));
    });
});

describe("buildAppArgs", () => {
    it("builds and serves the peer in the same container on its own loopback port", () => {
        const build = appArgs({mode: "fixture", withPeer: true});
        expect(build.slice(-3)).toEqual(["--build-seed", "--docs", "--peer"]);
        expect(build).not.toContain("-p");
        const serve = appArgs({withPeer: true});
        expect(serve).toContain("127.0.0.1:5003:5003");
        expect(serve).toContain("yaffo.docs.peer=true");
        expect(serve.at(-1)).toBe("--peer");
        expect(appArgs()).not.toContain("127.0.0.1:5003:5003");
        expect(appArgs({withPeer: true, hostPort: 5302})).toContain("127.0.0.1:5303:5003");
    });

    it("serves the docs fixture by name on the docs network", () => {
        const argv = appArgs();
        expect(argv).toEqual(expect.arrayContaining(["--name", DOCS_APP_CONTAINER, "--network", DOCS_NETWORK]));
        expect(argv.slice(-6)).toEqual(
            ["tsx", "lib/services/isolated_runner.ts", "--port", "5002", "--preseeded", "--docs"]);
    });

    it("publishes the sandbox on host loopback only", () => {
        expect(appArgs()).toContain("127.0.0.1:5002:5002");
    });

    it("allows the container name through the app's host guard", () => {
        expect(appArgs()).toContain(`YAFFO_WEB_ALLOWED_HOSTS=${DOCS_APP_CONTAINER}`);
        expect(appArgs()).toContain("YAFFO_DOC_OBSERVER=1");
    });

    it("mounts the repo read-only and writes only to the data and asset mounts", () => {
        const argv = appArgs();
        expect(argv).toContain("/repo:/app:ro");
        const writable = argv.filter((a, i, all) => all[i - 1] === "-v" && a.includes(":") && !a.endsWith(":ro"));
        expect(writable).toEqual(["docs-data:/tmp/yaffo-docs", "docs-assets:/tmp/yaffo-assets"]);
    });

    // HOME=/tmp would make the app show the fixture as ~/yaffo-docs.
    it("gives the app a home outside the fixture", () => {
        expect(appArgs()).toContain("HOME=/home/yaffo");
        expect(appArgs()).not.toContain("HOME=/tmp");
    });

    it("uses the image's venv, never the host's", () => {
        expect(appArgs()).toContain("YAFFO_VENV=/opt/venv");
    });

    // The image installs only Yaffo's dependencies; the package is the mounted source.
    it("puts the mounted source on the Python path", () => {
        expect(appArgs()).toContain("PYTHONPATH=/app");
    });

    // PYTHON_KEYRING_BACKEND names a backend, not a secret.
    it("passes no secret to the app", () => {
        expect(envNames(appArgs()).some((k) => /_KEY$|TOKEN|SECRET|PASSWORD|DOCKER_HOST/i.test(k))).toBe(false);
    });

    it("builds the fixture without a name, port, or observer", () => {
        const argv = appArgs({mode: "fixture"});
        expect(argv).not.toContain("--name");
        expect(argv).not.toContain("-p");
        expect(argv).not.toContain("YAFFO_DOC_OBSERVER=1");
        expect(argv.slice(-3)).toEqual(["lib/services/isolated_runner.ts", "--build-seed", "--docs"]);
    });

    it("keeps a detached sandbox after exit so its logs survive", () => {
        expect(appArgs({detach: true})).toContain("--detach");
        expect(appArgs({detach: true})).not.toContain("--rm");
        expect(appArgs()).toContain("--rm");
    });
});

describe("isNamedVolume", () => {
    it("distinguishes Docker volume names from host bind paths", () => {
        expect(isNamedVolume("yaffo-docs-data")).toBe(true);
        expect(isNamedVolume("/tmp/yaffo-docs")).toBe(false);
    });
});

describe("snapshotDockerEnv", () => {
    it("picks up the daemon settings Rancher Desktop sets", () => {
        expect(snapshotDockerEnv({DOCKER_HOST: "unix:///x.sock", PATH: "/usr/bin"}))
            .toEqual({DOCKER_HOST: "unix:///x.sock"});
    });

    it("omits what is not set, rather than passing undefined", () => {
        expect(snapshotDockerEnv({PATH: "/usr/bin"})).toEqual({});
    });

    // The whole reason these are snapshotted separately: DOCKER_HOST in the capture
    // allowlist would hand model-generated walkthroughs the daemon socket, which is
    // root on the host — and would make the container pointless.
    it("keeps the daemon socket out of what walkthroughs run with", () => {
        expect(CAPTURE_ENV_ALLOWLIST).not.toContain("DOCKER_HOST");
    });
});
