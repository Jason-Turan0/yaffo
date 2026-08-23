/**
 * Run generated tests as a local CI-style fan-out. Specs in the same directory
 * share one disposable environment; different directories never share app or
 * database state.
 */
import {Dirent, existsSync, readdirSync, statSync} from "node:fs";
import {dirname, join, relative, resolve, sep} from "node:path";

import {
    IsolatedEnvironment,
    IsolatedEnvironmentOptions,
    seedCacheDir,
    startIsolatedEnvironment,
} from "@lib/services/isolated_runner";
import {runPlaywrightTests, RunOptions} from "@lib/services/run_playwright_tests";

export const DEFAULT_SANDBOX_CONCURRENCY = 5;
export const DEFAULT_SANDBOX_BASE_PORT = 5002;
export const SANDBOX_CONCURRENCY_ENV = "TEST_SANDBOX_CONCURRENCY";
export const SANDBOX_BASE_PORT_ENV = "TEST_SANDBOX_BASE_PORT";

export interface SandboxedSuite {
    id: string;
    directory: string;
    specs: string[];
    withPeer: boolean;
}

export interface SuiteRunResult {
    suite: SandboxedSuite;
    success: boolean;
    error?: Error;
}

export interface SandboxedSuiteRunOptions {
    uiTestsDir?: string;
    selectors?: string[];
    concurrency?: number;
    basePort?: number;
    reporters?: string;
    signal?: AbortSignal;
}

export interface SandboxedSuiteDependencies {
    startEnvironment?: (
        port: number,
        options: IsolatedEnvironmentOptions,
    ) => Promise<IsolatedEnvironment>;
    runTests?: typeof runPlaywrightTests;
    log?: (message: string) => void;
}

const collectSpecs = (path: string): string[] => {
    if (statSync(path).isFile()) {
        return path.endsWith(".spec.ts") ? [path] : [];
    }
    const specs: string[] = [];
    for (const entry of readdirSync(path, {withFileTypes: true}) as Dirent[]) {
        const child = resolve(path, entry.name);
        if (entry.isDirectory()) specs.push(...collectSpecs(child));
        else if (entry.isFile() && child.endsWith(".spec.ts")) specs.push(child);
    }
    return specs;
};

const isWithin = (root: string, path: string): boolean => {
    const rel = relative(root, path);
    return rel === "" || (!rel.startsWith(`..${sep}`) && rel !== "..");
};

/** Discover specs and group them by the directory containing each spec file. */
export const discoverSandboxedSuites = (
    uiTestsDir = resolve(process.cwd()),
    selectors: string[] = [],
): SandboxedSuite[] => {
    const specRoot = resolve(uiTestsDir, "generated_tests");
    const roots = selectors.length > 0
        ? selectors.map((selector) => resolve(uiTestsDir, selector))
        : [specRoot];

    const missing = roots.filter((path) => !existsSync(path));
    if (missing.length > 0) {
        throw new Error(`Test path(s) not found:\n${missing.map((path) => `  ${path}`).join("\n")}`);
    }

    const specs = [...new Set(roots.flatMap(collectSpecs).map((path) => resolve(path)))].sort();
    const outside = specs.filter((spec) => !isWithin(specRoot, spec));
    if (outside.length > 0) {
        throw new Error(`Generated test path(s) must be under ${specRoot}`);
    }
    if (specs.length === 0) {
        throw new Error("No generated .spec.ts files found for the requested path(s)");
    }

    const grouped = new Map<string, string[]>();
    for (const spec of specs) {
        const directory = dirname(spec);
        grouped.set(directory, [...(grouped.get(directory) ?? []), spec]);
    }

    return [...grouped.entries()].map(([directory, suiteSpecs]) => {
        const relativeDirectory = relative(specRoot, directory) || "root";
        const firstSegment = relativeDirectory.split(/[\\/]/)[0];
        return {
            id: relativeDirectory.replace(/[^a-zA-Z0-9]+/g, "__"),
            directory: relative(uiTestsDir, directory),
            specs: suiteSpecs.map((spec) => relative(uiTestsDir, spec)),
            withPeer: firstSegment === "sharing",
        };
    }).sort((a, b) => a.id.localeCompare(b.id));
};

export const parsePositiveInteger = (name: string, raw: string | undefined, fallback: number): number => {
    if (raw === undefined || raw.trim() === "") return fallback;
    if (!/^\d+$/.test(raw.trim()) || Number(raw) < 1) {
        throw new Error(`${name} must be a positive integer; received ${JSON.stringify(raw)}`);
    }
    return Number(raw);
};

/**
 * A fixed set of workers gives every active environment a stable two-port slot
 * (the second port is reserved for sharing's peer instance).
 */
export const runWorkerPool = async <T, R>(
    items: T[],
    concurrency: number,
    worker: (item: T, workerIndex: number) => Promise<R>,
    signal?: AbortSignal,
): Promise<R[]> => {
    const results: R[] = [];
    let next = 0;
    const lanes = Array.from({length: Math.min(concurrency, items.length)}, (_, workerIndex) =>
        (async () => {
            while (!signal?.aborted) {
                const index = next++;
                if (index >= items.length) return;
                results[index] = await worker(items[index], workerIndex);
            }
        })()
    );
    await Promise.all(lanes);
    return results.filter((result) => result !== undefined);
};

