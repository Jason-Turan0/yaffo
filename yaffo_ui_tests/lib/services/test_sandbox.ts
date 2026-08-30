/**
 * OS sandbox selection for the process that executes model-generated test code.
 *
 * The generated specs are written by an LLM, so the run is confined twice: the
 * spawned process gets a scrubbed env allowlist (see run_playwright_tests.ts)
 * and, where the platform provides one, an OS sandbox that makes the filesystem
 * read-only apart from the few directories a test run legitimately writes.
 *
 *   linux  → bubblewrap (bwrap)
 *   darwin → sandbox-exec (Seatbelt)
 *
 * Network access stays unrestricted under both: the tests must reach the
 * isolated app instance on loopback, and egress is covered by the env scrub
 * plus the code-safety audit rather than by the sandbox.
 */
import {existsSync, realpathSync} from "fs";
import {delimiter, dirname, isAbsolute, join} from "path";
import {platform} from "os";
import {spawnSync} from "child_process";

export type SandboxKind = "bwrap" | "sandbox-exec" | "none";

/** TEST_SANDBOX values that explicitly turn the OS sandbox off. */
const DISABLED_VALUES = new Set(["none", "off", "0", "false", "no"]);

const SANDBOX_BINARY: Record<Exclude<SandboxKind, "none">, string> = {
    "bwrap": "bwrap",
    "sandbox-exec": "sandbox-exec",
};

/** True when `name` resolves to an executable file on PATH. */
export const isOnPath = (name: string, pathEnv = process.env.PATH ?? ""): boolean => {
    if (isAbsolute(name)) return existsSync(name);
    return pathEnv
        .split(delimiter)
        .filter(Boolean)
        .some((dir) => existsSync(join(dir, name)));
};

export interface ProbeResult {
    ok: boolean;
    /** Why the sandbox is unusable — the tool's own stderr where there is any. */
    error?: string;
}

/**
 * Being on PATH is not enough: bwrap needs unprivileged user namespaces, which
 * Ubuntu 24.04 and later deny by default under AppArmor
 * (kernel.apparmor_restrict_unprivileged_userns=1) — bwrap then fails at
 * "setting up uid map: Permission denied" on every spawn. Run the real wrapper
 * over `true` once and cache the verdict.
 */
const probeCache = new Map<SandboxKind, ProbeResult>();

export const probeSandbox = (
    kind: Exclude<SandboxKind, "none">,
    run: typeof spawnSync = spawnSync,
    onPath: (name: string) => boolean = isOnPath,
): ProbeResult => {
    const cached = probeCache.get(kind);
    if (cached) return cached;

    let result: ProbeResult;
    if (!onPath(SANDBOX_BINARY[kind])) {
        result = {ok: false, error: `"${SANDBOX_BINARY[kind]}" is not on PATH`};
    } else {
        const [command, args] = wrapWithSandbox({kind, command: "true", args: [], writableRoots: []});
        const probe = run(command, args, {encoding: "utf-8", timeout: 30_000});
        result = probe.status === 0
            ? {ok: true}
            : {ok: false, error: (probe.stderr || probe.error?.message || `exit ${probe.status}`).toString().trim()};
    }

    probeCache.set(kind, result);
    return result;
};

/** Test seam: forget cached probe verdicts. */
export const resetSandboxProbeCache = (): void => probeCache.clear();

/** Remediation hint for a sandbox that is installed but cannot start. */
const remediation = (kind: Exclude<SandboxKind, "none">): string =>
    kind === "bwrap"
        ? `bubblewrap needs unprivileged user namespaces. On Ubuntu 24.04+ enable them with: ` +
          `sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0 ` +
          `(install with: sudo apt-get install -y bubblewrap)`
        : `sandbox-exec ships with macOS; this platform is probably not macOS.`;

export interface DetectOptions {
    env?: NodeJS.ProcessEnv;
    osPlatform?: NodeJS.Platform;
    /** Verify a sandbox can actually start, not merely that it is installed. */
    probe?: (kind: Exclude<SandboxKind, "none">) => ProbeResult;
    warn?: (message: string) => void;
}

/**
 * Pick the sandbox for this run.
 *
 * TEST_SANDBOX overrides detection: "bwrap" / "sandbox-exec" force a specific
 * sandbox (and throw if its binary is missing, so CI cannot silently downgrade),
 * "none"/"off"/"0" disable it, and "auto" (or unset) detects from the platform.
 * Auto-detection that finds no usable sandbox warns and runs unwrapped.
 */
