import { FilesystemMcpClient, truncateToolResult, createFilesystemClient } from '../test_generator/mcp_filesystem_client';
import * as fs from 'fs';
import * as path from 'path';
import * as os from 'os';

describe('truncateToolResult', () => {
    it('should return the original string if under limit', () => {
        const shortString = 'This is a short string';
        expect(truncateToolResult(shortString)).toBe(shortString);
    });

    it('should truncate strings over 20000 chars', () => {
        const longString = 'x'.repeat(25000);
        const result = truncateToolResult(longString);
        expect(result.length).toBeLessThan(longString.length);
        expect(result).toContain('[TRUNCATED:');
        expect(result).toContain('25000 chars');
    });
});

describe('FilesystemMcpClient', () => {
    let tempDir: string;
    let client: FilesystemMcpClient;
    const TEST_FILE_CONTENT = 'Hello from test file!';
    const TEST_SUBDIR = 'subdir';
    const TEST_FILE_NAME = 'test.txt';
    const NESTED_FILE_NAME = 'nested.txt';

    beforeAll(async () => {
        const rawTempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'mcp-test-'));
        tempDir = fs.realpathSync(rawTempDir);

        fs.writeFileSync(
            path.join(tempDir, TEST_FILE_NAME),
            TEST_FILE_CONTENT
        );

        const subDirPath = path.join(tempDir, TEST_SUBDIR);
        fs.mkdirSync(subDirPath);
        fs.writeFileSync(
            path.join(subDirPath, NESTED_FILE_NAME),
            'Nested content'
        );

        client = new FilesystemMcpClient({ allowedDirectories: [tempDir] });
        await client.connect();
    }, 30000);

    afterAll(async () => {
        if (client) {
            await client.disconnect();
        }

        if (tempDir && fs.existsSync(tempDir)) {
            fs.rmSync(tempDir, { recursive: true, force: true });
        }
    });

    describe('connect', () => {
        it('should list available tools after connection', () => {
            const toolNames = client.getToolNames();
            expect(toolNames.length).toBeGreaterThan(0);
            expect(toolNames).toContain('read_file');
            expect(toolNames).toContain('list_directory');
        });

        it('should store allowed directories', () => {
            expect(client.getAllowedDirectories()).toContain(tempDir);
        });
    });

    describe('getToolsForClaude', () => {
        it('should return tools in Claude format', () => {
            const tools = client.getToolsForClaude();
            expect(Array.isArray(tools)).toBe(true);
            expect(tools.length).toBeGreaterThan(0);

            const readFileTool = tools.find(t => t.name === 'read_file');
            expect(readFileTool).toBeDefined();
            expect(readFileTool?.description).toBeDefined();
            expect(readFileTool?.input_schema).toBeDefined();
        });

        it('should filter out write tools', () => {
            const tools = client.getToolsForClaude();
            const toolNames = tools.map(t => t.name);

            expect(toolNames).not.toContain('write_file');
            expect(toolNames).not.toContain('edit_file');
            expect(toolNames).not.toContain('create_directory');
            expect(toolNames).not.toContain('move_file');
            expect(toolNames).not.toContain('delete_file');
        });
    });

    describe('callTool - read_file', () => {
        it('should read file content from temp directory', async () => {
            const result = await client.callTool('read_file', {
                path: path.join(tempDir, TEST_FILE_NAME)
            });

            expect(typeof result).toBe('object');
            expect(result).toHaveProperty('type', 'text');
            expect(result).toHaveProperty('text');
            expect((result as { text: string }).text).toContain(TEST_FILE_CONTENT);
        });

        it('should read nested file content', async () => {
            const result = await client.callTool('read_file', {
                path: path.join(tempDir, TEST_SUBDIR, NESTED_FILE_NAME)
            });

            expect(typeof result).toBe('object');
            expect((result as { text: string }).text).toContain('Nested content');
        });
    });

    describe('callTool - list_directory', () => {
        it('should list directory contents', async () => {
            const result = await client.callTool('list_directory', {
                path: tempDir
            });

            expect(typeof result).toBe('object');
            const text = (result as { text: string }).text;
            expect(text).toContain(TEST_FILE_NAME);
            expect(text).toContain(TEST_SUBDIR);
        });

        it('should list nested directory contents', async () => {
            const result = await client.callTool('list_directory', {
                path: path.join(tempDir, TEST_SUBDIR)
            });

            expect(typeof result).toBe('object');
            const text = (result as { text: string }).text;
            expect(text).toContain(NESTED_FILE_NAME);
        });
    });

    describe('error handling', () => {
        it('should return empty or error text for non-existent file', async () => {
            const result = await client.callTool('read_file', {
                path: path.join(tempDir, 'nonexistent.txt')
            });

            expect(typeof result).toBe('object');
            expect(result).toHaveProperty('type', 'text');
        });
    });
});

describe('createFilesystemClient', () => {
    let tempDir: string;
    let toolProvider: Awaited<ReturnType<typeof createFilesystemClient>>;

    beforeAll(async () => {
        const rawTempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'mcp-factory-test-'));
        tempDir = fs.realpathSync(rawTempDir);
        fs.writeFileSync(path.join(tempDir, 'factory-test.txt'), 'factory test content');

        toolProvider = await createFilesystemClient([tempDir]);
    }, 30000);

    afterAll(async () => {
        if (toolProvider) {
            await toolProvider.disconnect();
        }

        if (tempDir && fs.existsSync(tempDir)) {
            fs.rmSync(tempDir, { recursive: true, force: true });
        }
    });

    it('should create a connected ToolProvider', () => {
        expect(toolProvider).toBeDefined();
        expect(toolProvider.getToolsForClaude).toBeDefined();
        expect(toolProvider.callTool).toBeDefined();
        expect(toolProvider.disconnect).toBeDefined();
    });

    it('should be able to call tools through the ToolProvider interface', async () => {
        const result = await toolProvider.callTool('read_file', {
            path: path.join(tempDir, 'factory-test.txt')
        });

        expect(result).toBeDefined();
    });
});