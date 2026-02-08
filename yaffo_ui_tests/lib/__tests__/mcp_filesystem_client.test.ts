import * as fs from 'fs';
import * as path from 'path';
import * as os from 'os';
import {
    FilesystemMcpClient,
    createFilesystemClient,
    buildDockerTransportConfig,
    translateHostToContainer,
    translateContainerToHost,
    translateToolArgs,
    translateToolResult,
} from '../tool_providers/mcp_filesystem_client';
import {truncateToolResultIfNeeded} from "@lib/tool_providers/utils";

describe('truncateToolResult', () => {
    it('should return the original string if under limit', () => {
        const shortString = 'This is a short string';
        expect(truncateToolResultIfNeeded(shortString)).toBe(shortString);
    });

    it('should truncate strings over 20000 chars', () => {
        const longString = 'x'.repeat(25000);
        const result = truncateToolResultIfNeeded(longString);
        expect(result.length).toBeLessThan(longString.length);
        expect(result).toContain('[TRUNCATED:');
        expect(result).toContain('25000 chars');
    });
});

describe('buildDockerTransportConfig', () => {
    it('should produce correct docker run args for a single directory', () => {
        const config = buildDockerTransportConfig(['/Users/me/project']);

        expect(config.command).toBe('docker');
        expect(config.args).toContain('run');
        expect(config.args).toContain('--rm');
        expect(config.args).toContain('-i');
        expect(config.args).toContain('--network');
        expect(config.args).toContain('none');
        expect(config.args).toContain('-v');
        expect(config.args).toContain('/Users/me/project:/data/0:ro');
        expect(config.args).toContain('yaffo-mcp-filesystem:latest');
        expect(config.args[config.args.length - 1]).toBe('/data/0');
    });

    it('should produce volume mounts and container dirs for multiple directories', () => {
        const config = buildDockerTransportConfig(['/host/dir0', '/host/dir1', '/host/dir2']);

        expect(config.args).toContain('/host/dir0:/data/0:ro');
        expect(config.args).toContain('/host/dir1:/data/1:ro');
        expect(config.args).toContain('/host/dir2:/data/2:ro');

        const lastThree = config.args.slice(-3);
        expect(lastThree).toEqual(['/data/0', '/data/1', '/data/2']);
    });

    it('should use a custom docker image when provided', () => {
        const config = buildDockerTransportConfig(['/tmp'], 'my-custom-image:v2');

        expect(config.args).toContain('my-custom-image:v2');
        expect(config.args).not.toContain('yaffo-mcp-filesystem:latest');
    });

    it('should include --network none for isolation', () => {
        const config = buildDockerTransportConfig(['/tmp']);
        const networkIdx = config.args.indexOf('--network');

        expect(networkIdx).toBeGreaterThan(-1);
        expect(config.args[networkIdx + 1]).toBe('none');
    });

    it('should mount volumes as read-only by default', () => {
        const config = buildDockerTransportConfig(['/host/dir']);
        expect(config.args).toContain('/host/dir:/data/0:ro');
    });

    it('should mount volumes as read-write when readonly is false', () => {
        const config = buildDockerTransportConfig(['/host/dir'], undefined, false);
        expect(config.args).toContain('/host/dir:/data/0');
        expect(config.args).not.toContain('/host/dir:/data/0:ro');
    });
});

describe('translateHostToContainer', () => {
    it('should translate an exact host directory match', () => {
        const map = new Map([['/Users/me/project', '/data/0']]);
        expect(translateHostToContainer('/Users/me/project', map)).toBe('/data/0');
    });

    it('should translate a file within a host directory', () => {
        const map = new Map([['/Users/me/project', '/data/0']]);
        expect(translateHostToContainer('/Users/me/project/src/app.ts', map)).toBe('/data/0/src/app.ts');
    });

    it('should return the path unchanged if no prefix matches', () => {
        const map = new Map([['/Users/me/project', '/data/0']]);
        expect(translateHostToContainer('/other/path/file.txt', map)).toBe('/other/path/file.txt');
    });

    it('should handle overlapping prefixes by matching the longest first', () => {
        const map = new Map([
            ['/Users/me/project', '/data/0'],
            ['/Users/me/project/subdir', '/data/1'],
        ]);
        expect(translateHostToContainer('/Users/me/project/subdir/file.txt', map)).toBe('/data/1/file.txt');
        expect(translateHostToContainer('/Users/me/project/other.txt', map)).toBe('/data/0/other.txt');
    });

    it('should not match partial directory names', () => {
        const map = new Map([['/Users/me/proj', '/data/0']]);
        expect(translateHostToContainer('/Users/me/project/file.txt', map)).toBe('/Users/me/project/file.txt');
    });
});

describe('translateContainerToHost', () => {
    it('should translate a container path back to host path', () => {
        const map = new Map([['/data/0', '/Users/me/project']]);
        expect(translateContainerToHost('/data/0/src/app.ts', map)).toBe('/Users/me/project/src/app.ts');
    });

    it('should translate an exact container directory match', () => {
        const map = new Map([['/data/0', '/Users/me/project']]);
        expect(translateContainerToHost('/data/0', map)).toBe('/Users/me/project');
    });

    it('should return the path unchanged if no prefix matches', () => {
        const map = new Map([['/data/0', '/Users/me/project']]);
        expect(translateContainerToHost('/other/path', map)).toBe('/other/path');
    });

    it('should handle overlapping container prefixes', () => {
        const map = new Map([
            ['/data/0', '/Users/me/project'],
            ['/data/00', '/Users/me/other'],
        ]);
        expect(translateContainerToHost('/data/00/file.txt', map)).toBe('/Users/me/other/file.txt');
        expect(translateContainerToHost('/data/0/file.txt', map)).toBe('/Users/me/project/file.txt');
    });
});

