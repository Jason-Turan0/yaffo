import {jest, beforeAll, afterAll} from '@jest/globals';
import {mkdtempSync, rmSync, existsSync} from 'fs';
import {join} from 'path';
import {tmpdir} from 'os';
import {TestGeneratorOrchestrator} from '../test_generator/test_generator_orchestrator';
import {PromptGenerator} from '../test_generator/prompt_generator';
import {Spec} from '../test_generator/spec_parser.types';
import {ToolProvider, CallToolReturn} from '../test_generator/toolprovider.types';
import {TypeScriptValidator, TypeCheckResult} from '../test_generator/typescript_validator';
import {BetaTool} from '@anthropic-ai/sdk/resources/beta';
import {BetaMessage} from '@anthropic-ai/sdk/resources/beta';

type MockFn = ReturnType<typeof jest.fn>;

interface MockAnthropicModelClient {
    addMessage: MockFn;
    addMessages: MockFn;
    callModelApi: MockFn;
}

const createMockAnthropicModelClient = (): MockAnthropicModelClient => ({
    addMessage: jest.fn(),
    addMessages: jest.fn(),
    callModelApi: jest.fn(),
});

const createMockToolProvider = (
    tools: BetaTool[] = [],
    callToolResponse: CallToolReturn = 'mock tool response'
): ToolProvider => ({
    getToolsForClaude: jest.fn(() => tools),
    callTool: jest.fn(async () => callToolResponse),
    disconnect: jest.fn(async () => {}),
});

