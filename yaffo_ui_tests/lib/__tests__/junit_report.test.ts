import {describe, expect, it} from "@jest/globals";
import {join} from "path";
import {formatTestResultsAsJUnit} from "@lib/services/run_playwright_tests";
import {TestResult, TestRunResult} from "@lib/services/isolated_runner";

const SPEC = join(process.cwd(), "generated_tests", "themes", "themes.spec.ts");
const ESC = String.fromCharCode(27);

const runOf = (tests: TestResult[]): TestRunResult => ({
    success: tests.every((t) => t.status === "passed" || t.status === "skipped"),
    exitCode: 0,
    output: "",
    summary: {
        total: tests.length,
        passed: tests.filter((t) => t.status === "passed").length,
        failed: tests.filter((t) => t.status === "failed" || t.status === "timedOut").length,
        skipped: tests.filter((t) => t.status === "skipped").length,
    },
    tests,
});

describe("formatTestResultsAsJUnit", () => {
    it("emits counts and per-status elements a JUnit parser expects", () => {
        const xml = formatTestResultsAsJUnit(runOf([
            {file: SPEC, testName: "Themes > ok", status: "passed", duration: 1000},
            {file: SPEC, testName: "Themes > bad", status: "failed", duration: 500, error: {message: "boom"}},
            {file: SPEC, testName: "Themes > slow", status: "timedOut", duration: 5000},
            {file: SPEC, testName: "Themes > later", status: "skipped", duration: 0},
        ]), "themes");

        expect(xml).toContain(`tests="4" failures="2" skipped="1" errors="0"`);
        expect(xml).toContain("<skipped/>");
        // timedOut counts as a failure, not an unrecognised status.
        expect(xml.match(/<failure /g)).toHaveLength(2);
        expect(xml).toContain(`classname="generated_tests/themes/themes.spec.ts"`);
    });

    it("escapes XML metacharacters in test names and error bodies", () => {
        const xml = formatTestResultsAsJUnit(runOf([{
            file: SPEC,
            testName: `quotes "x" <y> & z`,
            status: "failed",
            duration: 1,
            error: {message: "m", stack: `at <frame> & "quoted"`},
        }]), "themes");

        expect(xml).toContain(`name="quotes &quot;x&quot; &lt;y&gt; &amp; z"`);
        expect(xml).toContain(`at &lt;frame&gt; &amp; &quot;quoted&quot;`);
        expect(xml).not.toMatch(/name="quotes "x"/);
    });

    it("strips the ANSI colouring Playwright puts in error messages", () => {
        const xml = formatTestResultsAsJUnit(runOf([{
            file: SPEC,
            testName: "coloured",
            status: "failed",
            duration: 1,
            errors: [{message: `${ESC}[2mexpect(${ESC}[22mlocator).toBe failed`}],
        }]), "themes");

        expect(xml).toContain(`message="expect(locator).toBe failed"`);
        expect(xml).not.toContain(ESC);
    });

    it("uses only the first line of a multi-line error for the message attribute", () => {
        const xml = formatTestResultsAsJUnit(runOf([{
            file: SPEC,
            testName: "multiline",
            status: "failed",
            duration: 1,
            errors: [{message: "first line\nsecond line"}],
        }]), "themes");

        // A raw newline inside an attribute is legal XML but renders badly and
        // some parsers normalise it away; keep the attribute to one line.
        expect(xml).toContain(`message="first line"`);
    });

    it("handles a run with no tests", () => {
        const xml = formatTestResultsAsJUnit(runOf([]), "empty");
        expect(xml).toContain(`tests="0" failures="0" skipped="0"`);
        expect(xml).toContain("</testsuites>");
    });
});
