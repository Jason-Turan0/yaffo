/**
 * Run one generated spec against its own isolated environment — the local
 * equivalent of a single CI fan-out leg (playwright.yml).
 *
 * Starts the environment from the seed cache (build it first with
 * `npm run seed:build`), derives the Playwright project and peer requirement
 * from the spec path, runs the spec, and tears the environment down.
 *
 * Usage:
 *   npm run test:spec -- generated_tests/albums/albums.spec.ts
 *   npm run test:spec -- generated_tests/sharing/sharing.spec.ts
 *   npm run test:spec -- <spec> --fresh      # seed inline instead of using the cache
 *   npm run test:spec -- <spec> --port 5005
 */
import {spawn} from "node:child_process";
import {resolve} from "node:path";

import {startIsolatedEnvironment} from "../lib/services/isolated_runner";
import {toEntry} from "./list_specs";

function parsePort(args: string[]): number {
    const i = args.findIndex((a) => a === "--port");
    return i !== -1 && args[i + 1] ? parseInt(args[i + 1], 10) : 5002;
}

async function runPlaywright(spec: string, project: string, env: NodeJS.ProcessEnv): Promise<number> {
    return new Promise((resolvePromise) => {
        const child = spawn("npx", ["playwright", "test", spec, "--project", project], {
            env,
            stdio: "inherit",
        });
        child.on("exit", (code) => resolvePromise(code ?? 1));
    });
}

async function main(): Promise<void> {
    const args = process.argv.slice(2);
    const fresh = args.includes("--fresh");
    const port = parsePort(args);
    const specArg = args.find((a) => !a.startsWith("--") && a !== String(port));

    if (!specArg) {
        console.error("Usage: npm run test:spec -- <spec-file> [--fresh] [--port <n>]");
        process.exit(1);
    }

    const entry = toEntry(resolve(process.cwd(), specArg));
    const withPeer = entry.peer === "true";
    console.log(`\n▶️  ${entry.spec} → project ${entry.project}${withPeer ? " (peer)" : ""}${fresh ? " [fresh seed]" : " [seed cache]"}`);

    let environment;
    try {
        environment = await startIsolatedEnvironment(port, {
            withPeer,
            preseeded: !fresh,
            copyPreseeded: !fresh,
        });
    } catch (e) {
        const message = e instanceof Error ? e.message : String(e);
        if (/preseeded/i.test(message)) {
            console.error("\n✖ No seed cache found. Run `npm run seed:build` first, or pass --fresh to seed inline.");
            process.exit(1);
        }
        throw e;
    }

    try {
        const code = await runPlaywright(entry.spec, entry.project, {
            ...process.env,
            SUITE: entry.id,
            BASE_URL: environment.baseUrl,
            PEER_URL: environment.peer?.baseUrl ?? "",
        });
        process.exitCode = code;
    } finally {
        await environment.cleanup();
    }
}

main().catch((e) => {
    console.error(`Fatal error: ${e instanceof Error ? e.message : String(e)}`);
    process.exit(1);
});
