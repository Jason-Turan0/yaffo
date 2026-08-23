import {beforeAll, afterAll, describe, it, expect} from '@jest/globals';
import {mkdtempSync, rmSync, existsSync, readFileSync, writeFileSync} from 'fs';
import {join} from 'path';
import {tmpdir} from 'os';
import {
    loadTestResultHistory,
    recordTestResult,
    formatHistoryForPrompt,
    TestRunRecord,
} from '../test_generator/test_result_history';
import {TestRunResult} from '../services/isolated_runner';

describe('test_result_history', () => {
    let testDir: string;

    beforeAll(() => {
        testDir = mkdtempSync(join(tmpdir(), 'history-test-'));
    });

    afterAll(() => {
        if (existsSync(testDir)) {
            rmSync(testDir, {recursive: true, force: true});
        }
    });

    const createTestRunResult = (success: boolean, failed: number = 0): TestRunResult => ({
        success,
        exitCode: success ? 0 : 1,
        output: '',
        summary: {total: 3, passed: 3 - failed, failed, skipped: 0},
        tests: failed > 0
            ? [{
                file: 'test.spec.ts',
                testName: 'should work',
                status: 'failed',
                duration: 1000,
                error: {message: 'Timeout waiting for selector'},
            }]
            : [{
                file: 'test.spec.ts',
                testName: 'should work',
                status: 'passed',
                duration: 500,
            }],
    });

    describe('loadTestResultHistory', () => {
        it('should return empty array when no history file exists', () => {
            const result = loadTestResultHistory(testDir, 'nonexistent');
            expect(result).toEqual([]);
        });

        it('should return empty array for invalid JSON', () => {
            const historyPath = join(testDir, 'invalid.history.json');
            writeFileSync(historyPath, 'not json');
            const result = loadTestResultHistory(testDir, 'invalid');
            expect(result).toEqual([]);
        });

        it('should parse valid history file', () => {
            const history = {
                testRunHistory: [{
                    timestamp: '2026-01-01T00:00:00.000Z',
                    success: true,
                    summary: {total: 1, passed: 1, failed: 0, skipped: 0},
                    failedTests: [],
                }],
            };
            const historyPath = join(testDir, 'valid.history.json');
            writeFileSync(historyPath, JSON.stringify(history));
            const result = loadTestResultHistory(testDir, 'valid');
            expect(result).toHaveLength(1);
            expect(result[0].success).toBe(true);
        });
    });

    describe('recordTestResult', () => {
        it('should create history file when none exists', () => {
            const featureName = 'new-feature';
            recordTestResult(testDir, featureName, createTestRunResult(true));

            const historyPath = join(testDir, `${featureName}.history.json`);
            expect(existsSync(historyPath)).toBe(true);

            const content = JSON.parse(readFileSync(historyPath, 'utf-8'));
            expect(content.testRunHistory).toHaveLength(1);
            expect(content.testRunHistory[0].success).toBe(true);
        });

        it('should append to existing history', () => {
            const featureName = 'append-feature';
            recordTestResult(testDir, featureName, createTestRunResult(true));
            recordTestResult(testDir, featureName, createTestRunResult(false, 1));

            const historyPath = join(testDir, `${featureName}.history.json`);
            const content = JSON.parse(readFileSync(historyPath, 'utf-8'));
            expect(content.testRunHistory).toHaveLength(2);
            expect(content.testRunHistory[0].success).toBe(true);
            expect(content.testRunHistory[1].success).toBe(false);
        });

        it('should keep only last 5 entries', () => {
            const featureName = 'trim-feature';
            for (let i = 0; i < 7; i++) {
                recordTestResult(testDir, featureName, createTestRunResult(i % 2 === 0));
            }

            const historyPath = join(testDir, `${featureName}.history.json`);
            const content = JSON.parse(readFileSync(historyPath, 'utf-8'));
            expect(content.testRunHistory).toHaveLength(5);
        });

        it('should record failed test details', () => {
            const featureName = 'failed-details';
            recordTestResult(testDir, featureName, createTestRunResult(false, 1));

            const historyPath = join(testDir, `${featureName}.history.json`);
            const content = JSON.parse(readFileSync(historyPath, 'utf-8'));
            expect(content.testRunHistory[0].failedTests).toHaveLength(1);
            expect(content.testRunHistory[0].failedTests[0].testName).toBe('should work');
            expect(content.testRunHistory[0].failedTests[0].error).toBe('Timeout waiting for selector');
        });
    });

    describe('formatHistoryForPrompt', () => {
        it('should return note when history is empty', () => {
            const result = formatHistoryForPrompt([]);
            expect(result).toContain('No previous test runs recorded');
        });

        it('should format history as XML', () => {
            const history: TestRunRecord[] = [{
                timestamp: '2026-01-01T00:00:00.000Z',
                success: false,
                summary: {total: 3, passed: 2, failed: 1, skipped: 0},
                failedTests: [{testName: 'should_work', error: 'Timeout'}],
            }];

            const result = formatHistoryForPrompt(history);
            expect(result).toContain('<test_run_history>');
            expect(result).toContain('success="false"');
            expect(result).toContain('total="3"');
            expect(result).toContain('passed="2"');
            expect(result).toContain('failed="1"');
            expect(result).toContain('name="should_work"');
            expect(result).toContain('error="Timeout"');
            expect(result).toContain('</test_run_history>');
        });
    });
});
