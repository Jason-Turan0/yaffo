import {afterEach, beforeEach, describe, expect, it} from "@jest/globals";
import {chmodSync, existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync} from "fs";
import {tmpdir} from "os";
import {join} from "path";
import {dockerAvailable, hostUserArgs, runCaptureContainer} from "../docker";
import {DOCS_DATA_DIR} from "../paths";

let testDir: string;
let fakeDocker: string;

beforeEach(() => {
    testDir = mkdtempSync(join(tmpdir(), "yaffo-fake-docker-"));
    fakeDocker = join(testDir, "docker");
    writeFileSync(fakeDocker, [
        "#!/bin/sh",
        'if [ -n "$DOCKER_TEST_OUTPUT" ]; then',
        '  printf "%s\\n" "$@" > "$DOCKER_TEST_OUTPUT"',
        '  printf "%s" "${DOCKER_HOST:-}" > "${DOCKER_TEST_OUTPUT}.host"',
        '  printf "%s" "${ANTHROPIC_API_KEY:-}" > "${DOCKER_TEST_OUTPUT}.secret"',
        "fi",
        'if [ "${DOCKER_TEST_SIGNAL:-}" = "1" ]; then kill -TERM $$; fi',
        'exit "${DOCKER_TEST_STATUS:-0}"',
        "",
    ].join("\n"), "utf8");
    chmodSync(fakeDocker, 0o755);
});

afterEach(() => {
    rmSync(testDir, {recursive: true, force: true});
});

describe("dockerAvailable", () => {
    it("returns true only when docker info succeeds", () => {
        expect(dockerAvailable({PATH: testDir, DOCKER_TEST_STATUS: "0"})).toBe(true);
        expect(dockerAvailable({PATH: testDir, DOCKER_TEST_STATUS: "2"})).toBe(false);
    });
});

describe("hostUserArgs", () => {
    it("keeps bind-mounted capture output owned by the invoking Linux user", () => {
        expect(hostUserArgs({uid: 1001, gid: 121})).toEqual(["--user", "1001:121"]);
    });

    it("omits the option on platforms without POSIX user ids", () => {
        expect(hostUserArgs({})).toEqual([]);
    });
});

describe("runCaptureContainer", () => {
    it("creates staging, returns Docker's status, and preserves daemon settings", () => {
        const stagingDir = join(testDir, "nested", "captures");
        const output = join(testDir, "argv.txt");
        const previousSecret = process.env.ANTHROPIC_API_KEY;
        process.env.ANTHROPIC_API_KEY = "must-not-reach-docker";
        try {
            const status = runCaptureContainer({
                repoDir: "/repo",
                stagingDir,
                baseUrl: "http://127.0.0.1:5002",
                pages: ["library/browsing"],
                dockerEnv: {
                    PATH: testDir,
                    DOCKER_HOST: "unix:///daemon.sock",
                    DOCKER_TEST_OUTPUT: output,
                    DOCKER_TEST_STATUS: "7",
                },
            });

            expect(status).toBe(7);
            expect(existsSync(stagingDir)).toBe(true);
            expect(readFileSync(`${output}.host`, "utf8")).toBe("unix:///daemon.sock");
            expect(readFileSync(`${output}.secret`, "utf8")).toBe("");
            const argv = readFileSync(output, "utf8").trim().split("\n");
            expect(argv.slice(0, 3)).toEqual(["run", "--rm", "--init"]);
            if (typeof process.getuid === "function" && typeof process.getgid === "function") {
                expect(argv).toContain(`${process.getuid()}:${process.getgid()}`);
            }
            expect(argv).toContain("DOCS_BASE_URL=http://host.docker.internal:5002");
            expect(argv).toContain(`YAFFO_DOCS_DATA_DIR=${DOCS_DATA_DIR}`);
            expect(argv.slice(-1)).toEqual(["library/browsing"]);
        } finally {
            if (previousSecret === undefined) delete process.env.ANTHROPIC_API_KEY;
            else process.env.ANTHROPIC_API_KEY = previousSecret;
        }
    });

    it("uses one as the failure status when Docker is terminated by a signal", () => {
        expect(runCaptureContainer({
            repoDir: "/repo",
            stagingDir: join(testDir, "captures"),
            baseUrl: "http://app.test",
            dockerEnv: {PATH: testDir, DOCKER_TEST_SIGNAL: "1"},
        })).toBe(1);
    });

    it("passes the shots-only flag to a stability capture worker", () => {
        const output = join(testDir, "stability-argv.txt");
        expect(runCaptureContainer({
            repoDir: "/repo",
            stagingDir: join(testDir, "stability"),
            baseUrl: "http://app.test",
            pages: ["library/browsing"],
            shotsOnly: true,
            dockerEnv: {PATH: testDir, DOCKER_TEST_OUTPUT: output},
        })).toBe(0);

        const argv = readFileSync(output, "utf8").trim().split("\n");
        expect(argv.slice(-2)).toEqual(["--shots-only", "library/browsing"]);
    });

    it("throws process-launch errors directly", () => {
        expect(() => runCaptureContainer({
            repoDir: "/repo",
            stagingDir: join(testDir, "captures"),
            baseUrl: "http://app.test",
            dockerEnv: {PATH: join(testDir, "missing")},
        })).toThrow(/ENOENT/);
    });
});
