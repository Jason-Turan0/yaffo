#!/usr/bin/env npx tsx
/**
 * Demo script for the Playwright MCP client.
 *
 * Navigates to the gallery page and takes a screenshot.
 *
 * Usage:
 *   npx tsx scripts/demo_playwright_mcp.ts
 *   npx tsx scripts/demo_playwright_mcp.ts --headed
 *   npx tsx scripts/demo_playwright_mcp.ts --install  # Install browsers first
 */

import {createPlaywrightClient} from "../lib/tool_providers/mcp_playwright_client";
import {writeFileSync, mkdirSync, existsSync} from "fs";
import {join, resolve} from "path";

const BASE_URL = process.env.BASE_URL || "http://127.0.0.1:5000";
const HEADED = process.argv.includes("--headed");
const INSTALL = process.argv.includes("--install");

const getResponseText = (result: any): string => {
    return result?.content?.[0]?.text || "";
};

const checkForError = (text: string): boolean => {
    if (text.includes("Error:") && text.includes("not installed")) {
        console.log(`   ⚠️  ${text.trim()}`);
        console.log("\n   💡 Run with --install flag to install browsers:");
        console.log("      npx tsx scripts/demo_playwright_mcp.ts --install");
        return true;
    }
    return false;
};

async function main() {
    console.log("🎭 Playwright MCP Demo");
    console.log(`   Base URL: ${BASE_URL}`);
    console.log(`   Headless: ${!HEADED}`);
    console.log("");

    const client = await createPlaywrightClient({
        headless: !HEADED,
        browser: "chromium",
        artifacts: {
            outputDir: resolve(process.cwd(), "reports", "artifacts"),
            saveVideo: "1280x720",

        }
    });

    try {
        if (INSTALL) {
            console.log("📦 Installing browsers...");
            const installResult = await client.callTool("browser_install", {});
            console.log(`   ${getResponseText(installResult)}`);
            console.log("");
        }
        await client.callTool('browser_navigate', {url: BASE_URL});
        await client.callTool('browser_click', {
            "element": "Year filter dropdown",
            "ref": "e27",
        });
        await client.callTool('browser_evaluate', {
            "function": "() => {\n  const firstImage = document.querySelector('.photo-card img');\n  return {\n    src: firstImage?.src,\n    alt: firstImage?.alt,\n    dataFallback: firstImage?.getAttribute('data-fallback')\n  };\n}"
        })
        await client.callTool('browser_snapshot', {});
        await client.callTool('browser_take_screenshot', {});
        console.log("\n🔒 Closing browser...");
        await client.disconnect();

        console.log("\n✅ Demo complete!");

    } finally {
        await client.disconnect();
    }
}

main().catch((e) => {
    console.error(`\n❌ Error: ${e.message}`);
    process.exit(1);
});