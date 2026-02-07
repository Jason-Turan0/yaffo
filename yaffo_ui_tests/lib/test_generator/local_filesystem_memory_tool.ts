import * as fs from 'fs';
import * as path from 'path';
import type {JSONSchema7} from "ai";
import {CallToolReturn, RawToolDefinition, ToolProvider} from "@lib/test_generator/toolprovider.types";

interface ViewCommand {
    command: "view";
    path: string;
    view_range?: [number, number];
}

interface CreateCommand {
    command: "create";
    path: string;
    file_text: string;
}

interface StrReplaceCommand {
    command: "str_replace";
    path: string;
    old_str: string;
    new_str: string;
}

interface InsertCommand {
    command: "insert";
    path: string;
    insert_line: number;
    insert_text: string;
}

interface DeleteCommand {
    command: "delete";
    path: string;
}

interface RenameCommand {
    command: "rename";
    old_path: string;
    new_path: string;
}

type MemoryCommand = ViewCommand | CreateCommand | StrReplaceCommand | InsertCommand | DeleteCommand | RenameCommand;

const MEMORY_TOOL_SCHEMA: JSONSchema7 = {
    type: "object",
    properties: {
        command: {
            type: "string",
            enum: ["view", "create", "str_replace", "insert", "delete", "rename"],
            description: "The memory operation to perform"
        },
        path: {
            type: "string",
            description: "The path within /memories to operate on. Must start with /memories"
        },
        file_text: {
            type: "string",
            description: "Content for the file (create command)"
        },
        old_str: {
            type: "string",
            description: "String to replace (str_replace command)"
        },
        new_str: {
            type: "string",
            description: "Replacement string (str_replace command)"
        },
        insert_line: {
            type: "number",
            description: "Line number to insert at (insert command)"
        },
        insert_text: {
            type: "string",
            description: "Text to insert (insert command)"
        },
        view_range: {
            type: "array",
            items: {type: "number"},
            description: "Optional [start, end] line range for view command"
        },
        old_path: {
            type: "string",
            description: "Source path (rename command)"
        },
        new_path: {
            type: "string",
            description: "Destination path (rename command)"
        }
    },
    required: ["command"]
};


class LocalFilesystemMemoryTool implements ToolProvider {
    private memoriesPath: string;

    constructor(basePath: string = './memory') {
        const memoriesPath = path.join(basePath, 'memories');
        if (!fs.existsSync(memoriesPath)) {
            fs.mkdirSync(memoriesPath, {recursive: true});
        }
        this.memoriesPath = memoriesPath;
    }

    getTools(): RawToolDefinition[] {
        return [{
            name: "memory",
            description: "A tool for managing persistent memory files. Supports viewing, creating, editing, and deleting files within the /memories directory.",
            inputSchema: MEMORY_TOOL_SCHEMA,
        }];
    }

    async callTool(_: string, args: Record<string, unknown>): Promise<CallToolReturn> {
        if (args == null || args['command'] == null) {
            throw new Error('command is required');
        }
        const command = args as unknown as MemoryCommand;
        const result = await this.runCommand(command);
        return {
            type: "text",
            text: result
        };
    }

    private async runCommand(command: MemoryCommand): Promise<string> {
        switch (command.command) {
            case "view":
                return this.view(command);
            case "create":
                return this.create(command);
            case "str_replace":
                return this.str_replace(command);
            case "insert":
                return this.insert(command);
            case "delete":
                return this.delete(command);
            case "rename":
                return this.rename(command);
            default:
                throw new Error(`Unknown command: ${(command as MemoryCommand).command}`);
        }
    }

    disconnect(): Promise<void> {
        return Promise.resolve();
    }