const createMockPromptGenerator = (): PromptGenerator => ({
    getSystemPrompt: jest.fn(() => 'system prompt'),
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

const createBetaUsage = (inputTokens: number, outputTokens: number) => ({
    input_tokens: inputTokens,
    output_tokens: outputTokens,
    cache_creation: undefined,
    cache_creation_input_tokens: 0,
    cache_read_input_tokens: 0,
    server_tool_use: undefined,
    service_tier: undefined,
});

const createToolUseResponse = (toolId: string, toolName: string, input: Record<string, unknown>): BetaMessage => ({
    id: 'msg_123',
    type: 'message',
    role: 'assistant',
    model: 'claude-sonnet-4-5',
    stop_reason: 'tool_use',
    stop_sequence: null,
    usage: createBetaUsage(100, 50),
    content: [{
        type: 'tool_use',
        id: toolId,
        name: toolName,
        input,
    }],
} as unknown as BetaMessage);

const createEndTurnResponse = (text: string): BetaMessage => ({
    id: 'msg_456',
    type: 'message',
    role: 'assistant',
    model: 'claude-sonnet-4-5',
    stop_reason: 'end_turn',
    stop_sequence: null,
    usage: createBetaUsage(100, 200),
    content: [{
        type: 'text',
        text,
        citations: null,
    }],
} as unknown as BetaMessage);

describe('TestGeneratorOrchestrator', () => {
    let mockAnthropicClient: MockAnthropicModelClient;
    let mockToolProvider: ToolProvider;
    let mockPromptGenerator: PromptGenerator;
    let mockTypeScriptValidator: TypeScriptValidator;
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
        mockAnthropicClient = createMockAnthropicModelClient();
        mockPromptGenerator = createMockPromptGenerator();
        mockTypeScriptValidator = createMockTypeScriptValidator();
    });

    describe('happy path - single tool call then test generation', () => {
        it('should make a tool call then generate a test', async () => {
            const testTool: BetaTool = {
                name: 'read_file',
                description: 'Read a file',
                input_schema: {
                    type: 'object' as const,
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
                mockAnthropicClient as unknown as import('../test_generator/anthropic_model_client').AnthropicModelClient,
                mockPromptGenerator,
                ['/allowed/dir'],
                null,
                [mockToolProvider],
                mockTypeScriptValidator,
            );

            const toolUseResponse = createToolUseResponse(
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
            const endTurnResponse = createEndTurnResponse(generatedTestJson);

            mockAnthropicClient.callModelApi
                .mockResolvedValueOnce(toolUseResponse)
                .mockResolvedValueOnce(endTurnResponse);

            const result = await orchestrator.generate('/path/to/spec.yaml', testBaseUrl);

            expect(mockAnthropicClient.addMessage).toHaveBeenCalledWith({
                role: 'user',
                content: 'user prompt',
            });

            expect(mockAnthropicClient.callModelApi).toHaveBeenCalledTimes(2);

            expect(mockToolProvider.callTool).toHaveBeenCalledWith(
                'read_file',
                {path: '/allowed/dir/template.html'}
            );

            expect(mockAnthropicClient.addMessages).toHaveBeenCalledWith([
                {role: 'assistant', content: toolUseResponse.content},
                {role: 'user', content: [{
                    type: 'tool_result',
                    tool_use_id: 'tool_use_1',
                    content: 'file contents here',
                }]},
            ]);

            expect(mockToolProvider.disconnect).toHaveBeenCalled();

            expect(result.success).toBe(true);
            expect(result.logPath).toBe(testRunLogDir);
        });

        it('should handle tool returning content block instead of string', async () => {
            const testTool: BetaTool = {
                name: 'read_file',
                description: 'Read a file',
                input_schema: {
                    type: 'object' as const,
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
                mockAnthropicClient as unknown as import('../test_generator/anthropic_model_client').AnthropicModelClient,
                mockPromptGenerator,
                ['/allowed/dir'],
                null,
                [mockToolProvider],
                mockTypeScriptValidator,
            );

            const toolUseResponse = createToolUseResponse(
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
            const endTurnResponse = createEndTurnResponse(generatedTestJson);

            mockAnthropicClient.callModelApi
                .mockResolvedValueOnce(toolUseResponse)
                .mockResolvedValueOnce(endTurnResponse);

            const result = await orchestrator.generate('/path/to/spec.yaml', testBaseUrl);

            expect(mockAnthropicClient.addMessages).toHaveBeenCalledWith([
                {role: 'assistant', content: toolUseResponse.content},
                {role: 'user', content: [{
                    type: 'tool_result',
                    tool_use_id: 'tool_use_2',
                    content: [contentBlockResponse],
                }]},
            ]);

            expect(result.success).toBe(true);
        });
    });

    describe('tool error handling', () => {
        it('should handle tool execution errors gracefully', async () => {
            const testTool: BetaTool = {
                name: 'failing_tool',
                description: 'A tool that fails',
                input_schema: {
                    type: 'object' as const,
                    properties: {},
                },
            };

            const failingToolProvider: ToolProvider = {
                getToolsForClaude: jest.fn(() => [testTool]),
                callTool: jest.fn(async () => {
                    throw new Error('Tool execution failed');
                }),
                disconnect: jest.fn(async () => {}),
            };

            const spec = createMinimalSpec();

            orchestrator = new TestGeneratorOrchestrator(
                spec,
                testRunLogDir,
                testOutputDir,
                testBaseUrl,
                mockAnthropicClient as unknown as import('../test_generator/anthropic_model_client').AnthropicModelClient,
                mockPromptGenerator,
                ['/allowed/dir'],
                null,
                [failingToolProvider],
                mockTypeScriptValidator,
            );

            const toolUseResponse = createToolUseResponse(
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
            const endTurnResponse = createEndTurnResponse(generatedTestJson);

            mockAnthropicClient.callModelApi
                .mockResolvedValueOnce(toolUseResponse)
                .mockResolvedValueOnce(endTurnResponse);

            const result = await orchestrator.generate('/path/to/spec.yaml', testBaseUrl);

            expect(mockAnthropicClient.addMessages).toHaveBeenCalledWith([
                {role: 'assistant', content: toolUseResponse.content},
                {role: 'user', content: [{
                    type: 'tool_result',
                    tool_use_id: 'tool_use_3',
                    content: 'Error: Tool execution failed',
                    is_error: true,
                }]},
            ]);

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
                mockAnthropicClient as unknown as import('../test_generator/anthropic_model_client').AnthropicModelClient,
                mockPromptGenerator,
                ['/allowed/dir'],
                null,
                [mockToolProvider],
                mockTypeScriptValidator,
            );

            const toolUseResponse = createToolUseResponse(
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
            const endTurnResponse = createEndTurnResponse(generatedTestJson);

            mockAnthropicClient.callModelApi
                .mockResolvedValueOnce(toolUseResponse)
                .mockResolvedValueOnce(endTurnResponse);

            const result = await orchestrator.generate('/path/to/spec.yaml', testBaseUrl);

            expect(mockAnthropicClient.addMessages).toHaveBeenCalledWith([
                {role: 'assistant', content: toolUseResponse.content},
                {role: 'user', content: [{
                    type: 'tool_result',
                    tool_use_id: 'tool_use_4',
                    content: 'Error: No implementation for tool unknown_tool',
                    is_error: true,
                }]},
            ]);

            expect(result.success).toBe(true);
        });
    });

    describe('duplicate tool names', () => {
        it('should throw error when multiple providers have the same tool name', () => {
            const duplicateTool: BetaTool = {
                name: 'duplicate_tool',
                description: 'A duplicate tool',
                input_schema: {type: 'object' as const, properties: {}},
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
                    mockAnthropicClient as unknown as import('../test_generator/anthropic_model_client').AnthropicModelClient,
                    mockPromptGenerator,
                    ['/allowed/dir'],
                    null,
                    [provider1, provider2],
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
                mockAnthropicClient as unknown as import('../test_generator/anthropic_model_client').AnthropicModelClient,
                mockPromptGenerator,
                ['/allowed/dir'],
                null,
                [mockToolProvider],
                mockTypeScriptValidator,
            );

            mockAnthropicClient.callModelApi.mockResolvedValueOnce(undefined);

            const result = await orchestrator.generate('/path/to/spec.yaml', testBaseUrl);

            expect(result.success).toBe(false);
            expect(result.error).toBe('Code Generation failed.');
            expect(mockToolProvider.disconnect).toHaveBeenCalled();
        });
    });
});