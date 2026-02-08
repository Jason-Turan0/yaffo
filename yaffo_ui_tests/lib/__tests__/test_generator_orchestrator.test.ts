import {jest, beforeAll, afterAll, beforeEach, describe, it, expect} from '@jest/globals';
import {mkdtempSync, rmSync, existsSync} from 'fs';
import {join} from 'path';
import {tmpdir} from 'os';
import {TestGeneratorOrchestrator} from '../test_generator/test_generator_orchestrator';
import {PromptGenerator} from '../test_generator/prompt_generator';
import {Spec} from '../test_generator/spec_parser.types';
import {ToolProvider, CallToolReturn, RawToolDefinition} from '../test_generator/toolprovider.types';
import {TypeScriptValidator, TypeCheckResult} from '../test_generator/typescript_validator';
import {ModelClient, ModelResponse, ModelAlias} from '../model_clients/model_client.interface';
import {AutoHealTestOrchestratorFactory} from '../test_generator/auto_heal_orchestrator';

type MockFn = ReturnType<typeof jest.fn>;

interface MockModelClient extends ModelClient {
    addUserMessage: MockFn;
    addToolResultMessage: MockFn;
    callModelApi: MockFn;
    model: ModelAlias;
}

const createMockModelClient = (): MockModelClient => ({
    addUserMessage: jest.fn(),
    addToolResultMessage: jest.fn(),
    model: "claude-sonnet-4-5",
    callModelApi: jest.fn()
});

const createMockToolProvider = (
    tools: RawToolDefinition[] = [],
    callToolResponse: CallToolReturn = 'mock tool response'
): ToolProvider => ({
    getTools: jest.fn(() => tools),
    callTool: jest.fn(async () => callToolResponse),
    disconnect: jest.fn(async () => {
    }),
});

const createMockPromptGenerator = (): PromptGenerator => ({
    getSystemPrompt: jest.fn(() => Promise.resolve('system prompt')),
    buildUserPrompt: jest.fn(() => 'user prompt'),
    buildSchemaFixPrompt: jest.fn(() => 'schema fix prompt'),
    buildTypeErrorFixPrompt: jest.fn(() => 'type error fix prompt'),
    buildTestFailurePrompt: jest.fn(() => 'test failure prompt'),
}) as unknown as PromptGenerator;

const createMockTypeScriptValidator = (): TypeScriptValidator => ({
    typeCheckFile: jest.fn((): TypeCheckResult => ({
        success: true,
        errors: [],
        errorCount: 0,
    })),
    formatTypeErrorsForModel: jest.fn(() => ''),
});

const createMockAutoHealFactory = (): AutoHealTestOrchestratorFactory => {
    return jest.fn() as unknown as AutoHealTestOrchestratorFactory;
};

const createMinimalSpec = (): Spec => ({
    feature: 'test-feature',
    description: 'Test feature description',
    scenarios: [{
        name: 'Test scenario',
        goal: 'Test goal',
        priority: 'medium',
        steps: ['Step 1'],
        verify: ['Verify 1'],
    }],
});

const VALID_PLAYWRIGHT_TEST_CODE = `import { test, expect } from '@playwright/test';

test.describe('Test Feature', () => {
    test('should work', async ({ page }) => {
        await page.goto('/');
        await expect(page.locator('h1')).toBeVisible();
    });
});
`;

const createToolCallsResponse = (toolId: string, toolName: string, input: Record<string, unknown>): ModelResponse => ({
    text: '',
    finishReason: 'tool-calls',
    toolCalls: [{
        toolCallId: toolId,
        toolName,
        input,
        type: 'tool-call'
    }],
    usage: {
        inputTokens: 100,
        outputTokens: 50,
        inputTokenDetails: {cacheReadTokens: 0, cacheWriteTokens: 0, noCacheTokens: 100},
        outputTokenDetails: {reasoningTokens: 0, textTokens: 50},
        totalTokens: 150,
    },
    responseMessages: [{
        role: 'assistant',
        content: `Tool call: ${toolName}`,
    }],
});

const createStopResponse = (text: string): ModelResponse => ({
    text,
    finishReason: 'stop',
    toolCalls: [],
    usage: {
        inputTokens: 100,
        outputTokens: 200,
        inputTokenDetails: {cacheReadTokens: 0, cacheWriteTokens: 0, noCacheTokens: 100},
        outputTokenDetails: {reasoningTokens: 0, textTokens: 50},
        totalTokens: 300,
    },
    responseMessages: [{
        role: 'assistant',
        content: text,
    }],
});