    private validatePath(memoryPath: string): string {
        if (!memoryPath.startsWith('/memories')) {
            throw new Error(`Path must start with /memories, got: ${memoryPath}`);
        }

        const relativePath = memoryPath.slice('/memories'.length).replace(/^\//, '');
        const fullPath = relativePath ? path.join(this.memoriesPath, relativePath) : this.memoriesPath;

        const resolvedPath = path.resolve(fullPath);
        const resolvedRoot = path.resolve(this.memoriesPath);
        if (!resolvedPath.startsWith(resolvedRoot)) {
            throw new Error(`Path ${memoryPath} would escape /memories directory`);
        }

        return resolvedPath;
    }

    private async view(command: ViewCommand): Promise<string> {
        const fullPath = this.validatePath(command.path);


        const stat = fs.statSync(fullPath);

        if (stat.isDirectory()) {
            const items: string[] = [];
            const dirContents = fs.readdirSync(fullPath);

            for (const item of dirContents.sort()) {
                if (item.startsWith('.')) {
                    continue;
                }
                const itemPath = path.join(fullPath, item);
                const itemStat = fs.statSync(itemPath);
                items.push(itemStat.isDirectory() ? `${item}/` : item);
            }

            return `Directory: ${command.path}\n` + items.map((item) => `- ${item}`).join('\n');
        } else if (stat.isFile()) {
            const content = fs.readFileSync(fullPath, 'utf-8');
            const lines = content.split('\n');

            let displayLines = lines;
            let startNum = 1;

            if (command.view_range && command.view_range.length === 2) {
                const startLine = Math.max(1, command.view_range[0]!) - 1;
                const endLine = command.view_range[1] === -1 ? lines.length : command.view_range[1];
                displayLines = lines.slice(startLine, endLine);
                startNum = startLine + 1;
            }

            const numberedLines = displayLines.map(
                (line, i) => `${String(i + startNum).padStart(4, ' ')}: ${line}`,
            );

            return numberedLines.join('\n');
        } else {
            throw new Error(`Path not found: ${command.path}`);
        }
    }

    private async create(command: CreateCommand): Promise<string> {
        const fullPath = this.validatePath(command.path);
        const dir = path.dirname(fullPath);

        if (!(fs.existsSync(dir))) {
            fs.mkdirSync(dir, {recursive: true});
        }

        fs.writeFileSync(fullPath, command.file_text, 'utf-8');
        return `File created successfully at ${command.path}`;
    }

    private async str_replace(command: StrReplaceCommand): Promise<string> {
        const fullPath = this.validatePath(command.path);

        if (!fs.existsSync(fullPath)) {
            throw new Error(`File not found: ${command.path}`);
        }

        const stat = fs.statSync(fullPath);
        if (!stat.isFile()) {
            throw new Error(`Path is not a file: ${command.path}`);
        }

        const content = fs.readFileSync(fullPath, 'utf-8');
        const count = content.split(command.old_str).length - 1;

        if (count === 0) {
            throw new Error(`Text not found in ${command.path}`);
        } else if (count > 1) {
            throw new Error(`Text appears ${count} times in ${command.path}. Must be unique.`);
        }

        const newContent = content.replace(command.old_str, command.new_str);
        fs.writeFileSync(fullPath, newContent, 'utf-8');
        return `File ${command.path} has been edited`;
    }

    private async insert(command: InsertCommand): Promise<string> {
        const fullPath = this.validatePath(command.path);

        if (!(fs.existsSync(fullPath))) {
            throw new Error(`File not found: ${command.path}`);
        }

        const stat = fs.statSync(fullPath);
        if (!stat.isFile()) {
            throw new Error(`Path is not a file: ${command.path}`);
        }

        const content = fs.readFileSync(fullPath, 'utf-8');
        const lines = content.split('\n');

        if (command.insert_line < 0 || command.insert_line > lines.length) {
            throw new Error(`Invalid insert_line ${command.insert_line}. Must be 0-${lines.length}`);
        }

        lines.splice(command.insert_line, 0, command.insert_text.replace(/\n$/, ''));
        fs.writeFileSync(fullPath, lines.join('\n'), 'utf-8');
        return `Text inserted at line ${command.insert_line} in ${command.path}`;
    }

    private async delete(command: DeleteCommand): Promise<string> {
        const fullPath = this.validatePath(command.path);

        if (command.path === '/memories') {
            throw new Error('Cannot delete the /memories directory itself');
        }

        if (!(fs.existsSync(fullPath))) {
            throw new Error(`Path not found: ${command.path}`);
        }

        const stat = fs.statSync(fullPath);

        if (stat.isFile()) {
            fs.unlinkSync(fullPath);
            return `File deleted: ${command.path}`;
        } else if (stat.isDirectory()) {
            fs.rmdirSync(fullPath, {recursive: true});
            return `Directory deleted: ${command.path}`;
        } else {
            throw new Error(`Path not found: ${command.path}`);
        }
    }

    private async rename(command: RenameCommand): Promise<string> {
        const oldFullPath = this.validatePath(command.old_path);
        const newFullPath = this.validatePath(command.new_path);

        if (!(fs.existsSync(oldFullPath))) {
            throw new Error(`Source path not found: ${command.old_path}`);
        }

        if (fs.existsSync(newFullPath)) {
            throw new Error(`Destination already exists: ${command.new_path}`);
        }

        const newDir = path.dirname(newFullPath);
        if (!(fs.existsSync(newDir))) {
            fs.mkdirSync(newDir, {recursive: true});
        }

        fs.renameSync(oldFullPath, newFullPath);
        return `Renamed ${command.old_path} to ${command.new_path}`;
    }
}

export const localFilesystemMemoryToolFactory = (basePath: string) => {
    return new LocalFilesystemMemoryTool(basePath)
}