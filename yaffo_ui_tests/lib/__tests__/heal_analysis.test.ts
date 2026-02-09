import {describe, it, expect} from '@jest/globals';
import {parseHealAnalysisResponse} from '../test_generator/heal_analysis';

describe('parseHealAnalysisResponse', () => {
    it('should parse valid test_code_defect response', () => {
        const json = JSON.stringify({
            classification: 'test_code_defect',
            reasoning: 'The selector #submit-btn does not exist in the DOM',
            affectedTests: ['should submit form'],
            suggestedAction: 'Update selector to match current DOM structure',
        });

        const {response, schemaErrors} = parseHealAnalysisResponse(json);
        expect(schemaErrors).toHaveLength(0);
        expect(response).not.toBeNull();
        expect(response!.classification).toBe('test_code_defect');
        expect(response!.affectedTests).toEqual(['should submit form']);
    });

    it('should parse valid application_regression response', () => {
        const json = JSON.stringify({
            classification: 'application_regression',
            reasoning: 'The login endpoint returns 500 instead of 200',
            affectedTests: ['should login', 'should show dashboard'],
            suggestedAction: 'Investigate server-side error in login handler',
        });

        const {response, schemaErrors} = parseHealAnalysisResponse(json);
        expect(schemaErrors).toHaveLength(0);
        expect(response!.classification).toBe('application_regression');
    });

    it('should parse valid environment_instability response', () => {
        const json = JSON.stringify({
            classification: 'environment_instability',
            reasoning: 'Network timeout after 30s, likely transient issue',
            affectedTests: ['should load page'],
            suggestedAction: 'Retry the test run',
        });

        const {response, schemaErrors} = parseHealAnalysisResponse(json);
        expect(schemaErrors).toHaveLength(0);
        expect(response!.classification).toBe('environment_instability');
    });

    it('should extract JSON from markdown code blocks', () => {
        const fenced = '```json\n' + JSON.stringify({
            classification: 'test_code_defect',
            reasoning: 'Wrong selector',
            affectedTests: ['test1'],
            suggestedAction: 'Fix selector',
        }) + '\n```';

        const {response, schemaErrors} = parseHealAnalysisResponse(fenced);
        expect(schemaErrors).toHaveLength(0);
        expect(response).not.toBeNull();
    });

    it('should return errors for invalid classification', () => {
        const json = JSON.stringify({
            classification: 'invalid_type',
            reasoning: 'test',
            affectedTests: [],
            suggestedAction: 'test',
        });

        const {response, schemaErrors} = parseHealAnalysisResponse(json);
        expect(response).toBeNull();
        expect(schemaErrors.length).toBeGreaterThan(0);
    });

    it('should return errors for missing fields', () => {
        const json = JSON.stringify({
            classification: 'test_code_defect',
        });

        const {response, schemaErrors} = parseHealAnalysisResponse(json);
        expect(response).toBeNull();
        expect(schemaErrors.length).toBeGreaterThan(0);
    });

    it('should return errors for non-JSON text', () => {
        const {response, schemaErrors} = parseHealAnalysisResponse('not json at all');
        expect(response).toBeNull();
        expect(schemaErrors).toContain('No valid JSON found in heal analysis response');
    });
});
