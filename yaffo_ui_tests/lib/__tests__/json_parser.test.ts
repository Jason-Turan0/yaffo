import {parseJsonResponse} from '../test_generator/json_parser';
import {join} from "path";
import fs from "fs";

describe('parseJsonResponse', () => {
    it('should parse clean JSON directly', () => {
        const cleanJson = JSON.stringify({
            files: [{filename: 'test.spec.ts', code: 'test code', description: 'desc'}],
            confidence: 0.9
        });

        const result = parseJsonResponse(cleanJson);
        expect(result).not.toBeNull();
        expect(result?.files).toHaveLength(1);
        expect(result?.files[0].filename).toBe('test.spec.ts');
    });

    it('should extract JSON when preceded by explanation text', () => {
        const textWithExplanation = `Perfect! Now I have enough information.

Based on my analysis:
1. The home page is at root
2. Gallery uses .photo-grid

{"files": [{"filename": "test.spec.ts", "code": "const x = 1;", "description": "test"}], "confidence": 0.9}`;

        const result = parseJsonResponse(textWithExplanation);
        expect(result).not.toBeNull();
        expect(result?.files).toHaveLength(1);
        expect(result?.files[0].filename).toBe('test.spec.ts');
    });

    it('should handle JSON with embedded escaped strings containing braces', () => {
        const jsonWithEscapedCode = `Some explanation first.

{"files": [{"filename": "test.spec.ts", "code": "function test() {\\n  return { value: 1 };\\n}", "description": "test"}], "confidence": 0.9}`;

        const result = parseJsonResponse(jsonWithEscapedCode);
        expect(result).not.toBeNull();
        expect(result?.files).toHaveLength(1);
        expect(result?.files[0].code).toContain('return { value: 1 }');
    });

    it('should handle real-world Claude response with explanation before JSON - Case 1', () => {
        const realResponse = JSON.parse(fs.readFileSync(join(process.cwd(), 'lib', '__tests__', 'test_data', 'example_response.json'), 'utf-8'))?.content[0].text;

        const result = parseJsonResponse(realResponse);

        expect(result).not.toBeNull();
        expect(result?.files).toHaveLength(1);
        expect(result?.files[0].filename).toBe('photo-gallery.spec.ts');
        expect(result?.files[0].code).toContain("import { test, expect } from '@playwright/test'");
        expect(result?.testContext).toBe('Tests verify photo gallery loads correctly, all images return HTTP 200, no broken links exist, and fallback mechanisms work properly. The tests use actual selectors found in the codebase: .photo-grid, .photo-card, .page-header, .subtitle, .empty-state');
        expect(result?.explanation).toContain('Generated comprehensive tests based on the actual codebase structure. The tests check for gallery visibility, image loading success, HTTP status verification, fallback handling, and empty states. Used real CSS selectors and URL patterns discovered from exploring the Flask templates and routes.');
    });

    it('should handle real-world Claude response with explanation before JSON - Case 2', () => {
        const realResponse = JSON.parse(fs.readFileSync(join(process.cwd(), 'lib', '__tests__', 'test_data', 'example_response2.json'), 'utf-8'))?.content[0].text;

        const result = parseJsonResponse(realResponse);

        expect(result).not.toBeNull();
    });

    it('should handle markdown code blocks around JSON', () => {
        const jsonWithCodeBlocks = "```json\n" +
            '{"files": [{"filename": "test.spec.ts", "code": "test", "description": "desc"}], "confidence": 0.9}' +
            "\n```";

        const result = parseJsonResponse(jsonWithCodeBlocks);
        expect(result).not.toBeNull();
        expect(result?.files[0].filename).toBe('test.spec.ts');
    });

    it('should return null for text with no JSON', () => {
        const noJson = "This is just plain text with no JSON at all.";
        const result = parseJsonResponse(noJson);
        expect(result).toBeNull();
    });

    it('should return null for malformed JSON', () => {
        const malformedJson = '{"files": [{"broken json';
        const result = parseJsonResponse(malformedJson);
        expect(result).toBeNull();
    });

    it('should handle complex nested code with various escaped characters', () => {
        const complexCode = `Explanation here

{"files": [{"filename": "complex.spec.ts", "code": "test.describe('Test', () => {\\n  test('should work', async ({ page }) => {\\n    const obj = { key: \\"value\\" };\\n    expect(obj).toEqual({ key: \\"value\\" });\\n  });\\n});", "description": "Complex test"}], "confidence": 0.85}`;

        const result = parseJsonResponse(complexCode);
        expect(result).not.toBeNull();
        expect(result?.files[0].code).toContain("test.describe('Test'");
        expect(result?.confidence).toBe(0.85);
    });
});