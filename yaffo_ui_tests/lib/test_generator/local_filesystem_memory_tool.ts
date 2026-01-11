import * as fs from 'fs/promises';
import * as path from 'path';
import Anthropic from '@anthropic-ai/sdk';
import {betaMemoryTool, type MemoryToolHandlers} from '@anthropic-ai/sdk/helpers/beta/memory';
import type {
    BetaMemoryTool20250818ViewCommand,
    BetaMemoryTool20250818CreateCommand,
    BetaMemoryTool20250818DeleteCommand,
    BetaMemoryTool20250818InsertCommand,
    BetaMemoryTool20250818RenameCommand,
    BetaMemoryTool20250818StrReplaceCommand,
} from '@anthropic-ai/sdk/resources/beta';
import {ToolProvider} from "@lib/test_generator/toolprovider.types";
import {Tool} from "@anthropic-ai/sdk/resources.js";

import {Client} from "@modelcontextprotocol/sdk/client/index.js";
type CallToolReturn = ReturnType<Client["callTool"]>;

async function exists(path: string) {
    return await fs
        .access(path)
        .then(() => true)
        .catch(() => false);
}

class LocalFilesystemMemoryTool implements MemoryToolHandlers, ToolProvider {
    private basePath: string;
    private memoryRoot: string;

    constructor(basePath: string = './memory') {
        this.basePath = basePath;
        this.memoryRoot = path.join(this.basePath, 'memories');
    }

    getToolsForClaude(): Tool[] {
        return [betaMemoryTool(this) as Tool];
    }

    async callTool(name: string, args: Record<string, unknown>): Promise<CallToolReturn> {
        const toolImpl = betaMemoryTool(this);
        const command = toolImpl.parse(name)
        const result = await toolImpl.run(command)

        return result as unknown as CallToolReturn;
    }

    disconnect(): Promise<void> {
        return new Promise(resolve => {
            resolve()
        })
    }

    private validatePath(memoryPath: string): string {
        if (!memoryPath.startsWith('/memories')) {
            throw new Error(`Path must start with /memories, got: ${memoryPath}`);
        }

        const relativePath = memoryPath.slice('/memories'.length).replace(/^\//, '');
        const fullPath = relativePath ? path.join(this.memoryRoot, relativePath) : this.memoryRoot;

        const resolvedPath = path.resolve(fullPath);
        const resolvedRoot = path.resolve(this.memoryRoot);
        if (!resolvedPath.startsWith(resolvedRoot)) {
            throw new Error(`Path ${memoryPath} would escape /memories directory`);
        }

        return resolvedPath;
    }

    async view(command: BetaMemoryTool20250818ViewCommand): Promise<string> {
        const fullPath = this.validatePath(command.path);

        if (!(await exists(fullPath))) {
            throw new Error(`Path not found: ${command.path}`);
        }

        const stat = await fs.stat(fullPath);

        if (stat.isDirectory()) {
            const items: string[] = [];
            const dirContents = await fs.readdir(fullPath);

            for (const item of dirContents.sort()) {
                if (item.startsWith('.')) {
                    continue;
                }
                const itemPath = path.join(fullPath, item);
                const itemStat = await fs.stat(itemPath);
                items.push(itemStat.isDirectory() ? `${item}/` : item);
            }

            return `Directory: ${command.path}\n` + items.map((item) => `- ${item}`).join('\n');
        } else if (stat.isFile()) {
            const content = await fs.readFile(fullPath, 'utf-8');
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

    async create(command: BetaMemoryTool20250818CreateCommand): Promise<string> {
        const fullPath = this.validatePath(command.path);
        const dir = path.dirname(fullPath);

        if (!(await exists(dir))) {
            await fs.mkdir(dir, {recursive: true});
            throw new Error(`Path not found: ${command.path}`);
        }

        await fs.writeFile(fullPath, command.file_text, 'utf-8');
        return `File created successfully at ${command.path}`;
    }

    async str_replace(command: BetaMemoryTool20250818StrReplaceCommand): Promise<string> {
        const fullPath = this.validatePath(command.path);

        if (!(await exists(fullPath))) {
            throw new Error(`File not found: ${command.path}`);
        }

        const stat = await fs.stat(fullPath);
        if (!stat.isFile()) {
            throw new Error(`Path is not a file: ${command.path}`);
        }

        const content = await fs.readFile(fullPath, 'utf-8');
        const count = content.split(command.old_str).length - 1;

        if (count === 0) {
            throw new Error(`Text not found in ${command.path}`);
        } else if (count > 1) {
            throw new Error(`Text appears ${count} times in ${command.path}. Must be unique.`);
        }

        const newContent = content.replace(command.old_str, command.new_str);
        await fs.writeFile(fullPath, newContent, 'utf-8');
        return `File ${command.path} has been edited`;
    }

    async insert(command: BetaMemoryTool20250818InsertCommand): Promise<string> {
        const fullPath = this.validatePath(command.path);

        if (!(await exists(fullPath))) {
            throw new Error(`File not found: ${command.path}`);
        }

        const stat = await fs.stat(fullPath);
        if (!stat.isFile()) {
            throw new Error(`Path is not a file: ${command.path}`);
        }

        const content = await fs.readFile(fullPath, 'utf-8');
        const lines = content.split('\n');

        if (command.insert_line < 0 || command.insert_line > lines.length) {
            throw new Error(`Invalid insert_line ${command.insert_line}. Must be 0-${lines.length}`);
        }

        lines.splice(command.insert_line, 0, command.insert_text.replace(/\n$/, ''));
        await fs.writeFile(fullPath, lines.join('\n'), 'utf-8');
        return `Text inserted at line ${command.insert_line} in ${command.path}`;
    }

    async delete(command: BetaMemoryTool20250818DeleteCommand): Promise<string> {
        const fullPath = this.validatePath(command.path);

        if (command.path === '/memories') {
            throw new Error('Cannot delete the /memories directory itself');
        }

        if (!(await exists(fullPath))) {
            throw new Error(`Path not found: ${command.path}`);
        }

        const stat = await fs.stat(fullPath);

        if (stat.isFile()) {
            await fs.unlink(fullPath);
            return `File deleted: ${command.path}`;
        } else if (stat.isDirectory()) {
            fs.rmdir(fullPath, {recursive: true});
            return `Directory deleted: ${command.path}`;
        } else {
            throw new Error(`Path not found: ${command.path}`);
        }
    }

    async rename(command: BetaMemoryTool20250818RenameCommand): Promise<string> {
        const oldFullPath = this.validatePath(command.old_path);
        const newFullPath = this.validatePath(command.new_path);

        if (!(await exists(oldFullPath))) {
            throw new Error(`Source path not found: ${command.old_path}`);
        }

        if (await exists(newFullPath)) {
            throw new Error(`Destination already exists: ${command.new_path}`);
        }

        const newDir = path.dirname(newFullPath);
        if (!(await exists(newDir))) {
            await fs.mkdir(newDir, {recursive: true});
        }

        await fs.rename(oldFullPath, newFullPath);
        return `Renamed ${command.old_path} to ${command.new_path}`;
    }
}

export const localFilesystemMemoryToolFactory = (basePath: string) => {
    return new LocalFilesystemMemoryTool(basePath)
}