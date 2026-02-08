import {execSync} from "child_process";
import {dirname} from "path";

export interface TypeCheckResult {
    success: boolean;
    errors: string[];
    errorCount: number;
}

export interface TypeScriptValidator {
    typeCheckFile(filePath: string): TypeCheckResult;
    formatTypeErrorsForModel(filePath: string, result: TypeCheckResult): string;
}

export const typeCheckFile = (filePath: string): TypeCheckResult => {
    const projectDir = dirname(filePath);

    try {
        // Run tsc with --noEmit to just check types without generating output
        // Use the project's tsconfig if available
        execSync(
            `npx tsc --noEmit --skipLibCheck "${filePath}"`,
            {
                cwd: projectDir,
                encoding: "utf-8",
                stdio: ["pipe", "pipe", "pipe"],
            }
        );

        return {
            success: true,
            errors: [],
            errorCount: 0,
        };
    } catch (e) {
        const error = e as { stdout?: string; stderr?: string; message?: string };
        const output = error.stdout || error.stderr || error.message || "";

        // Parse tsc output to extract errors
        const errorLines = output
            .split("\n")
            .filter((line) => line.includes("error TS") || line.includes(": error"))
            .map((line) => line.trim());

        return {
            success: false,
            errors: errorLines.length > 0 ? errorLines : [output.slice(0, 1000)],
            errorCount: errorLines.length || 1,
        };
    }
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
        const projectDir = dirname(filePath);

        try {
            execSync(
                `npx tsc --noEmit --skipLibCheck "${filePath}"`,
                {
                    cwd: projectDir,
                    encoding: "utf-8",
                    stdio: ["pipe", "pipe", "pipe"],
                }
            );

            return {
                success: true,
                errors: [],
                errorCount: 0,
            };
        } catch (e) {
            const error = e as { stdout?: string; stderr?: string; message?: string };
            const output = error.stdout || error.stderr || error.message || "";

            const errorLines = output
                .split("\n")
                .filter((line) => line.includes("error TS") || line.includes(": error"))
                .map((line) => line.trim());

            return {
                success: false,
                errors: errorLines.length > 0 ? errorLines : [output.slice(0, 1000)],
                errorCount: errorLines.length || 1,
            };
        }
    }

    formatTypeErrorsForModel(filePath: string, result: TypeCheckResult): string {
        if (result.success) {
            return "";
        }

        return `TypeScript compilation failed for ${filePath}:

${result.errors.map((e, i) => `${i + 1}. ${e}`).join("\n")}

Please fix these TypeScript errors and provide the corrected code in the same JSON format.`;
    }
}