import {addHostArgs, buildCaptureArgs, containerBaseUrl, snapshotDockerEnv,
        DOCS_CAPTURE_IMAGE, HOST_ALIAS} from "../user_doc_automation/docker";
import {CAPTURE_ENV_ALLOWLIST} from "../user_doc_automation/env";

const args = (over: Partial<Parameters<typeof buildCaptureArgs>[0]> = {}): string[] =>
    buildCaptureArgs({repoDir: "/repo", stagingDir: "/repo/yaffo_ui_tests/.doc-staging/captures",
                      baseUrl: "http://127.0.0.1:5002", ...over});

describe("containerBaseUrl", () => {
    it.each([
        ["http://127.0.0.1:5002", `http://${HOST_ALIAS}:5002`],
        ["http://localhost:5002", `http://${HOST_ALIAS}:5002`],
        ["http://0.0.0.0:5002", `http://${HOST_ALIAS}:5002`],
    ])("rewrites %s so the container can reach the host", (given, expected) => {
        expect(containerBaseUrl(given)).toBe(expected);
    });

    it("leaves a real host alone", () => {
        expect(containerBaseUrl("http://staging.example.com:8080")).toBe("http://staging.example.com:8080");
    });
});

describe("addHostArgs", () => {
    // Regression: mapping the alias to host-gateway on macOS overrides Docker's own
    // resolution (the Mac) with the bridge gateway (the Linux VM), and every
    // connection to the app is refused.
    it("is empty on macOS, where Docker resolves the alias itself", () => {
        expect(addHostArgs("darwin")).toEqual([]);
    });

    it("maps the alias on Linux, which has no built-in one", () => {
        expect(addHostArgs("linux")).toEqual(["--add-host", `${HOST_ALIAS}:host-gateway`]);
    });
});

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
