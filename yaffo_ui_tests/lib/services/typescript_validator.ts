import {execSync} from "child_process";
import {existsSync} from "fs";
import {dirname, join, resolve} from "path";

export interface TypeCheckResult {
    success: boolean;
    errors: string[];
    errorCount: number;
}

export interface TypeScriptValidator {
    typeCheckFile(filePath: string): TypeCheckResult;
    formatTypeErrorsForModel(filePath: string, result: TypeCheckResult): string;
}

/** Nearest tsconfig.json at or above `startDir`, or undefined at the filesystem root. */
export const findTsconfig = (startDir: string): string | undefined => {
    let dir = resolve(startDir);
    for (;;) {
        const candidate = join(dir, "tsconfig.json");
        if (existsSync(candidate)) return candidate;
        const parent = dirname(dir);
        if (parent === dir) return undefined;
        dir = parent;
    }
};

/** `path(line,col): error TSxxxx: message` — the only shape tsc emits for diagnostics. */
const DIAGNOSTIC = /^(.+?)\((\d+),(\d+)\):\s+error\s+TS\d+:/;

/**
 * Diagnostic lines from a tsc run that belong to `targetFile`.
 *
 * tsc prints paths relative to the project directory, so each one is resolved
 * against it before comparing.
 */
export const diagnosticsForFile = (output: string, projectDir: string, targetFile: string): string[] => {
    const target = resolve(targetFile);
    return output
        .split("\n")
        .map((line) => line.trimEnd())
        .filter((line) => {
            const match = DIAGNOSTIC.exec(line);
            if (!match) return false;
            return resolve(projectDir, match[1].trim()) === target;
        })
        .map((line) => line.trim());
};

/**
 * Type-check one file using the project's own compiler options.
 *
 * Passing a file directly to `tsc` makes it ignore tsconfig.json entirely and
 * fall back to compiler defaults — no esModuleInterop, no `@lib/*` path
 * aliases, a different target. That reported failures on correct code (a
 * default import of `node:fs` became "has no default export"), and the healer
 * fed those phantom errors back to the model, which then "fixed" code that was
 * already right. So run the real project check and keep only the diagnostics
 * belonging to this file.
 *
 * Errors in *other* files are deliberately ignored: this answers "is this file
 * type-correct", which is the question the heal loop asks after rewriting it.
 */
export const typeCheckFile = (filePath: string): TypeCheckResult => {
    const target = resolve(filePath);
    const tsconfigPath = findTsconfig(dirname(target));

    if (!tsconfigPath) {
        return {
            success: false,
            errors: [`No tsconfig.json found at or above ${dirname(target)}`],
            errorCount: 1,
        };
    }

    const projectDir = dirname(tsconfigPath);
    let output = "";

    try {
        execSync(
            `npx tsc --noEmit --skipLibCheck -p "${tsconfigPath}"`,
            {
                cwd: projectDir,
                encoding: "utf-8",
                stdio: ["pipe", "pipe", "pipe"],
            }
        );
        // Whole project compiled, so this file did too.
        return {success: true, errors: [], errorCount: 0};
    } catch (e) {
        const error = e as { stdout?: string; stderr?: string; message?: string };
        output = error.stdout || error.stderr || error.message || "";
    }

    const errors = diagnosticsForFile(output, projectDir, target);
    if (errors.length === 0) {
        // The project has errors, but none in this file — nothing for the model
        // to act on here, and reporting them would send it editing the wrong file.
        return {success: true, errors: [], errorCount: 0};
    }

    return {success: false, errors, errorCount: errors.length};
};

export const formatTypeErrorsForModel = (filePath: string, result: TypeCheckResult): string => {
    if (result.success) {
        return "";
    }

    return `TypeScript compilation failed for ${filePath}:

${result.errors.map((e, i) => `${i + 1}. ${e}`).join("\n")}

Please fix these TypeScript errors and provide the corrected code in the same JSON format.`;
};

export class DefaultTypeScriptValidator implements TypeScriptValidator {
    typeCheckFile(filePath: string): TypeCheckResult {
        return typeCheckFile(filePath);
    }

    formatTypeErrorsForModel(filePath: string, result: TypeCheckResult): string {
        return formatTypeErrorsForModel(filePath, result);
    }
}
