import {describe, expect, it} from "@jest/globals";
import {buildSeatbeltProfile, detectSandboxKind, wrapWithSandbox} from "@lib/services/test_sandbox";

const detect = (
    env: NodeJS.ProcessEnv,
    osPlatform: NodeJS.Platform,
    available: string[] = ["bwrap", "sandbox-exec"],
    warn: (message: string) => void = () => {
    },
) => detectSandboxKind({env, osPlatform, isAvailable: (binary) => available.includes(binary), warn});

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

    it("falls back to none with a warning when the detected binary is missing", () => {
        const warnings: string[] = [];
        expect(detect({}, "linux", [], (message) => warnings.push(message))).toBe("none");
        expect(warnings[0]).toContain("bwrap");
    });

    it("honours an explicit sandbox request", () => {
        expect(detect({TEST_SANDBOX: "bwrap"}, "darwin")).toBe("bwrap");
        expect(detect({TEST_SANDBOX: "sandbox-exec"}, "linux")).toBe("sandbox-exec");
    });

    it("throws rather than silently downgrading an explicit request", () => {
        expect(() => detect({TEST_SANDBOX: "bwrap"}, "linux", [])).toThrow(/not on PATH/);
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