export const detectSandboxKind = ({
                                      env = process.env,
                                      osPlatform = platform(),
                                      probe = (kind) => probeSandbox(kind),
                                      warn = (message) => console.warn(message),
                                  }: DetectOptions = {}): SandboxKind => {
    const requested = env.TEST_SANDBOX?.trim().toLowerCase();

    if (requested && requested !== "auto") {
        if (DISABLED_VALUES.has(requested)) return "none";
        if (requested !== "bwrap" && requested !== "sandbox-exec") {
            throw new Error(
                `Unknown TEST_SANDBOX value "${env.TEST_SANDBOX}". ` +
                `Expected one of: auto, bwrap, sandbox-exec, none.`
            );
        }
        const {ok, error} = probe(requested);
        if (!ok) {
            throw new Error(
                `TEST_SANDBOX=${requested} was requested but the sandbox cannot start: ${error}\n` +
                remediation(requested)
            );
        }
        return requested;
    }

    const auto: SandboxKind = osPlatform === "darwin"
        ? "sandbox-exec"
        : osPlatform === "linux"
            ? "bwrap"
            : "none";

    if (auto === "none") {
        warn(`⚠️  No OS sandbox available for platform "${osPlatform}"; running generated tests unsandboxed.`);
        return "none";
    }
    const {ok, error} = probe(auto);
    if (!ok) {
        warn(
            `⚠️  ${auto} cannot start (${error}); running generated tests unsandboxed.\n` +
            `   ${remediation(auto)}`
        );
        return "none";
    }
    return auto;
};

/**
 * Directories the test run must be able to write to: the OS temp root (isolated
 * app sandboxes, the JSON report, Chromium profiles), this suite's reports tree,
 * and the package/browser caches npx and Playwright touch on startup.
 *
 * Paths are realpath'd (macOS /tmp and /var are symlinks into /private, and
 * Seatbelt matches on real paths) and filtered to those that exist, since both
 * bwrap binds and Seatbelt subpaths want concrete directories.
 */
export const writableRootsFor = (
    uiTestsDir: string,
    {env = process.env, osPlatform = platform(), tempRoot}: {
        env?: NodeJS.ProcessEnv;
        osPlatform?: NodeJS.Platform;
        tempRoot: string;
    },
): string[] => {
    const home = env.HOME;
    const candidates = [
        tempRoot,
        join(uiTestsDir, "reports"),
        ...(home ? [join(home, ".cache"), join(home, ".npm")] : []),
        ...(home && osPlatform === "darwin" ? [join(home, "Library", "Caches")] : []),
        ...(env.PLAYWRIGHT_BROWSERS_PATH ? [env.PLAYWRIGHT_BROWSERS_PATH] : []),
        // macOS puts the per-user temp dir (…/T) and the per-user cache dir
        // (…/C) side by side under /private/var/folders/<x>/<y>; node and
        // Chromium write to both, so grant the shared parent.
        ...(osPlatform === "darwin" ? [dirname(tempRoot)] : []),
    ];

    const seen = new Set<string>();
    return candidates
        .filter((dir) => existsSync(dir))
        .map((dir) => realpathSync(dir))
        .filter((dir) => {
            if (seen.has(dir)) return false;
            seen.add(dir);
            return true;
        });
};

/** Escape a path for embedding in a Seatbelt profile string literal. */
const seatbeltLiteral = (value: string): string => `"${value.replace(/(["\\])/g, "\\$1")}"`;

/**
 * Build the Seatbelt profile: allow everything by default (network, exec,
 * mach lookups — the test run needs a working browser), then revoke write
 * access and hand it back only for the directories above plus /dev.
 */
export const buildSeatbeltProfile = (writableRoots: string[]): string => [
    "(version 1)",
    "(allow default)",
    "(deny file-write*)",
    `(allow file-write* (subpath "/dev"))`,
    ...writableRoots.map((dir) => `(allow file-write* (subpath ${seatbeltLiteral(dir)}))`),
].join("\n");

export interface WrapOptions {
    kind: SandboxKind;
    command: string;
    args: string[];
    writableRoots: string[];
}

/** Wrap `command`/`args` in the chosen sandbox, or return them unchanged. */
export const wrapWithSandbox = ({kind, command, args, writableRoots}: WrapOptions): [string, string[]] => {
    switch (kind) {
        case "bwrap":
            return ["bwrap", [
                "--ro-bind", "/", "/",
                "--dev", "/dev",
                "--proc", "/proc",
                ...writableRoots.flatMap((dir) => ["--bind", dir, dir]),
                "--unshare-pid",
                "--die-with-parent",
                command, ...args,
            ]];
        case "sandbox-exec":
            return ["sandbox-exec", ["-p", buildSeatbeltProfile(writableRoots), command, ...args]];
        case "none":
            return [command, args];
    }
};
