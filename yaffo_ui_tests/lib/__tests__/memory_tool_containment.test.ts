import {describe, expect, it} from "@jest/globals";
import {existsSync, mkdirSync, mkdtempSync, symlinkSync, writeFileSync} from "fs";
import {tmpdir} from "os";
import {join} from "path";
import {localFilesystemMemoryToolFactory} from "@lib/tool_providers/local_filesystem_memory_tool";

/**
 * The memory tool is the only write the healed model controls the path of, and
 * it runs beside generated_tests/_support (the reviewed fs helpers) and the
 * application source. These pin the containment.
 */
const newTool = () => {
    const featureDir = mkdtempSync(join(tmpdir(), "memtool-"));
    return {featureDir, tool: localFilesystemMemoryToolFactory(featureDir)};
};

const write = (tool: ReturnType<typeof newTool>["tool"], path: string) =>
    tool.callTool("memory", {command: "create", path, file_text: "x"});

describe("memory tool path containment", () => {
    it("writes inside /memories", async () => {
        const {featureDir, tool} = newTool();
        await write(tool, "/memories/note.md");
        expect(existsSync(join(featureDir, "memories", "note.md"))).toBe(true);
    });

    it("refuses to traverse into _support", async () => {
        const {tool} = newTool();
        await expect(write(tool, "/memories/../../_support/sandbox-fs.ts")).rejects.toThrow(/escape|\.\./);
    });

    it("refuses to traverse into the application source", async () => {
        const {tool} = newTool();
        await expect(write(tool, "/memories/../../../../yaffo/app.py")).rejects.toThrow(/escape|\.\./);
    });

    it("refuses an absolute path outside /memories", async () => {
        const {tool} = newTool();
        await expect(write(tool, "/etc/passwd")).rejects.toThrow(/must start with \/memories/);
    });

    it("refuses a sibling directory that merely shares the prefix", async () => {
        // "…/memories-evil" startsWith "…/memories" as a string; the check has
        // to compare on a separator or this lands in the feature directory.
        const {featureDir, tool} = newTool();
        await expect(write(tool, "/memories/../memories-evil/note.md")).rejects.toThrow(/escape|\.\./);
        expect(existsSync(join(featureDir, "memories-evil"))).toBe(false);
    });

    // path.resolve normalises `..` but does NOT follow symlinks, so lexical
    // containment passes a symlinked path straight through and fs writes to
    // the target. The tool cannot create a symlink itself, but it follows one
    // already on disk.
    it("refuses to write through a symlink pointing out of /memories", async () => {
        const generatedTests = mkdtempSync(join(tmpdir(), "gt-"));
        const support = join(generatedTests, "_support");
        const featureDir = join(generatedTests, "themes");
        mkdirSync(support, {recursive: true});
        mkdirSync(featureDir, {recursive: true});
        const tool = localFilesystemMemoryToolFactory(featureDir);
        symlinkSync(support, join(featureDir, "memories", "out"), "dir");

        await expect(write(tool, "/memories/out/sandbox-fs.ts")).rejects.toThrow(/escape/);
        expect(existsSync(join(support, "sandbox-fs.ts"))).toBe(false);
    });

    it("refuses to read through a symlink pointing out of /memories", async () => {
        const generatedTests = mkdtempSync(join(tmpdir(), "gt-"));
        const secrets = join(generatedTests, "secrets");
        const featureDir = join(generatedTests, "themes");
        mkdirSync(secrets, {recursive: true});
        mkdirSync(featureDir, {recursive: true});
        writeFileSync(join(secrets, "key.txt"), "s3cret");
        const tool = localFilesystemMemoryToolFactory(featureDir);
        symlinkSync(secrets, join(featureDir, "memories", "peek"), "dir");

        await expect(tool.callTool("memory", {command: "view", path: "/memories/peek/key.txt"}))
            .rejects.toThrow(/escape/);
    });

    // The guard must not reject symlinks wholesale — one resolving back inside
    // the memories root is legitimate and must still work.
    it("allows a symlink that resolves within /memories", async () => {
        const {featureDir, tool} = newTool();
        const realDir = join(featureDir, "memories", "real");
        mkdirSync(realDir, {recursive: true});
        symlinkSync(realDir, join(featureDir, "memories", "alias"), "dir");

        await write(tool, "/memories/alias/note.md");
        expect(existsSync(join(realDir, "note.md"))).toBe(true);
    });
});
