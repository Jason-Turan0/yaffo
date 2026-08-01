/**
 * Static audit of model-generated test code, run at the JSON→write boundary —
 * while the code is still data, before it is written to disk or executed.
 *
 * Policy:
 *  - Bare imports are allowlisted: @playwright/test plus benign node builtins.
 *  - Privileged builtins (child_process, fs) are always rejected — sandbox
 *    setup goes through the narrow, reviewed helpers in generated_tests/_support.
 *  - Network/exec-adjacent builtins (http, net, vm, worker_threads, …) and any
 *    other bare package (axios, lodash, …) are rejected — generated tests talk
 *    to the app via Playwright only.
 *  - Relative imports must stay inside generated_tests/.
 *  - eval, the Function constructor, and non-literal require()/import() are
 *    rejected (they would bypass this audit).
 *
 * This is a tripwire, not a sandbox: determined obfuscation can evade static
 * analysis, which is why the spawn env is scrubbed and CI wraps execution in
 * bubblewrap. Together the layers cover credential theft and the obvious
 * malicious-code shapes.
 */
import ts from "typescript";
import {dirname, resolve, sep} from "path";
import {GENERATED_TESTS_ROOT} from "@lib/types";

/** Bare module specifiers generated tests may always import. */
const ALLOWED_BARE_MODULES = new Set([
    "@playwright/test",
    "path",
    "os",
    "url",
    "util",
    "assert",
    "buffer",
    "crypto",
]);

/** Privileged builtins — never importable from generated tests; the narrow
 * helpers in generated_tests/_support wrap the legitimate uses. */
const PRIVILEGED_MODULES = new Set(["child_process", "fs", "fs/promises"]);

export interface CodeAuditOptions {
    /** Absolute path the code will be written to (anchors relative imports). */
    filePath: string;
}

const stripNodePrefix = (specifier: string): string =>
    specifier.startsWith("node:") ? specifier.slice("node:".length) : specifier;

interface ParsedCode {
    specifiers: string[];
    violations: string[];
}

function parseCode(code: string, fileLabel: string): ParsedCode {
    const sourceFile = ts.createSourceFile(fileLabel, code, ts.ScriptTarget.Latest, true);
    const specifiers: string[] = [];
    const violations: string[] = [];

    const addSpecifier = (node: ts.Expression | undefined, context: string): void => {
        if (node && ts.isStringLiteralLike(node)) {
            specifiers.push(node.text);
        } else {
            violations.push(`${context} with a non-literal module specifier is not allowed`);
        }
    };

    const visit = (node: ts.Node): void => {
        if (ts.isImportDeclaration(node) || ts.isExportDeclaration(node)) {
            if (node.moduleSpecifier) {
                addSpecifier(node.moduleSpecifier, "import/export");
            }
        } else if (ts.isImportEqualsDeclaration(node) && ts.isExternalModuleReference(node.moduleReference)) {
            addSpecifier(node.moduleReference.expression, "import =");
        } else if (ts.isCallExpression(node)) {
            if (node.expression.kind === ts.SyntaxKind.ImportKeyword) {
                addSpecifier(node.arguments[0], "dynamic import()");
            } else if (ts.isIdentifier(node.expression) && node.expression.text === "require") {
                addSpecifier(node.arguments[0], "require()");
            } else if (ts.isIdentifier(node.expression) && node.expression.text === "eval") {
                violations.push("eval() is not allowed");
            } else if (ts.isIdentifier(node.expression) && node.expression.text === "Function") {
                violations.push("the Function constructor is not allowed");
            }
        } else if (ts.isNewExpression(node) && ts.isIdentifier(node.expression) && node.expression.text === "Function") {
            violations.push("the Function constructor is not allowed");
        }
        ts.forEachChild(node, visit);
    };
    visit(sourceFile);

    return {specifiers, violations};
}

/**
 * Audit generated test code. Returns a list of violations; empty means the
 * code passed. Never throws on malformed code — the TypeScript compile step
 * catches syntax errors separately.
 */
export function auditGeneratedCode(code: string, options: CodeAuditOptions): string[] {
    const {specifiers, violations} = parseCode(code, options.filePath);

    for (const raw of specifiers) {
        const specifier = stripNodePrefix(raw);
        if (specifier.startsWith(".")) {
            const target = resolve(dirname(options.filePath), specifier);
            const root = resolve(GENERATED_TESTS_ROOT);
            if (target !== root && !target.startsWith(root + sep)) {
                violations.push(`relative import '${raw}' resolves outside generated_tests/`);
            }
            continue;
        }
        if (ALLOWED_BARE_MODULES.has(specifier)) {
            continue;
        }
        if (PRIVILEGED_MODULES.has(specifier)) {
            violations.push(
                `import of '${raw}' is not allowed: use the reviewed helpers in ` +
                "generated_tests/_support (e.g. sandbox-fs, theme-draft) for sandbox setup instead",
            );
            continue;
        }
        violations.push(
            `import of '${raw}' is not allowed: only @playwright/test, ` +
            `${[...ALLOWED_BARE_MODULES].filter(m => m !== "@playwright/test").join(", ")}, ` +
            "and relative imports within generated_tests/ are permitted",
        );
    }

    return violations;
}
