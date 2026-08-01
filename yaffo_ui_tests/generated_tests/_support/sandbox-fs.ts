/**
 * Filesystem test utilities for generated specs.
 *
 * Generated test code may not import fs or child_process directly (enforced by
 * the code-safety audit in lib/test_generator/code_safety.ts) — privileged
 * operations live here instead, behind narrow fixed-purpose functions with
 * data-only parameters. All write and delete operations are confined to the OS
 * temp directory, which is where the isolated sandbox environments live; reads
 * are additionally allowed from the repo's test_data fixtures.
 */
import fs from 'node:fs';
import path from 'node:path';
import {tmpdir} from 'node:os';

// On macOS tmpdir() is /var/folders/… where /var is a symlink; realpath both
// sides so containment checks compare like with like.
const TEMP_ROOT = fs.realpathSync(tmpdir());
// Test processes always run with cwd = yaffo_ui_tests (see runPlaywrightTests).
const TEST_DATA_ROOT = path.resolve(process.cwd(), 'test_data');

/** Realpath a possibly-not-yet-existing path via its nearest existing ancestor. */
function realpathDeep(target: string): string {
    let existing = path.resolve(target);
    const tail: string[] = [];
    while (!fs.existsSync(existing)) {
        tail.unshift(path.basename(existing));
        const parent = path.dirname(existing);
        if (parent === existing) break;
        existing = parent;
    }
    return path.join(fs.realpathSync(existing), ...tail);
}

/** Throws unless the path is inside the OS temp root (where sandboxes live). */
function assertInTempRoot(target: string): string {
    const resolved = realpathDeep(target);
    if (resolved !== TEMP_ROOT && !resolved.startsWith(TEMP_ROOT + path.sep)) {
        throw new Error(`Refusing filesystem operation outside the temp root: ${target}`);
    }
    return resolved;
}

/** Throws unless the path is inside temp root or test_data (read-only scope). */
function assertReadable(target: string): string {
    const resolved = realpathDeep(target);
    const inTemp = resolved === TEMP_ROOT || resolved.startsWith(TEMP_ROOT + path.sep);
    const inTestData = resolved === TEST_DATA_ROOT || resolved.startsWith(TEST_DATA_ROOT + path.sep);
    if (!inTemp && !inTestData) {
        throw new Error(`Refusing to read outside temp root or test_data: ${target}`);
    }
    return resolved;
}

/** Create a directory (and parents) under the temp root. */
export function ensureTempDir(dir: string): void {
    fs.mkdirSync(assertInTempRoot(dir), {recursive: true});
}

/** Delete (if present) and re-create a directory under the temp root. */
export function resetTempDir(dir: string): void {
    const resolved = assertInTempRoot(dir);
    fs.rmSync(resolved, {recursive: true, force: true});
    fs.mkdirSync(resolved, {recursive: true});
}

/** Recursively delete directories under the temp root; skips empty/undefined. */
export function removeTempDirs(...dirs: Array<string | undefined>): void {
    for (const dir of dirs) {
        if (!dir) continue;
        fs.rmSync(assertInTempRoot(dir), {recursive: true, force: true});
    }
}

/** Delete a single file under the temp root if it exists. */
export function removeTempFile(file: string | null | undefined): void {
    if (!file) return;
    const resolved = assertInTempRoot(file);
    if (fs.existsSync(resolved)) fs.unlinkSync(resolved);
}

/** Number of entries directly inside a temp-root directory. */
export function countEntriesIn(dir: string): number {
    return fs.readdirSync(assertReadable(dir)).length;
}

/** Recursive list of files (absolute paths) under a readable directory. */
export function listFilesRecursive(dir: string): string[] {
    const resolved = realpathDeep(dir);
    if (!fs.existsSync(resolved)) return [];
    assertReadable(resolved);
    const results: string[] = [];
    for (const entry of fs.readdirSync(resolved, {withFileTypes: true})) {
        const full = path.join(resolved, entry.name);
        if (entry.isDirectory()) results.push(...listFilesRecursive(full));
        else results.push(full);
    }
    return results;
}

/** Names of the immediate subdirectories of a readable directory. */
export function listSubdirectories(dir: string): string[] {
    const resolved = assertReadable(dir);
    return fs.readdirSync(resolved, {withFileTypes: true})
        .filter((entry) => entry.isDirectory())
        .map((entry) => entry.name);
}

/** First photo file (jpg/png/heic) found under a readable directory. */
export function findAnyPhotoIn(dir: string): string {
    for (const file of listFilesRecursive(dir)) {
        if (/\.(jpe?g|png|heic)$/i.test(file)) return file;
    }
    throw new Error(`No supported photo found under ${dir}`);
}

/**
 * Copy a photo into the temp root and append a marker so its content hash is
 * unique. Source must be readable (temp root or test_data).
 */
export function copyPhotoWithUniqueMarker(source: string, destFile: string, marker: string): void {
    const resolvedDest = assertInTempRoot(destFile);
    fs.copyFileSync(assertReadable(source), resolvedDest);
    fs.appendFileSync(resolvedDest, Buffer.from(marker));
}

/**
 * Build a duplicate-photo corpus for the remove-duplicates suite: groupCount
 * visually distinct photos from the test_data fixtures, each copied twice
 * (dup-<g>-a.jpg / dup-<g>-b.jpg) into scanDir. Real photographs have distinct
 * perceptual hashes, so each pair forms its own duplicate group.
 */
export function buildDuplicateImageCorpus(scanDir: string, destDir: string, groupCount: number): void {
    ensureTempDir(scanDir);
    ensureTempDir(destDir);
    const sources = fs.readdirSync(path.join(TEST_DATA_ROOT, 'obama', 'images'))
        .filter((name) => /\.jpe?g$/i.test(name))
        .sort()
        .slice(0, groupCount);
    if (sources.length < groupCount) {
        throw new Error(`Need ${groupCount} fixture photos, found ${sources.length}`);
    }
    sources.forEach((name, group) => {
        const source = path.join(TEST_DATA_ROOT, 'obama', 'images', name);
        for (const copy of ['a', 'b']) {
            fs.copyFileSync(source, path.join(assertInTempRoot(scanDir), `dup-${group}-${copy}.jpg`));
        }
    });
}
