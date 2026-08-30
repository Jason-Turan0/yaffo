import {join, resolve} from "path";

/**
 * The project virtualenv's interpreter. Pillow and NumPy are already project
 * dependencies, so image encoding and comparison run there instead of pulling npm
 * packages that would need their own WebP decoder.
 *
 * Resolved from the working directory, like the rest of the harness: every entry
 * point is run from yaffo_ui_tests/.
 */
export const VENV_PYTHON = resolve(join(process.cwd(), "..", "venv", "bin", "python"));

/** Absolute path to a helper script living beside this module. */
export const supportScript = (name: string): string =>
    resolve(join(process.cwd(), "lib", "user_doc_automation", name));
