/**
 * Injects a READY working draft into a custom theme's ApplicationSettings row —
 * exactly the state a finished AI generation leaves. The sandbox has no AI key,
 * so this is the only seam for reaching the publish/discard UI.
 *
 * Generated test code may not import child_process (enforced by the
 * code-safety audit); this helper runs one FIXED Python script against the
 * sandbox's SQLite database, parameterized by data-only arguments passed via
 * sys.argv — nothing model-controlled is ever interpolated into the code.
 */
import {execFileSync} from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import {tmpdir} from 'node:os';

const TEMP_ROOT = fs.realpathSync(tmpdir());
// Test processes always run with cwd = yaffo_ui_tests (see runPlaywrightTests);
// the app venv lives one level up at the repo root.
const VENV_PYTHON = path.resolve(process.cwd(), '..', 'venv', 'bin', 'python');

// The DB is WAL-mode SQLite shared with the sandbox Flask process, hence the
// server venv's Python + a busy timeout rather than a Node driver.
const INJECT_DRAFT_SCRIPT = [
    'import json, sqlite3, sys',
    'db_path, slug, marker = sys.argv[1], sys.argv[2], sys.argv[3]',
    'conn = sqlite3.connect(db_path, timeout=30)',
    'name = f"custom_theme:{slug}"',
    'row = conn.execute("SELECT value FROM application_settings WHERE name = ?", (name,)).fetchone()',
    'theme = json.loads(row[0])',
    'theme["status"] = "READY"',
    'theme["working_theme"] = {',
    '    "tokens_css": f"[data-theme=\\"{slug}\\"] {{\\n    --color-bg: {marker};\\n}}\\n",',
    '    "skin_css": "", "favicon_svg": "", "placeholder_svg": "",',
    '}',
    'conn.execute("UPDATE application_settings SET value = ? WHERE name = ?", (json.dumps(theme), name))',
    'conn.commit()',
    'conn.close()',
].join('\n');

export function injectReadyThemeDraft(sandboxDbPath: string, slug: string, marker: string): void {
    const resolvedDb = fs.realpathSync(path.resolve(sandboxDbPath));
    if (!resolvedDb.startsWith(TEMP_ROOT + path.sep)) {
        throw new Error(`Refusing to touch a database outside the sandbox temp root: ${sandboxDbPath}`);
    }
    if (!/^[a-z0-9-]+$/i.test(slug)) {
        throw new Error(`Invalid theme slug: ${slug}`);
    }
    if (!/^#[0-9a-f]{3,8}$/i.test(marker)) {
        throw new Error(`Marker must be a CSS hex color: ${marker}`);
    }
    execFileSync(VENV_PYTHON, ['-c', INJECT_DRAFT_SCRIPT, resolvedDb, slug, marker]);
}
