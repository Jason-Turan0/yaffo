import {describe, it, expect, beforeEach} from '@jest/globals';
import {
    PlaywrightMcpClient,
    truncateToolResult,
    createStubPlaywrightClient,
    type McpClientLike,
} from '../test_generator/mcp_playwright_client';
import * as fs from 'fs';
import * as path from 'path';

const testDataPath = path.join(process.cwd(), 'lib', '__tests__', 'test_data', 'playwright_tools.json');
const testData = JSON.parse(fs.readFileSync(testDataPath, 'utf-8'));

function createMockClient(overrides: Partial<McpClientLike> = {}): McpClientLike {
    return {
        close: async () => {
        },
        listTools: async () => ({tools: testData.tools}),
        callTool: async ({name}: { name: string }) => {
            const response = testData.mockResponses[name];
            if (!response) {
                throw new Error(`"${name}" not implemented`);
            }
            return response || {content: [{type: 'text', text: ''}]};
        },
        ...overrides,
    };
}

describe('truncateToolResult', () => {
    it('should return the original string if under limit', () => {
        const shortString = 'This is a short string';
        expect(truncateToolResult(shortString)).toBe(shortString);
    });

    it('should truncate strings over 30000 chars', () => {
        const longString = 'x'.repeat(35000);
        const result = truncateToolResult(longString);
        expect(result.length).toBeLessThan(longString.length);
        expect(result).toContain('[TRUNCATED:');
        expect(result).toContain('35000 chars');
    });

    it('should not truncate strings exactly at the limit', () => {
        const exactString = 'x'.repeat(30000);
        expect(truncateToolResult(exactString)).toBe(exactString);
    });
});

