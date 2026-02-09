import {TestRunResult} from "@lib/services/isolated_runner";
import {formatTestResultsAsXml} from "@lib/services/run_playwright_tests";

export const buildTestFailurePrompt = (testFailures: TestRunResult): string => {
    const failuresXml = formatTestResultsAsXml(testFailures);
    return [
        failuresXml,
        "",
        "",
        "<instructions>The test is still failing. Investigate further and provide corrected code.</instructions>",
    ].join("\n");
}