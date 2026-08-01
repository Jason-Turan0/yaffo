/**
 * Emit a fan-out matrix of the FEATURE DIRECTORIES containing spec files that
 * FAILED, read from one or more Playwright JSON reports. Used by the auto-heal
 * workflow to heal one feature per job — all failed files in a feature heal
 * serially inside that job, so per-feature files (memories/, sibling specs)
 * never end up in two conflicting patches.
 *
 * Walks a directory tree for every `test-results.json` (each per-spec CI job
 * uploads its own report), collects spec files with an "unexpected" test, and
 * groups them by their containing directory. Matrix entries carry:
 *   - id:      filesystem/artifact-safe identifier for the feature dir
 *   - spec:    the feature's YAML spec (specs/<feature>.yaml) — heal_test input
 *   - dir:     feature directory relative to yaffo_ui_tests
 *   - project/peer: same semantics as list_specs.ts (uniform per directory)
 *
 * Usage:
 *   tsx scripts/failed_spec_matrix.ts <reports-dir> [--github]
 */
import {appendFileSync, readdirSync, readFileSync, statSync} from "node:fs";
import {dirname, join, relative, resolve} from "node:path";

import {SPEC_ROOT, toEntry, UI_TESTS_DIR} from "./list_specs";

interface PlaywrightSuite {
    file?: string;
    specs?: {tests: {status: string}[]}[];
    suites?: PlaywrightSuite[];
}
interface PlaywrightReport {
    suites?: PlaywrightSuite[];
}

interface FeatureEntry {
    id: string;
    spec: string;
    dir: string;
    project: "chromium" | "sharing";
    peer: "true" | "false";
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

/** Group failed spec files by feature directory and emit one entry per dir. */
function emitFeatureMatrix(absPaths: string[], emitGithub: boolean): void {
    const byDir = new Map<string, string[]>();
    for (const absPath of absPaths) {
        const dir = dirname(absPath);
        byDir.set(dir, [...(byDir.get(dir) ?? []), absPath]);
    }
    const include: FeatureEntry[] = [...byDir.entries()]
        .map(([dir, files]) => {
            // project/peer derive from the path prefix, so any file works.
            const {project, peer} = toEntry(files[0]);
            return {
                id: relative(SPEC_ROOT, dir).replace(/[^a-zA-Z0-9]+/g, "__"),
                // Inverse of the generator's specs/<rel>.yaml → generated_tests/<rel>.
                spec: join("specs", `${relative(SPEC_ROOT, dir)}.yaml`),
                dir: relative(UI_TESTS_DIR, dir),
                project,
                peer,
            };
        })
        .sort((a, b) => a.id.localeCompare(b.id));
    const json = JSON.stringify({include});
    process.stdout.write(json + "\n");
    if (emitGithub && process.env.GITHUB_OUTPUT) {
        appendFileSync(process.env.GITHUB_OUTPUT, `matrix=${json}\n`);
    }
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

    emitFeatureMatrix([...failed].sort(), emitGithub);
    if (emitGithub && process.env.GITHUB_OUTPUT) {
        appendFileSync(process.env.GITHUB_OUTPUT, `has_failures=${failed.size > 0}\n`);
    }
}

main();