describe('PlaywrightMcpClient', () => {
    let mockClient: McpClientLike;
    let client: PlaywrightMcpClient;

    beforeEach(() => {
        mockClient = createMockClient();
        client = new PlaywrightMcpClient(mockClient);
    });

    describe('connect', () => {
        it('should list available tools after connection', async () => {
            await client.connect();

            const toolNames = client.getToolNames();
            expect(toolNames.length).toBeGreaterThan(0);
            expect(toolNames).toContain('browser_navigate');
            expect(toolNames).toContain('browser_snapshot');
            expect(toolNames).toContain('browser_click');
        });
    });

    describe('getToolsForClaude', () => {
        beforeEach(async () => {
            await client.connect();
        });

        it('should return tools in Claude format', () => {
            const tools = client.getToolsForClaude();
            expect(Array.isArray(tools)).toBe(true);
            expect(tools.length).toBeGreaterThan(0);

            const navigateTool = tools.find((t) => t.name === 'browser_navigate');
            expect(navigateTool).toBeDefined();
            expect(navigateTool?.description).toBe('Navigate to a URL');
            expect(navigateTool?.input_schema).toBeDefined();
        });

        it('should filter out excluded tools', () => {
            const tools = client.getToolsForClaude();
            const toolNames = tools.map((t) => t.name);

            expect(toolNames).not.toContain('browser_install');
            expect(toolNames).not.toContain('browser_pdf_save');
        });

        it('should include non-excluded tools', () => {
            const tools = client.getToolsForClaude();
            const toolNames = tools.map((t) => t.name);

            expect(toolNames).toContain('browser_navigate');
            expect(toolNames).toContain('browser_snapshot');
            expect(toolNames).toContain('browser_click');
            expect(toolNames).toContain('browser_type');
            expect(toolNames).toContain('browser_take_screenshot');
            expect(toolNames).toContain('browser_close');
        });
    });

    describe('callTool', () => {
        it('should throw error if not connected', async () => {
            await expect(client.callTool('browser_navigate', {url: '/'})).rejects.toThrow(
                'Playwright MCP client not connected'
            );
        });

        it('should call browser_navigate tool', async () => {
            await client.connect();

            const result = await client.callTool('browser_navigate', {url: 'http://localhost:5000'});

            expect(result).toHaveProperty('type', 'text');
        });

        it('should call browser_snapshot tool and return content', async () => {
            await client.connect();

            const result = await client.callTool('browser_snapshot', {});

            expect(typeof result).toBe('object');
            expect((result as { text: string }).text).toContain('Page Title: Home - Photo Organizer');
        });

        it('should call browser_evaluate tool and return content', async () => {
            await client.connect();

            const result = await client.callTool('browser_evaluate',
                {
                    "function": "() => {\n  const firstImage = document.querySelector('.photo-card img');\n  return {\n    src: firstImage?.src,\n    alt: firstImage?.alt,\n    dataFallback: firstImage?.getAttribute('data-fallback')\n  };\n}"
                }
            );

            expect(typeof result).toBe('object');
            expect((result as { text: string }).text).toContain('const firstImage = document.querySelector');
        });

        it('should verify callTool was invoked with correct arguments', async () => {
            const callToolSpy = {calls: [] as Array<{ name: string; arguments?: Record<string, unknown> }>};
            mockClient = createMockClient({
                callTool: async (params) => {
                    callToolSpy.calls.push(params);
                    return {content: [{type: 'text', text: 'mocked'}]};
                },
            });
            client = new PlaywrightMcpClient(mockClient);
            await client.connect();

            await client.callTool('browser_click', {element: 'Submit button', ref: 'btn1'});

            expect(callToolSpy.calls).toHaveLength(1);
            expect(callToolSpy.calls[0]).toEqual({
                name: 'browser_click',
                arguments: {element: 'Submit button', ref: 'btn1'},
            });
        });
    });

    describe('convenience methods', () => {
        let callToolSpy: { calls: Array<{ name: string; arguments?: Record<string, unknown> }> };

        beforeEach(async () => {
            callToolSpy = {calls: []};
            mockClient = createMockClient({
                callTool: async (params) => {
                    callToolSpy.calls.push(params);
                    return {content: [{type: 'text', text: ''}]};
                },
            });
            client = new PlaywrightMcpClient(mockClient);
            await client.connect();
        });

        it('navigate() should call browser_navigate', async () => {
            await client.navigate('http://example.com');

            expect(callToolSpy.calls).toContainEqual({
                name: 'browser_navigate',
                arguments: {url: 'http://example.com'},
            });
        });

        it('snapshot() should call browser_snapshot', async () => {
            await client.snapshot();

            expect(callToolSpy.calls).toContainEqual({
                name: 'browser_snapshot',
                arguments: {},
            });
        });

        it('click() should call browser_click', async () => {
            await client.click('button-ref');

            expect(callToolSpy.calls).toContainEqual({
                name: 'browser_click',
                arguments: {element: 'button-ref'},
            });
        });

        it('type() should call browser_type', async () => {
            await client.type('input-ref', 'hello world');

            expect(callToolSpy.calls).toContainEqual({
                name: 'browser_type',
                arguments: {element: 'input-ref', text: 'hello world'},
            });
        });

        it('takeScreenshot() should call browser_take_screenshot', async () => {
            await client.takeScreenshot({fullPage: true});

            expect(callToolSpy.calls).toContainEqual({
                name: 'browser_take_screenshot',
                arguments: {fullPage: true},
            });
        });

        it('close() should call browser_close', async () => {
            await client.close();

            expect(callToolSpy.calls).toContainEqual({
                name: 'browser_close',
                arguments: {},
            });
        });
    });

    describe('disconnect', () => {
        it('should call close on the underlying client', async () => {
            let closeCalled = false;
            mockClient = createMockClient({
                close: async () => {
                    closeCalled = true;
                },
            });
            client = new PlaywrightMcpClient(mockClient);
            await client.connect();

            await client.disconnect();

            expect(closeCalled).toBe(true);
        });

        it('should not call close if not connected', async () => {
            let closeCalled = false;
            mockClient = createMockClient({
                close: async () => {
                    closeCalled = true;
                },
            });
            client = new PlaywrightMcpClient(mockClient);

            await client.disconnect();

            expect(closeCalled).toBe(false);
        });
    });
});

describe('createStubPlaywrightClient', () => {
    it('should create a stub client that returns empty tools', async () => {
        const stub = await createStubPlaywrightClient();

        expect(stub.getToolsForClaude()).toEqual([]);
    });

    it('should throw error when calling tools on stub', async () => {
        const stub = await createStubPlaywrightClient();

        await expect(stub.callTool('browser_navigate', {url: '/'})).rejects.toThrow(
            'Playwright MCP client not connected'
        );
    });

    it('should disconnect without error', async () => {
        const stub = await createStubPlaywrightClient();
        await expect(stub.disconnect()).resolves.toBeUndefined();
    });
});