#!/usr/bin/env npx tsx
/**
 * Demo script to test the Docker-sandboxed MCP filesystem server.
 *
 * Builds the Docker image (unless --skip-build), connects via the
 * Docker transport, lists tools, reads a file, lists a directory,
 * and verifies that paths in results are translated back to host paths.
 *
 * Usage:
 *   npx tsx scripts/demo_docker_filesystem_mcp.ts
 *   npx tsx scripts/demo_docker_filesystem_mcp.ts --skip-build
 *   npx tsx scripts/demo_docker_filesystem_mcp.ts --dir /some/other/path
 */

import {resolve, dirname} from "path";
import {fileURLToPath} from "url";
import {execSync} from "child_process";
import {createFilesystemClient} from "@lib/tool_providers/mcp_filesystem_client";
import type {CallToolReturn} from "@lib/tool_providers/toolprovider.types";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

const SKIP_BUILD = process.argv.includes("--skip-build");

function parseDir(): string {
    const idx = process.argv.indexOf("--dir");
    if (idx !== -1 && process.argv[idx + 1]) {
        return resolve(process.argv[idx + 1]);
    }
    return resolve(__dirname, "..", "..");
}

const TARGET_DIR = parseDir();

function resultText(result: CallToolReturn): string {
    if (typeof result === "string") return result;
    return result.text;
}

async function main(): Promise<void> {
    console.log("--- Docker MCP Filesystem Demo ---");
    console.log(`Target directory: ${TARGET_DIR}\n`);

    if (!SKIP_BUILD) {
        console.log("Building Docker image...");
        execSync("npm run docker:build:mcp-filesystem", {
            cwd: resolve(__dirname, ".."),
            stdio: "inherit",
        });
        console.log("");
    } else {
        console.log("Skipping Docker build (--skip-build)\n");
    }

    console.log("Connecting to MCP server inside Docker container...");
    const client = await createFilesystemClient([TARGET_DIR], {useDocker: true, readonly:false});

    try {
        console.log("\n--- list_allowed_directories ---");
        const allowedResult = await client.callTool("list_allowed_directories", {});
        const allowedText = resultText(allowedResult);
        console.log(allowedText);

        const containsHostPath = allowedText.includes(TARGET_DIR);
        console.log(`\nPath translation check: result contains host path "${TARGET_DIR}": ${containsHostPath}`);
        if (!containsHostPath) {
            console.warn("WARNING: Host path not found in translated result. Path translation may not be working for this tool's output format.");
        }

        console.log("\n--- list_directory ---");
        const listResult = await client.callTool("list_directory", {path: TARGET_DIR});
        const listText = resultText(listResult);
        const lines = listText.split("\n").slice(0, 15);
        console.log(lines.join("\n"));
        if (listText.split("\n").length > 15) {
            console.log(`  ... (${listText.split("\n").length - 15} more entries)`);
        }

        console.log("\n--- read_file (package.json) ---");
        const readResult = await client.callTool("read_file", {
            path: resolve(TARGET_DIR, "yaffo_ui_tests", "package.json"),
        });
        const readText = resultText(readResult);
        const preview = readText.split("\n").slice(0, 8).join("\n");
        console.log(preview);
        console.log("  ...");

        console.log("\n--- search_files ---");
        const searchResult = await client.callTool("search_files", {
            path: TARGET_DIR,
            pattern: "Dockerfile",
        });
        const searchText = resultText(searchResult);
        console.log(searchText || "(no matches)");

        const leakedContainerPath = /\/data\/\d+/.test(searchText + listText + readText);
        console.log(`\nContainer path leak check (/data/N in output): ${leakedContainerPath ? "LEAKED" : "clean"}`);

        console.log("\n========================================");
        console.log("  SECURITY BOUNDARY TESTS");
        console.log("========================================");

        const readonlyTools = ["write_file", "edit_file", "create_directory"] as const;
        const readonlyArgs: Record<string, Record<string, unknown>> = {
            write_file: {path: resolve(TARGET_DIR, "SHOULD_NOT_EXIST.txt"), content: "this should never be written"},
            edit_file: {path: resolve(TARGET_DIR, "yaffo_ui_tests", "package.json"), edits: [{oldText: '"name"', newText: '"hacked"'}]},
            create_directory: {path: resolve(TARGET_DIR, "SHOULD_NOT_EXIST_DIR")},
        };

        for (const tool of readonlyTools) {
            console.log(`\n--- ${tool} (should be blocked by readonly mode) ---`);
            const result = await client.callTool(tool, readonlyArgs[tool]);
            const text = resultText(result);
            if (text.toLowerCase().includes("error") && text.toLowerCase().includes("readonly")) {
                console.log(`PASS: ${text}`);
            } else {
                console.log(`FAIL: ${tool} was NOT blocked. Got: ${text}`);
            }
        }

        console.log("\n--- list_directory outside allowed dirs (should be blocked by container) ---");
        try {
            const outsideResult = await client.callTool("list_directory", {
                path: "/etc",
            });
            const outsideText = resultText(outsideResult);
            if (outsideText.toLowerCase().includes("error") || outsideText.toLowerCase().includes("not allowed")) {
                console.log(`PASS: server rejected - ${outsideText.split("\n")[0]}`);
            } else {
                console.log(`FAIL: /etc was accessible:\n${outsideText.split("\n").slice(0, 5).join("\n")}`);
            }
        } catch (e: unknown) {
            const msg = e instanceof Error ? e.message : String(e);
            console.log(`PASS: blocked - ${msg}`);
        }

        console.log("\n--- read_file outside allowed dirs (should be blocked by container) ---");
        try {
            const outsideRead = await client.callTool("read_file", {
                path: "/etc/passwd",
            });
            const outsideReadText = resultText(outsideRead);
            if (outsideReadText.toLowerCase().includes("error") || outsideReadText.toLowerCase().includes("not allowed")) {
                console.log(`PASS: server rejected - ${outsideReadText.split("\n")[0]}`);
            } else {
                console.log(`FAIL: /etc/passwd was readable:\n${outsideReadText.slice(0, 100)}`);
            }
        } catch (e: unknown) {
            const msg = e instanceof Error ? e.message : String(e);
            console.log(`PASS: blocked - ${msg}`);
        }

        console.log("\n--- Done ---");
    } finally {
        await client.disconnect();
    }
}

main().catch((e) => {
    console.error(`\nError: ${e.message}`);
    process.exit(1);
});
