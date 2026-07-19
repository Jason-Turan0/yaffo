/**
 * Emit a fan-out matrix of the spec files that FAILED, read from one or more
 * Playwright JSON reports. Used by the auto-heal workflow to heal each failed
 * spec in its own environment.
 *
 * Walks a directory tree for every `test-results.json` (each per-spec CI job
 * uploads its own report), collects spec files with an "unexpected" test, and
 * prints the same matrix shape as list_specs.ts (id/spec/project/peer).
 *
 * Usage:
 *   tsx scripts/failed_spec_matrix.ts <reports-dir> [--github]
 */
import {appendFileSync, readdirSync, readFileSync, statSync} from "node:fs";
import {join, resolve} from "node:path";

import {emitMatrix, SPEC_ROOT} from "./list_specs";

interface PlaywrightSuite {
    file?: string;
    specs?: {tests: {status: string}[]}[];
    suites?: PlaywrightSuite[];
}
interface PlaywrightReport {
    suites?: PlaywrightSuite[];
}

function findReports(dir: string): string[] {
    const found: string[] = [];
    for (const entry of readdirSync(dir)) {
        const full = join(dir, entry);
        if (statSync(full).isDirectory()) {
            found.push(...findReports(full));
        } else if (entry === "test-results.json") {
            found.push(full);
        }
    }
    return found;
}

function failedSpecsFromReport(reportPath: string): Set<string> {
    const report = JSON.parse(readFileSync(reportPath, "utf-8")) as PlaywrightReport;
    const failed = new Set<string>();
    const visit = (suite: PlaywrightSuite): void => {
        for (const spec of suite.specs || []) {
            if (spec.tests.some((t) => t.status === "unexpected") && suite.file) {
                // report `file` is relative to generated_tests (the testDir).
                failed.add(resolve(SPEC_ROOT, suite.file));
            }
        }
        for (const nested of suite.suites || []) {
            visit(nested);
        }
    };
    for (const suite of report.suites || []) {
        visit(suite);
    }
    return failed;
}

function main(): void {
    const args = process.argv.slice(2);
    const emitGithub = args.includes("--github");
    const dirArg = args.find((a) => !a.startsWith("--")) || "reports";
    const reportsDir = resolve(process.cwd(), dirArg);

    const failed = new Set<string>();
    for (const report of findReports(reportsDir)) {
        for (const spec of failedSpecsFromReport(report)) {
            failed.add(spec);
        }
    }

    emitMatrix([...failed].sort(), emitGithub);
    if (emitGithub && process.env.GITHUB_OUTPUT) {
        appendFileSync(process.env.GITHUB_OUTPUT, `has_failures=${failed.size > 0}\n`);
    }
}

main();
