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
});