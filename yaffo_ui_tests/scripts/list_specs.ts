/**
 * Emit the per-spec-file test matrix for CI fan-out.
 *
 * Prints a GitHub Actions matrix object ({ "include": [...] }) with one entry
 * per generated spec file:
 *   - id:      filesystem/artifact-safe identifier (dir__file)
 *   - spec:    path passed to `playwright test`
 *   - project: 'sharing' for specs under generated_tests/sharing, else 'chromium'
 *   - peer:    'true' when the spec needs the two-instance (peer) environment
 *
 * Usage:
 *   tsx scripts/list_specs.ts                 # every spec file
 *   tsx scripts/list_specs.ts <spec> [<spec>] # only the given specs (auto-heal)
 *
 * With `--github`, also appends `matrix=<json>` to $GITHUB_OUTPUT.
 */
import {appendFileSync, existsSync, readdirSync, statSync} from "node:fs";
import {relative, resolve} from "node:path";

export const UI_TESTS_DIR = resolve(process.cwd());
export const SPEC_ROOT = resolve(UI_TESTS_DIR, "generated_tests");

export interface SpecEntry {
    id: string;
    spec: string;
    project: "chromium" | "sharing";
    peer: "true" | "false";
}

function collectSpecFiles(dir: string): string[] {
    const found: string[] = [];
    for (const entry of readdirSync(dir)) {
        const full = resolve(dir, entry);
        if (statSync(full).isDirectory()) {
            found.push(...collectSpecFiles(full));
        } else if (full.endsWith(".spec.ts")) {
            found.push(full);
        }
    }
    return found;
}

/**
 * Derive a matrix entry from a spec file's absolute path. Shared with
 * failed_spec_matrix so CI fan-out and auto-heal agree on id/project/peer.
 */
export function toEntry(absPath: string): SpecEntry {
    const specPath = relative(UI_TESTS_DIR, absPath);
    const isSharing = relative(SPEC_ROOT, absPath).split("/")[0] === "sharing";
    const id = relative(SPEC_ROOT, absPath)
        .replace(/\.spec\.ts$/, "")
        .replace(/[^a-zA-Z0-9]+/g, "__");
    return {
        id,
        spec: specPath,
        project: isSharing ? "sharing" : "chromium",
        peer: isSharing ? "true" : "false",
    };
}

/** Serialize entries as a GitHub Actions matrix and optionally emit to $GITHUB_OUTPUT. */
export function emitMatrix(absPaths: string[], emitGithub: boolean): void {
    const include = absPaths.map(toEntry).sort((a, b) => a.id.localeCompare(b.id));
    const json = JSON.stringify({include});
    process.stdout.write(json + "\n");
    if (emitGithub && process.env.GITHUB_OUTPUT) {
        appendFileSync(process.env.GITHUB_OUTPUT, `matrix=${json}\n`);
    }
}

function main(): void {
    const args = process.argv.slice(2).filter((a) => a !== "--github");
    const emitGithub = process.argv.includes("--github");

    let absPaths: string[];
    if (args.length > 0) {
        absPaths = args.map((a) => resolve(UI_TESTS_DIR, a));
        const missing = absPaths.filter((p) => !existsSync(p));
        if (missing.length > 0) {
            console.error(`✖ spec file(s) not found:\n${missing.map((m) => `  ${m}`).join("\n")}`);
            process.exit(1);
        }
    } else {
        absPaths = collectSpecFiles(SPEC_ROOT);
    }

    emitMatrix(absPaths, emitGithub);
}

// Run only when invoked directly, so failed_spec_matrix can import the helpers.
if (process.argv[1] && resolve(process.argv[1]).endsWith("list_specs.ts")) {
    main();
}
