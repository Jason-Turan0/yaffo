/**
 * Verify every test spec in yaffo_ui_tests/specs is valid YAML.
 *
 * Parses each *.yaml / *.yml file with the same `yaml` parser the test
 * generator uses, collecting *all* problems (not just the first) so a single
 * run reports every broken file. Exits 0 when everything parses, 1 otherwise —
 * suitable for CI or a pre-commit hook.
 *
 * Usage:
 *   npm run validate:specs                 # validate specs/
 *   tsx scripts/validate_specs.ts [dir…]   # validate one or more directories
 */
import { readdirSync, statSync } from "node:fs";
import { readFile } from "node:fs/promises";
import { relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { parse, YAMLError } from "yaml";

const SCRIPT_DIR = resolve(fileURLToPath(new URL(".", import.meta.url)));
const REPO_DIR = resolve(SCRIPT_DIR, "..");
const DEFAULT_SPEC_DIR = resolve(REPO_DIR, "specs");

const YAML_EXTENSIONS = [".yaml", ".yml"];

function isYamlFile(path: string): boolean {
  return YAML_EXTENSIONS.some((ext) => path.toLowerCase().endsWith(ext));
}

/** Recursively collect YAML files under `dir`, sorted for stable output. */
function collectYamlFiles(dir: string): string[] {
  const found: string[] = [];
  for (const entry of readdirSync(dir)) {
    const full = resolve(dir, entry);
    if (statSync(full).isDirectory()) {
      found.push(...collectYamlFiles(full));
    } else if (isYamlFile(full)) {
      found.push(full);
    }
  }
  return found.sort();
}

type Failure = { file: string; message: string };

async function validateFile(path: string): Promise<Failure | null> {
  const rel = relative(REPO_DIR, path);
  let text: string;
  try {
    text = await readFile(path, "utf8");
  } catch (error) {
    return { file: rel, message: `could not read file: ${(error as Error).message}` };
  }
  try {
    parse(text);
    return null;
  } catch (error) {
    if (error instanceof YAMLError) {
      // YAMLError.message already includes the line/column and a source snippet.
      return { file: rel, message: error.message };
    }
    return { file: rel, message: (error as Error).message };
  }
}

async function main(): Promise<void> {
  const args = process.argv.slice(2);
  const targets = args.length > 0 ? args.map((a) => resolve(process.cwd(), a)) : [DEFAULT_SPEC_DIR];

  const files: string[] = [];
  for (const target of targets) {
    let stats;
    try {
      stats = statSync(target);
    } catch {
      console.error(`✖ path not found: ${relative(REPO_DIR, target)}`);
      process.exit(1);
    }
    if (stats.isDirectory()) {
      files.push(...collectYamlFiles(target));
    } else if (isYamlFile(target)) {
      files.push(resolve(target));
    } else {
      console.error(`✖ not a YAML file or directory: ${relative(REPO_DIR, target)}`);
      process.exit(1);
    }
  }

  if (files.length === 0) {
    console.error("✖ no YAML spec files found");
    process.exit(1);
  }

  const results = await Promise.all(files.map(validateFile));
  const failures = results.filter((r): r is Failure => r !== null);

  for (const failure of failures) {
    console.error(`✖ ${failure.file}\n  ${failure.message.replace(/\n/g, "\n  ")}\n`);
  }

  const total = files.length;
  const failed = failures.length;
  const passed = total - failed;

  if (failed === 0) {
    console.log(`✔ ${passed}/${total} spec files are valid YAML`);
    process.exit(0);
  }
  console.error(`✖ ${failed} of ${total} spec files are invalid YAML (${passed} valid)`);
  process.exit(1);
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
