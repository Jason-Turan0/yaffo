import {describe, expect, it} from "@jest/globals";
import {mkdtempSync, existsSync, rmSync} from "fs";
import {tmpdir} from "os";
import {join} from "path";
import {localFilesystemMemoryToolFactory} from "@lib/tool_providers/local_filesystem_memory_tool";
import {readFileSync} from "fs";

/**
 * `localFilesystemMemoryToolFactory` appends "memories" to what it is given, so callers
 * pass the *parent* directory. The docs pipeline passed the memories directory itself
 * in all three of its call sites, producing
 * `photo-details/memories/memories/page_content.md`.
 *
 * The nesting was the visible half. The quiet half: `hasMemories` probed
 * `{page}/memories`, which stayed empty, so every run was told the page had no notes
 * and the agent rediscovered the same dead ends each time.
 */
describe("the memory tool's base path", () => {
    it("appends 'memories' to the directory it is given", () => {
        const base = mkdtempSync(join(tmpdir(), "memprobe-"));
        try {
            localFilesystemMemoryToolFactory(base);
            expect(existsSync(join(base, "memories"))).toBe(true);
            expect(existsSync(join(base, "memories", "memories"))).toBe(false);
        } finally {
            rmSync(base, {recursive: true, force: true});
        }
    });

    it.each(["generate_cli.ts", "heal.ts"])(
        "%s gives it the page directory, not the memories directory", (file) => {
            const source = readFileSync(
                join(process.cwd(), "lib", "user_doc_automation", file), "utf8");
            const calls = source.match(/localFilesystemMemoryToolFactory\([^)]*\)/g) ?? [];
            expect(calls.length).toBeGreaterThan(0);
            for (const call of calls) expect(call).not.toMatch(/"memories"/);
        });
});
