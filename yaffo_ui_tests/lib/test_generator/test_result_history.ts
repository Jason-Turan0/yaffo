import {z} from "zod";
import {join} from "path";
import {existsSync, readFileSync, writeFileSync} from "fs";
import {TestRunResult} from "@lib/services/isolated_runner";

const MAX_HISTORY_ENTRIES = 5;

const TestRunRecordSchema = z.object({
    timestamp: z.string(),
    success: z.boolean(),
    summary: z.object({
        total: z.number(),
        passed: z.number(),
        failed: z.number(),
        skipped: z.number(),
    }),
    failedTests: z.array(z.object({
        testName: z.string(),
        error: z.string().optional(),
    })),
});

const TestResultHistorySchema = z.object({
    testRunHistory: z.array(TestRunRecordSchema),
});

export type TestRunRecord = z.infer<typeof TestRunRecordSchema>;
export type TestResultHistory = z.infer<typeof TestResultHistorySchema>;

const getHistoryPath = (outputDir: string, featureName: string): string =>
    join(outputDir, `${featureName}.history.json`);

export const loadTestResultHistory = (outputDir: string, featureName: string): TestRunRecord[] => {
    const historyPath = getHistoryPath(outputDir, featureName);
    if (!existsSync(historyPath)) {
        return [];
    }

    try {
        const raw = readFileSync(historyPath, "utf-8");
        const parsed = TestResultHistorySchema.safeParse(JSON.parse(raw));
        if (parsed.success) {
            return parsed.data.testRunHistory;
        }
        console.warn(`Invalid history file at ${historyPath}, starting fresh`);
        return [];
    } catch {
        console.warn(`Failed to read history file at ${historyPath}, starting fresh`);
        return [];
    }
};

export const recordTestResult = (
    outputDir: string,
    featureName: string,
    testRunResult: TestRunResult,
): void => {
    const existing = loadTestResultHistory(outputDir, featureName);

    const record: TestRunRecord = {
        timestamp: new Date().toISOString(),
        success: testRunResult.success,
        summary: {...testRunResult.summary},
        failedTests: testRunResult.tests
            .filter(t => t.status === "failed" || t.status === "timedOut")
            .map(t => ({
                testName: t.testName,
                error: t.error?.message,
            })),
    };

    existing.push(record);
    const trimmed = existing.slice(-MAX_HISTORY_ENTRIES);

    const history: TestResultHistory = {testRunHistory: trimmed};
    const historyPath = getHistoryPath(outputDir, featureName);
    writeFileSync(historyPath, JSON.stringify(history, null, 2));
};

export const formatHistoryForPrompt = (history: TestRunRecord[]): string => {
    if (history.length === 0) {
        return "<test_run_history>\n    <note>No previous test runs recorded.</note>\n</test_run_history>";
    }

    const lines: string[] = ["<test_run_history>"];
    for (const record of history) {
        lines.push(`    <run timestamp="${record.timestamp}" success="${record.success}">`);
        lines.push(`        <summary total="${record.summary.total}" passed="${record.summary.passed}" failed="${record.summary.failed}" skipped="${record.summary.skipped}" />`);
        if (record.failedTests.length > 0) {
            lines.push("        <failed_tests>");
            for (const ft of record.failedTests) {
                lines.push(`            <test name="${ft.testName}"${ft.error ? ` error="${ft.error}"` : ""} />`);
            }
            lines.push("        </failed_tests>");
        }
        lines.push("    </run>");
    }
    lines.push("</test_run_history>");
    return lines.join("\n");
};
