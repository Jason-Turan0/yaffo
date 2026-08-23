import {beforeEach, describe, expect, it} from "@jest/globals";
import {platform} from "os";
import {
    buildSeatbeltProfile,
    detectSandboxKind,
    probeSandbox,
    resetSandboxProbeCache,
    wrapWithSandbox
} from "@lib/services/test_sandbox";

const detect = (
    env: NodeJS.ProcessEnv,
    osPlatform: NodeJS.Platform,
    usable: string[] = ["bwrap", "sandbox-exec"],
    warn: (message: string) => void = () => {
    },
) => detectSandboxKind({
    env,
    osPlatform,
    probe: (kind) => usable.includes(kind)
        ? {ok: true}
        : {ok: false, error: "bwrap: setting up uid map: Permission denied"},
    warn,
});

describe("detectSandboxKind", () => {
    it("picks sandbox-exec on macOS and bwrap on Linux", () => {
        expect(detect({}, "darwin")).toBe("sandbox-exec");
        expect(detect({}, "linux")).toBe("bwrap");
    });

    it("treats TEST_SANDBOX=auto the same as unset", () => {
        expect(detect({TEST_SANDBOX: "auto"}, "darwin")).toBe("sandbox-exec");
    });

    it("falls back to none with a warning on unsupported platforms", () => {
        const warnings: string[] = [];
        expect(detect({}, "win32", [], (message) => warnings.push(message))).toBe("none");
        expect(warnings).toHaveLength(1);
    });

    it("falls back to none with a warning when the detected sandbox cannot start", () => {
        const warnings: string[] = [];
        expect(detect({}, "linux", [], (message) => warnings.push(message))).toBe("none");
        expect(warnings[0]).toContain("setting up uid map");
        // The warning has to say how to fix it, not just that it broke.
        expect(warnings[0]).toContain("apparmor_restrict_unprivileged_userns");
    });

    it("honours an explicit sandbox request", () => {
        expect(detect({TEST_SANDBOX: "bwrap"}, "darwin")).toBe("bwrap");
        expect(detect({TEST_SANDBOX: "sandbox-exec"}, "linux")).toBe("sandbox-exec");
    });

    it("throws rather than silently downgrading an explicit request", () => {
        // A bwrap that is installed but blocked by AppArmor must not degrade to
        // an unsandboxed run just because the binary exists.
        expect(() => detect({TEST_SANDBOX: "bwrap"}, "linux", []))
            .toThrow(/setting up uid map[\s\S]*apparmor_restrict_unprivileged_userns/);
    });

    it("disables the sandbox on the off switches", () => {
        for (const value of ["none", "off", "0", "false", "no", "NONE"]) {
            expect(detect({TEST_SANDBOX: value}, "linux")).toBe("none");
        }
    });

    it("rejects an unknown TEST_SANDBOX value", () => {
        expect(() => detect({TEST_SANDBOX: "firejail"}, "linux")).toThrow(/Unknown TEST_SANDBOX/);
    });
});

describe("wrapWithSandbox", () => {
    const base = {command: "npx", args: ["playwright", "test"], writableRoots: ["/tmp/x", "/reports"]};

    it("binds each writable root read-write under bwrap", () => {
        const [command, args] = wrapWithSandbox({...base, kind: "bwrap"});
        expect(command).toBe("bwrap");
        expect(args).toEqual(expect.arrayContaining(["--ro-bind", "/", "/"]));
        expect(args.join(" ")).toContain("--bind /tmp/x /tmp/x");
        expect(args.join(" ")).toContain("--bind /reports /reports");
        expect(args.slice(-3)).toEqual(["npx", "playwright", "test"]);
    });

    it("passes a Seatbelt profile under sandbox-exec", () => {
        const [command, args] = wrapWithSandbox({...base, kind: "sandbox-exec"});
        expect(command).toBe("sandbox-exec");
        expect(args[0]).toBe("-p");
        expect(args[1]).toContain("(deny file-write*)");
        expect(args.slice(2)).toEqual(["npx", "playwright", "test"]);
    });

    it("returns the command untouched when there is no sandbox", () => {
        expect(wrapWithSandbox({...base, kind: "none"})).toEqual(["npx", ["playwright", "test"]]);
    });
});

describe("probeSandbox", () => {
    // Treat the binary as installed so these unit tests exercise the process probe
    // independently of the tools present on the machine running Jest.
    const onPath = () => true;
    const fakeRun = (status: number, stderr = "") =>
        ((): {status: number; stderr: string} => ({status, stderr})) as never;

    beforeEach(() => resetSandboxProbeCache());

    it("reports ok when the sandbox runs `true` successfully", () => {
        const kind = platform() === "darwin" ? "sandbox-exec" : "bwrap";
        expect(probeSandbox(kind, fakeRun(0), onPath)).toEqual({ok: true});
    });

    it("surfaces the sandbox's own stderr when it cannot start", () => {
        const kind = platform() === "darwin" ? "sandbox-exec" : "bwrap";
        expect(probeSandbox(kind, fakeRun(1, "bwrap: setting up uid map: Permission denied\n"), onPath))
            .toEqual({ok: false, error: "bwrap: setting up uid map: Permission denied"});
    });

    it("reports a missing sandbox binary before trying to run it", () => {
        const kind = platform() === "darwin" ? "sandbox-exec" : "bwrap";
        let calls = 0;
        const counting = ((): {status: number; stderr: string} => {
            calls++;
            return {status: 0, stderr: ""};
        }) as never;

        expect(probeSandbox(kind, counting, () => false)).toEqual({
            ok: false,
            error: `"${kind}" is not on PATH`,
        });
        expect(calls).toBe(0);
    });

    it("caches the verdict so a heal does not re-probe on every test run", () => {
        const kind = platform() === "darwin" ? "sandbox-exec" : "bwrap";
        let calls = 0;
        const counting = ((): {status: number; stderr: string} => {
            calls++;
            return {status: 0, stderr: ""};
        }) as never;
        probeSandbox(kind, counting, onPath);
        probeSandbox(kind, counting, onPath);
        expect(calls).toBe(1);
    });
});

describe("buildSeatbeltProfile", () => {
    it("denies writes globally and re-grants only the writable roots", () => {
        const profile = buildSeatbeltProfile(["/private/tmp/run", "/repo/reports"]);
        expect(profile.split("\n")).toEqual([
            "(version 1)",
            "(allow default)",
            "(deny file-write*)",
            `(allow file-write* (subpath "/dev"))`,
            `(allow file-write* (subpath "/private/tmp/run"))`,
            `(allow file-write* (subpath "/repo/reports"))`,
        ]);
    });

    it("escapes quotes and backslashes in paths", () => {
        expect(buildSeatbeltProfile([`/tmp/we"ird\\path`]))
            .toContain(`(subpath "/tmp/we\\"ird\\\\path")`);
    });
});