describe('TestGeneratorOrchestrator', () => {
    let mockModelClient: MockModelClient;
    let mockToolProvider: ToolProvider;
    let mockPromptGenerator: PromptGenerator;
    let mockTypeScriptValidator: TypeScriptValidator;
    let mockAutoHealFactory: AutoHealTestOrchestratorFactory;
    let orchestrator: TestGeneratorOrchestrator;
    let testOutputDir: string;
    let testRunLogDir: string;
    const testBaseUrl = 'http://localhost:5000';

    beforeAll(() => {
        testOutputDir = mkdtempSync(join(tmpdir(), 'orchestrator-test-output-'));
        testRunLogDir = mkdtempSync(join(tmpdir(), 'orchestrator-test-logs-'));
    });

    afterAll(() => {
        if (existsSync(testOutputDir)) {
            rmSync(testOutputDir, {recursive: true, force: true});
        }
        if (existsSync(testRunLogDir)) {
            rmSync(testRunLogDir, {recursive: true, force: true});
        }
    });

    beforeEach(() => {
        mockModelClient = createMockModelClient();
        mockPromptGenerator = createMockPromptGenerator();
        mockTypeScriptValidator = createMockTypeScriptValidator();
        mockAutoHealFactory = createMockAutoHealFactory();
    });

    describe('happy path - single tool call then test generation', () => {
        it('should make a tool call then generate a test', async () => {
            const testTool: RawToolDefinition = {
                name: 'read_file',
                description: 'Read a file',
                inputSchema: {
                    type: 'object',
                    properties: {path: {type: 'string'}},
                    required: ['path'],
                },
            };

            mockToolProvider = createMockToolProvider([testTool], 'file contents here');
            const spec = createMinimalSpec();

            orchestrator = new TestGeneratorOrchestrator(
                spec,
                testRunLogDir,
                testOutputDir,
                testBaseUrl,
                mockModelClient,
                mockPromptGenerator,
                ['/allowed/dir'],
                null,
                [mockToolProvider],
                mockTypeScriptValidator,
                mockAutoHealFactory,
            );

            const toolCallsResponse = createToolCallsResponse(
                'tool_use_1',
                'read_file',
                {path: '/allowed/dir/template.html'}
            );

            const generatedTestJson = JSON.stringify({
                files: [{
                    filename: 'test-feature.spec.ts',
                    code: VALID_PLAYWRIGHT_TEST_CODE,
                    description: 'Test file',
                }],
                confidence: 0.9,
            });
            const stopResponse = createStopResponse(generatedTestJson);

            mockModelClient.callModelApi
                .mockResolvedValueOnce(toolCallsResponse)
                .mockResolvedValueOnce(stopResponse);

            const result = await orchestrator.generate('/path/to/spec.yaml', testBaseUrl);

            expect(mockModelClient.addUserMessage).toHaveBeenCalledWith([{
                type: 'text',
                text: 'user prompt',
            }]);

            expect(mockModelClient.callModelApi).toHaveBeenCalledTimes(2);

            expect(mockToolProvider.callTool).toHaveBeenCalledWith(
                'read_file',
                {path: '/allowed/dir/template.html'}
            );

            expect(mockToolProvider.disconnect).toHaveBeenCalled();

            expect(result.success).toBe(true);
            expect(result.logPath).toBe(testRunLogDir);
        });

        it('should handle tool returning content block instead of string', async () => {
            const testTool: RawToolDefinition = {
                name: 'read_file',
                description: 'Read a file',
                inputSchema: {
                    type: 'object',
                    properties: {path: {type: 'string'}},
                    required: ['path'],
                },
            };

            const contentBlockResponse = {
                type: 'text' as const,
                text: 'content block response',
            };
            mockToolProvider = createMockToolProvider([testTool], contentBlockResponse);
            const spec = createMinimalSpec();

            orchestrator = new TestGeneratorOrchestrator(
                spec,
                testRunLogDir,
                testOutputDir,
                testBaseUrl,
                mockModelClient,
                mockPromptGenerator,
                ['/allowed/dir'],
                null,
                [mockToolProvider],
                mockTypeScriptValidator,
                mockAutoHealFactory,
            );

            const toolCallsResponse = createToolCallsResponse(
                'tool_use_2',
                'read_file',
                {path: '/allowed/dir/file.html'}
            );

            const generatedTestJson = JSON.stringify({
                files: [{
                    filename: 'test-feature.spec.ts',
                    code: VALID_PLAYWRIGHT_TEST_CODE,
                    description: 'Test file',
                }],
                confidence: 0.85,
            });
            const stopResponse = createStopResponse(generatedTestJson);

            mockModelClient.callModelApi
                .mockResolvedValueOnce(toolCallsResponse)
                .mockResolvedValueOnce(stopResponse);

            const result = await orchestrator.generate('/path/to/spec.yaml', testBaseUrl);

            expect(result.success).toBe(true);
        });
    });

    describe('tool error handling', () => {
        it('should handle tool execution errors gracefully', async () => {
            const testTool: RawToolDefinition = {
                name: 'failing_tool',
                description: 'A tool that fails',
                inputSchema: {
                    type: 'object',
                    properties: {},
                },
            };

            const failingToolProvider: ToolProvider = {
                getTools: jest.fn(() => [testTool]),
                callTool: jest.fn(async () => {
                    throw new Error('Tool execution failed');
                }),
                disconnect: jest.fn(async () => {
                }),
            };

            const spec = createMinimalSpec();

            orchestrator = new TestGeneratorOrchestrator(
                spec,
                testRunLogDir,
                testOutputDir,
                testBaseUrl,
                mockModelClient,
                mockPromptGenerator,
                ['/allowed/dir'],
                null,
                [failingToolProvider],
                mockTypeScriptValidator,
                mockAutoHealFactory,
            );

            const toolCallsResponse = createToolCallsResponse(
                'tool_use_3',
                'failing_tool',
                {}
            );

            const generatedTestJson = JSON.stringify({
                files: [{
                    filename: 'test-feature.spec.ts',
                    code: VALID_PLAYWRIGHT_TEST_CODE,
                    description: 'Test file',
                }],
                confidence: 0.7,
            });
            const stopResponse = createStopResponse(generatedTestJson);

            mockModelClient.callModelApi
                .mockResolvedValueOnce(toolCallsResponse)
                .mockResolvedValueOnce(stopResponse);

            const result = await orchestrator.generate('/path/to/spec.yaml', testBaseUrl);

            expect(result.success).toBe(true);
        });

        it('should handle unknown tool names', async () => {
            mockToolProvider = createMockToolProvider([], 'response');
            const spec = createMinimalSpec();

            orchestrator = new TestGeneratorOrchestrator(
                spec,
                testRunLogDir,
                testOutputDir,
                testBaseUrl,
                mockModelClient,
                mockPromptGenerator,
                ['/allowed/dir'],
                null,
                [mockToolProvider],
                mockTypeScriptValidator,
                mockAutoHealFactory,
            );

            const toolCallsResponse = createToolCallsResponse(
                'tool_use_4',
                'unknown_tool',
                {}
            );

            const generatedTestJson = JSON.stringify({
                files: [{
                    filename: 'test-feature.spec.ts',
                    code: VALID_PLAYWRIGHT_TEST_CODE,
                    description: 'Test file',
                }],
                confidence: 0.8,
            });
            const stopResponse = createStopResponse(generatedTestJson);

            mockModelClient.callModelApi
                .mockResolvedValueOnce(toolCallsResponse)
                .mockResolvedValueOnce(stopResponse);

            const result = await orchestrator.generate('/path/to/spec.yaml', testBaseUrl);

            expect(result.success).toBe(true);
        });
    });

    describe('duplicate tool names', () => {
        it('should throw error when multiple providers have the same tool name', () => {
            const duplicateTool: RawToolDefinition = {
                name: 'duplicate_tool',
                description: 'A duplicate tool',
                inputSchema: {type: 'object', properties: {}},
            };

            const provider1 = createMockToolProvider([duplicateTool]);
            const provider2 = createMockToolProvider([duplicateTool]);
            const spec = createMinimalSpec();

            expect(() => {
                new TestGeneratorOrchestrator(
                    spec,
                    testRunLogDir,
                    testOutputDir,
                    testBaseUrl,
                    mockModelClient,
                    mockPromptGenerator,
                    ['/allowed/dir'],
                    null,
                    [provider1, provider2],
                    mockTypeScriptValidator,
                    mockAutoHealFactory,
                );
            }).toThrow('Duplicate tool names duplicate_tool');
        });
    });

    describe('API failure handling', () => {
        it('should return failure when model API returns undefined', async () => {
            mockToolProvider = createMockToolProvider([]);
            const spec = createMinimalSpec();

            orchestrator = new TestGeneratorOrchestrator(
                spec,
                testRunLogDir,
                testOutputDir,
                testBaseUrl,
                mockModelClient,
                mockPromptGenerator,
                ['/allowed/dir'],
                null,
                [mockToolProvider],
                mockTypeScriptValidator,
                mockAutoHealFactory,
            );

            mockModelClient.callModelApi.mockResolvedValueOnce(undefined);

            const result = await orchestrator.generate('/path/to/spec.yaml', testBaseUrl);

            expect(result.success).toBe(false);
            expect(result.error).toBe('Code Generation failed.');
            expect(mockToolProvider.disconnect).toHaveBeenCalled();
        });
    });
});