describe('translateToolArgs', () => {
    const hostToContainerMap = new Map([
        ['/Users/me/project', '/data/0'],
        ['/Users/me/other', '/data/1'],
    ]);

    it('should translate a single path argument', () => {
        const args = {path: '/Users/me/project/src/app.ts'};
        const result = translateToolArgs(args, hostToContainerMap);
        expect(result.path).toBe('/data/0/src/app.ts');
    });

    it('should translate a paths array argument', () => {
        const args = {
            paths: [
                '/Users/me/project/file1.ts',
                '/Users/me/other/file2.ts',
            ],
        };
        const result = translateToolArgs(args, hostToContainerMap);
        expect(result.paths).toEqual(['/data/0/file1.ts', '/data/1/file2.ts']);
    });

    it('should leave non-path args unchanged', () => {
        const args = {path: '/Users/me/project/f.ts', encoding: 'utf-8'};
        const result = translateToolArgs(args, hostToContainerMap);
        expect(result.encoding).toBe('utf-8');
    });

    it('should not mutate the original args object', () => {
        const args = {path: '/Users/me/project/f.ts'};
        translateToolArgs(args, hostToContainerMap);
        expect(args.path).toBe('/Users/me/project/f.ts');
    });
});

describe('translateToolResult', () => {
    const containerToHostMap = new Map([
        ['/data/0', '/Users/me/project'],
        ['/data/1', '/Users/me/other'],
    ]);

    it('should replace container paths with host paths in text', () => {
        const text = 'File contents of /data/0/src/app.ts:\nconsole.log("hello")';
        const result = translateToolResult(text, containerToHostMap);
        expect(result).toBe('File contents of /Users/me/project/src/app.ts:\nconsole.log("hello")');
    });

    it('should replace multiple container paths in the same text', () => {
        const text = 'Compared /data/0/a.ts with /data/1/b.ts';
        const result = translateToolResult(text, containerToHostMap);
        expect(result).toBe('Compared /Users/me/project/a.ts with /Users/me/other/b.ts');
    });

    it('should handle text with no container paths', () => {
        const text = 'No paths here, just text.';
        const result = translateToolResult(text, containerToHostMap);
        expect(result).toBe(text);
    });

    it('should replace all occurrences of the same container path', () => {
        const text = '/data/0/file.ts imports from /data/0/utils.ts';
        const result = translateToolResult(text, containerToHostMap);
        expect(result).toBe('/Users/me/project/file.ts imports from /Users/me/project/utils.ts');
    });
});

describe('readonly mode', () => {
    let tempDir: string;
    let readonlyClient: FilesystemMcpClient;
    let writableClient: FilesystemMcpClient;

    beforeAll(async () => {
        const rawTempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'mcp-readonly-test-'));
        tempDir = fs.realpathSync(rawTempDir);
        fs.writeFileSync(path.join(tempDir, 'test.txt'), 'hello');

        readonlyClient = new FilesystemMcpClient({allowedDirectories: [tempDir]});
        await readonlyClient.connect();

        writableClient = new FilesystemMcpClient({allowedDirectories: [tempDir], readonly: false});
        await writableClient.connect();
    }, 30000);

    afterAll(async () => {
        await readonlyClient?.disconnect();
        await writableClient?.disconnect();
        if (tempDir && fs.existsSync(tempDir)) {
            fs.rmSync(tempDir, {recursive: true, force: true});
        }
    });

    it('should default to readonly mode', async () => {
        const result = await readonlyClient.callTool('write_file', {
            path: path.join(tempDir, 'blocked.txt'),
            content: 'should not write',
        });
        expect((result as {text: string}).text).toBe('Error: Tool "write_file" is not allowed in readonly mode');
    });

    it('should block all write tools in readonly mode', async () => {
        const writeTools = ['write_file', 'edit_file', 'create_directory', 'move_file', 'delete_file'];
        for (const tool of writeTools) {
            const result = await readonlyClient.callTool(tool, {});
            expect((result as {text: string}).text).toBe(`Error: Tool "${tool}" is not allowed in readonly mode`);
        }
    });

    it('should allow read tools in readonly mode', async () => {
        const result = await readonlyClient.callTool('read_file', {
            path: path.join(tempDir, 'test.txt'),
        });
        expect((result as {text: string}).text).toContain('hello');
    });

    it('should allow write tools when readonly is false', async () => {
        await expect(writableClient.callTool('write_file', {
            path: path.join(tempDir, 'writable.txt'),
            content: 'allowed',
        })).resolves.toBeDefined();
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

    describe('getTools', () => {
        it('should return tools in Claude format', () => {
            const tools = client.getTools();
            expect(Array.isArray(tools)).toBe(true);
            expect(tools.length).toBeGreaterThan(0);

            const readFileTool = tools.find(t => t.name === 'read_file');
            expect(readFileTool).toBeDefined();
            expect(readFileTool?.description).toBeDefined();
            expect(readFileTool?.inputSchema).toBeDefined();
        });

        it('should filter out write tools', () => {
            const tools = client.getTools();
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
        expect(toolProvider.getTools).toBeDefined();
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