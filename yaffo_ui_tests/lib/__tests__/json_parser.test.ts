import {parseJsonResponse} from '../test_generator/json_parser';
import {join} from "path";
import fs from "fs";

describe('parseJsonResponse', () => {
    it('should parse clean JSON directly', () => {
        const cleanJson = JSON.stringify({
            files: [{filename: 'test.spec.ts', code: 'test code', description: 'desc'}],
            confidence: 0.9
        });

        const {response, schemaErrors} = parseJsonResponse(cleanJson);
        expect(response).not.toBeNull();
        expect(schemaErrors).toHaveLength(0);
        expect(response?.files).toHaveLength(1);
        expect(response?.files[0].filename).toBe('test.spec.ts');
    });

    it('should extract JSON when preceded by explanation text', () => {
        const textWithExplanation = `Perfect! Now I have enough information.

Based on my analysis:
1. The home page is at root
2. Gallery uses .photo-grid

{"files": [{"filename": "test.spec.ts", "code": "const x = 1;", "description": "test"}], "confidence": 0.9}`;

        const {response, schemaErrors} = parseJsonResponse(textWithExplanation);
        expect(response).not.toBeNull();
        expect(schemaErrors).toHaveLength(0);
        expect(response?.files).toHaveLength(1);
        expect(response?.files[0].filename).toBe('test.spec.ts');
    });

    it('should handle JSON with embedded escaped strings containing braces', () => {
        const jsonWithEscapedCode = `Some explanation first.

{"files": [{"filename": "test.spec.ts", "code": "function test() {\\n  return { value: 1 };\\n}", "description": "test"}], "confidence": 0.9}`;

        const {response, schemaErrors} = parseJsonResponse(jsonWithEscapedCode);
        expect(response).not.toBeNull();
        expect(schemaErrors).toHaveLength(0);
        expect(response?.files).toHaveLength(1);
        expect(response?.files[0].code).toContain('return { value: 1 }');
    });

    it('should handle real-world Claude response with explanation before JSON - Case 1', () => {
        const realResponse = JSON.parse(fs.readFileSync(join(process.cwd(), 'lib', '__tests__', 'test_data', 'example_response.json'), 'utf-8'))?.content[0].text;

        const {response, schemaErrors} = parseJsonResponse(realResponse);

        // This response has confidence on the file object (schema error) - response is still returned for fixing
        expect(response).not.toBeNull();
        expect(schemaErrors.length).toBeGreaterThan(0);
        expect(schemaErrors.some(e => e.includes('confidence'))).toBe(true);

        // Parsing still extracts the data correctly despite schema issues
        expect(response?.files).toHaveLength(1);
        expect(response?.files[0].filename).toBe('photo-gallery.spec.ts');
        expect(response?.files[0].code).toContain("import { test, expect } from '@playwright/test'");
        expect(response?.testContext).toBe('Tests verify photo gallery loads correctly, all images return HTTP 200, no broken links exist, and fallback mechanisms work properly. The tests use actual selectors found in the codebase: .photo-grid, .photo-card, .page-header, .subtitle, .empty-state');
        expect(response?.explanation).toContain('Generated comprehensive tests based on the actual codebase structure. The tests check for gallery visibility, image loading success, HTTP status verification, fallback handling, and empty states. Used real CSS selectors and URL patterns discovered from exploring the Flask templates and routes.');
    });

    it('should handle real-world Claude response with explanation before JSON - Case 2', () => {
        const realResponse = JSON.parse(fs.readFileSync(join(process.cwd(), 'lib', '__tests__', 'test_data', 'example_response2.json'), 'utf-8'))?.content[0].text;

        const {response, schemaErrors} = parseJsonResponse(realResponse);

        expect(response).not.toBeNull();
        expect(schemaErrors).toHaveLength(0);
    });

    it('should handle markdown code blocks around JSON', () => {
        const jsonWithCodeBlocks = "```json\n" +
            '{"files": [{"filename": "test.spec.ts", "code": "test", "description": "desc"}], "confidence": 0.9}' +
            "\n```";

        const {response, schemaErrors} = parseJsonResponse(jsonWithCodeBlocks);
        expect(response).not.toBeNull();
        expect(schemaErrors).toHaveLength(0);
        expect(response?.files[0].filename).toBe('test.spec.ts');
    });

    it('should return null response for text with no JSON', () => {
        const noJson = "This is just plain text with no JSON at all.";
        const {response, schemaErrors} = parseJsonResponse(noJson);
        expect(response).toBeNull();
        expect(schemaErrors.length).toBeGreaterThan(0);
    });

    it('should return null response for malformed JSON', () => {
        const malformedJson = '{"files": [{"broken json';
        const {response, schemaErrors} = parseJsonResponse(malformedJson);
        expect(response).toBeNull();
        expect(schemaErrors.length).toBeGreaterThan(0);
    });

    it('should handle complex nested code with various escaped characters', () => {
        const complexCode = `Explanation here

{"files": [{"filename": "complex.spec.ts", "code": "test.describe('Test', () => {\\n  test('should work', async ({ page }) => {\\n    const obj = { key: \\"value\\" };\\n    expect(obj).toEqual({ key: \\"value\\" });\\n  });\\n});", "description": "Complex test"}], "confidence": 0.85}`;

        const {response, schemaErrors} = parseJsonResponse(complexCode);
        expect(response).not.toBeNull();
        expect(schemaErrors).toHaveLength(0);
        expect(response?.files[0].code).toContain("test.describe('Test'");
        expect(response?.confidence).toBe(0.85);
    });

    it('should return schema errors when explanation is on file object instead of root', () => {
        const badSchema = JSON.stringify({
            files: [{
                filename: 'test.spec.ts',
                code: 'test code',
                description: 'desc',
                explanation: 'This should be at root level'
            }],
            confidence: 0.9
        });

        const {response, schemaErrors} = parseJsonResponse(badSchema);
        expect(response).not.toBeNull();
        expect(schemaErrors.length).toBeGreaterThan(0);
        expect(schemaErrors.some(e => e.includes('explanation'))).toBe(true);
    });

    it('should return schema errors for missing required fields', () => {
        const missingFields = JSON.stringify({
            files: [{filename: 'test.spec.ts'}]
        });

        const {response, schemaErrors} = parseJsonResponse(missingFields);
        expect(response).not.toBeNull();
        expect(schemaErrors.length).toBeGreaterThan(0);
        expect(schemaErrors.some(e => e.includes('code'))).toBe(true);
    });
});