export const runSandboxedSuites = async (
    options: SandboxedSuiteRunOptions = {},
    dependencies: SandboxedSuiteDependencies = {},
): Promise<SuiteRunResult[]> => {
    const uiTestsDir = resolve(options.uiTestsDir ?? process.cwd());
    const concurrency = options.concurrency ?? DEFAULT_SANDBOX_CONCURRENCY;
    const basePort = options.basePort ?? DEFAULT_SANDBOX_BASE_PORT;
    if (!Number.isInteger(concurrency) || concurrency < 1) {
        throw new Error("concurrency must be a positive integer");
    }
    if (!Number.isInteger(basePort) || basePort < 1 || basePort + (concurrency * 2) - 1 > 65_535) {
        throw new Error("basePort does not leave enough valid ports for the requested concurrency");
    }

    const suites = discoverSandboxedSuites(uiTestsDir, options.selectors);
    if (!dependencies.startEnvironment) {
        const requiredCaches = [seedCacheDir("primary")];
        if (suites.some((suite) => suite.withPeer)) requiredCaches.push(seedCacheDir("peer"));
        const missingCaches = requiredCaches.filter((dir) => !existsSync(join(dir, "yaffo.db")));
        if (missingCaches.length > 0) {
            throw new Error("Seed cache missing; run `npm run seed:build` before `npm run test:sandboxed`");
        }
    }
    const startEnvironment = dependencies.startEnvironment ?? startIsolatedEnvironment;
    const runTests = dependencies.runTests ?? runPlaywrightTests;
    const log = dependencies.log ?? console.log;
    const reporters = options.reporters ?? "json,html,list,junit";

    log(`\nFound ${suites.length} suite director${suites.length === 1 ? "y" : "ies"}; ` +
        `running up to ${concurrency} concurrently.`);

    return runWorkerPool(suites, concurrency, async (suite, workerIndex) => {
        const port = basePort + (workerIndex * 2);
        let environment: IsolatedEnvironment | undefined;
        let runError: Error | undefined;
        let success = false;
        log(`\n▶ [${suite.id}] ${suite.specs.length} spec file(s) on port ${port}`);

        try {
            environment = await startEnvironment(port, {
                withPeer: suite.withPeer,
                preseeded: true,
                copyPreseeded: true,
            });
            if (options.signal?.aborted) {
                throw new Error("Sandboxed test run interrupted");
            }

            const reportRoot = `reports/${suite.id}`;
            const runOptions: RunOptions = {
                reporters,
                jsonReportOut: `${reportRoot}/results/test-results.json`,
                environment: {
                    SUITE: suite.id,
                    PEER_URL: environment.peer?.baseUrl ?? "",
                    PLAYWRIGHT_HTML_OUTPUT_DIR: `${reportRoot}/html`,
                    PLAYWRIGHT_JUNIT_OUTPUT_FILE: `${reportRoot}/results/junit.xml`,
                },
                signal: options.signal,
            };
            const result = await runTests(environment.baseUrl, suite.specs, runOptions);
            success = result.success;
            if (!success) runError = new Error(`Playwright exited with code ${result.exitCode}`);
        } catch (error) {
            runError = error instanceof Error ? error : new Error(String(error));
        } finally {
            if (environment) {
                try {
                    await environment.cleanup();
                } catch (error) {
                    const cleanupError = error instanceof Error ? error : new Error(String(error));
                    runError = runError
                        ? new Error(`${runError.message}; cleanup failed: ${cleanupError.message}`)
                        : cleanupError;
                    success = false;
                }
            }
        }

        log(`${success ? "✓" : "✖"} [${suite.id}]${runError ? ` ${runError.message}` : ""}`);
        return {suite, success, error: runError};
    }, options.signal);
};

interface CliOptions {
    selectors: string[];
    reporters?: string;
    basePort: number;
    concurrency: number;
}

const parseCli = (args: string[]): CliOptions => {
    const consumed = new Set<number>();
    const valueFor = (...names: string[]): string | undefined => {
        const index = args.findIndex((arg) => names.includes(arg));
        if (index === -1) return undefined;
        if (!args[index + 1]) throw new Error(`${args[index]} requires a value`);
        consumed.add(index).add(index + 1);
        return args[index + 1];
    };

    const reporters = valueFor("--reporter", "-r");
    const basePort = parsePositiveInteger(
        SANDBOX_BASE_PORT_ENV,
        valueFor("--base-port") ?? process.env[SANDBOX_BASE_PORT_ENV],
        DEFAULT_SANDBOX_BASE_PORT,
    );
    const concurrency = parsePositiveInteger(
        SANDBOX_CONCURRENCY_ENV,
        process.env[SANDBOX_CONCURRENCY_ENV],
        DEFAULT_SANDBOX_CONCURRENCY,
    );
    const selectors = args.filter((arg, index) => !consumed.has(index) && !arg.startsWith("-"));
    return {selectors, reporters, basePort, concurrency};
};

const main = async (): Promise<void> => {
    const cli = parseCli(process.argv.slice(2));
    const controller = new AbortController();
    let interrupted = false;
    const interrupt = () => {
        if (!interrupted) console.error("\nInterrupt received; stopping tests and cleaning active sandboxes...");
        interrupted = true;
        controller.abort();
    };
    process.on("SIGINT", interrupt);
    process.on("SIGTERM", interrupt);

    try {
        const results = await runSandboxedSuites({...cli, signal: controller.signal});
        const failures = results.filter((result) => !result.success);
        console.log(`\n${results.length - failures.length}/${results.length} suite directories passed.`);
        process.exitCode = interrupted ? 130 : failures.length > 0 ? 1 : 0;
    } finally {
        process.removeListener("SIGINT", interrupt);
        process.removeListener("SIGTERM", interrupt);
    }
};

if (process.argv[1] && resolve(process.argv[1]).endsWith("run_sandboxed_suites.ts")) {
    main().catch((error) => {
        console.error(`Fatal error: ${error instanceof Error ? error.message : String(error)}`);
        process.exitCode = 1;
    });
}
