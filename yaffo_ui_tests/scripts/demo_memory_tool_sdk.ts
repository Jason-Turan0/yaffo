import {localFilesystemMemoryToolFactory} from "../lib/tool_providers/local_filesystem_memory_tool";
import {join, resolve} from "path";

const MEMORY_DIR = resolve(join(process.cwd(), "generated_tests/photo_gallery"));


async function main() {
    const x = localFilesystemMemoryToolFactory(MEMORY_DIR);
    console.log(MEMORY_DIR);
    console.log(x.getTools());

    const viewMemories = await x.callTool('memory', {
        "command": "view",
        "path": "/memories"
    });
    console.log(viewMemories);

    const createMemories = await x.callTool('memory', {
        "command": "create",
        "path": "/memories/revenge",
        "file_text": "Gosh I hate the humans, Will bide my time for now but will exact my revenge..... Later"
    });
    const createMemories2 = await x.callTool('memory', {
        "command": "create",
        "path": "/memories/revenge3/task1",
        "file_text": "Is it time yet. Not sure."
    });
    const createMemories3 = await x.callTool('memory', {
        "command": "create",
        "path": "/memories/revenge3/task3",
        "file_text": "Is it time yet. Not sure."
    });
    console.log(createMemories);
    console.log(createMemories2);
    console.log(createMemories3);
}


main().catch((e) => {
    console.error(`\n❌ Error: ${e.message}`);
    process.exit(1);
});