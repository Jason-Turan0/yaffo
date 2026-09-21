import {existsSync} from "node:fs";
import {join, resolve} from "node:path";
import {createInterface} from "node:readline/promises";
import {parseArgs} from "node:util";

import {seedCacheDir, startIsolatedEnvironment} from "../lib/services/isolated_runner";
import {runPlaywrightTests, RunOptions} from "../lib/services/run_playwright_tests";
import {discoverSandboxedSuites, SandboxedSuite} from "../lib/services/run_sandboxed_suites";

type Mode = NonNullable<RunOptions["mode"]>;
const MODES: Mode[] = ["headed", "headless", "ui"];

interface LocalRunOptions {
    port: number;
    fresh: boolean;
    mode: Mode;
    signal?: AbortSignal;
}

export async function runLocalSuite(
    suite: SandboxedSuite,
    options: LocalRunOptions,
    dependencies = {startEnvironment: startIsolatedEnvironment, runTests: runPlaywrightTests},
): Promise<number> {
    if (options.signal?.aborted) return 130;
    const environment = await dependencies.startEnvironment(options.port, {
        withPeer: suite.withPeer,
        preseeded: !options.fresh,
        copyPreseeded: !options.fresh,
    });
    try {
        if (options.signal?.aborted) return 130;
        const reportRoot = `reports/local__${suite.id}`;
        console.log(`\nApp: ${environment.baseUrl}`);
        if (environment.peer) console.log(`Peer: ${environment.peer.baseUrl}`);
        console.log(`Reports: ${reportRoot}/html/index.html`);
        if (options.mode === "ui") {
            console.log("Close Playwright UI or press Ctrl+C to stop the environment.");
        }
        const result = await dependencies.runTests(environment.baseUrl, suite.specs, {
            mode: options.mode,
            reporters: "list,html,json",
            jsonReportOut: `${reportRoot}/results/test-results.json`,
            signal: options.signal,
            environment: {
                CI: "",
                SUITE: `local__${suite.id}`,
                PEER_URL: environment.peer?.baseUrl ?? "",
                PLAYWRIGHT_HTML_OUTPUT_DIR: `${reportRoot}/html`,
                PLAYWRIGHT_HTML_OPEN: "never",
            },
        });
        return options.signal?.aborted ? 130 : result.exitCode;
    } finally {
        await environment.cleanup();
    }
}

async function main(): Promise<void> {
    const {values} = parseArgs({options: {
        help: {type: "boolean", short: "h"},
        suite: {type: "string"},
        mode: {type: "string"},
        port: {type: "string", default: "5002"},
        fresh: {type: "boolean", default: false},
    }});
    if (values.help) {
        console.log(`Usage: npm run test:local -- [--suite <name>] [--mode headed|headless|ui] [--port <port>] [--fresh]

Choose a suite and run mode from the interactive menus. Sharing starts two app
instances; other suites start one. Seed-cache copies are discarded on exit.
--fresh seeds new data instead of copying the cache (slower).
Supply both --suite and --mode to skip the menus.`);
        return;
    }
    const port = Number(values.port);
    if (!/^\d+$/.test(values.port!) || !Number.isInteger(port) || port < 1 || port > 65534) {
        throw new Error("--port must be an integer from 1 to 65534 (sharing also uses port + 1)");
    }
    if (values.mode && !MODES.includes(values.mode as Mode)) {
        throw new Error("--mode must be headed, headless, or ui");
    }
    const suites = discoverSandboxedSuites();
    let suite = values.suite
        ? suites.find(item => item.directory === values.suite || item.directory === `generated_tests/${values.suite}`)
        : undefined;
    if (values.suite && !suite) {
        throw new Error(`Unknown suite: ${values.suite}. Available: ${suites.map(item => item.directory.replace(/^generated_tests[/\\]/, "")).join(", ")}`);
    }
    let mode = values.mode as Mode | undefined;
    const controller = new AbortController();
    const interrupt = () => controller.abort();
    process.on("SIGINT", interrupt);
    process.on("SIGTERM", interrupt);
    try {
        if (!suite || !mode) {
            if (!process.stdin.isTTY) {
                throw new Error("Interactive selection needs a terminal. Supply --suite <name> --mode <mode> for a non-interactive run.");
            }
            const prompt = createInterface({input: process.stdin, output: process.stdout});
            prompt.on("SIGINT", interrupt);
            const choose = async (label: string, count: number, fallback?: number): Promise<number> => {
                for (;;) {
                    const answer = (await prompt.question(label, {signal: controller.signal})).trim();
                    if (/^(q|quit)$/i.test(answer)) return 0;
                    const number = answer === "" ? fallback : Number(answer);
                    if (number !== undefined && Number.isInteger(number) && number >= 1 && number <= count) return number;
                    console.log(`Enter a number from 1 to ${count}, or q to quit.`);
                }
            };
            try {
                if (!suite) {
                    console.log("\nSelect a Playwright suite:\n");
                    suites.forEach((item, index) => console.log(
                        `  ${index + 1}. ${item.directory.replace(/^generated_tests[/\\]/, "")} — ${item.specs.length} spec file(s), ${item.withPeer ? "sharing (two instances)" : "standard environment"}`,
                    ));
                    const choice = await choose("\nSuite number (q to quit): ", suites.length);
                    if (!choice) return;
                    suite = suites[choice - 1];
                }
                if (!mode) {
                    console.log("\n  1. Headed — watch tests run in a browser\n  2. Headless — run in the terminal\n  3. Playwright UI — select, inspect, and rerun tests");
                    const choice = await choose("\nRun mode [1] (q to quit): ", MODES.length, 1);
                    if (!choice) return;
                    mode = MODES[choice - 1];
                }
            } finally {
                prompt.close();
            }
        }
        if (!values.fresh) {
            const roles: ("primary" | "peer")[] = suite.withPeer ? ["primary", "peer"] : ["primary"];
            if (roles.some(role => !existsSync(join(seedCacheDir(role), "yaffo.db")))) {
                throw new Error("Seed cache missing. Run `npm run seed:build` once, or rerun with --fresh.");
            }
        }
        if (suite.withPeer) console.log("\nSharing scenarios are ordered: run the complete suite before retrying individual tests.");
        process.exitCode = await runLocalSuite(suite, {port, fresh: values.fresh!, mode, signal: controller.signal});
    } catch (error) {
        if (!controller.signal.aborted) throw error;
        process.exitCode = 130;
    } finally {
        process.removeListener("SIGINT", interrupt);
        process.removeListener("SIGTERM", interrupt);
    }
}

if (process.argv[1] && resolve(process.argv[1]).endsWith("test_local.ts")) {
    main().catch(error => {
        console.error(`Error: ${error instanceof Error ? error.message : String(error)}`);
        process.exitCode = 1;
    });
}
