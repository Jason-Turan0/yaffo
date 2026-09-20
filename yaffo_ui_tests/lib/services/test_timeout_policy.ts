import {readdirSync, readFileSync} from "node:fs";
import {join} from "node:path";
import ts from "typescript";

export const MAX_TEST_TIMEOUT_MS = 30_000;

export const TEST_TIMEOUT_PROMPT = `<test_timeout_policy>
    Every Playwright test has a hard ${MAX_TEST_TIMEOUT_MS} ms (30-second) timeout, including its setup and beforeEach hooks.
    Inherit the configured timeout. Do not use test.setTimeout(), testInfo.setTimeout(), test.slow(),
    testInfo.slow(), or test.describe.configure({ timeout: ... }); these overrides are rejected before execution.
    Never disable timeouts or set action, assertion, polling, or fixture timeouts above 30 seconds.
    When a test times out, investigate the actual bottleneck: selectors, async rendering, reload loops,
    network requests, and polling. Wait for async content to finish loading before deciding to reload.
    Preserve assertions and behavior coverage. Do not skip tests, weaken assertions, add retries, or
    increase budgets to hide slowness. Split independent scenarios when appropriate; report a real
    application or environment problem when the required behavior cannot complete within the limit.
    This policy overrides older test code, metadata, and memory notes suggesting larger timeouts.
</test_timeout_policy>`;

/** Keep specs from overriding the runner's per-test budget. */
export function auditTestTimeouts(code: string, filePath: string): string[] {
    const source = ts.createSourceFile(filePath, code, ts.ScriptTarget.Latest, true);
    const violations: string[] = [];
    const report = (node: ts.Node, message: string) => {
        const {line} = source.getLineAndCharacterOfPosition(node.getStart(source));
        violations.push(`${filePath}:${line + 1}: ${message} (30-second test limit)`);
    };
    const nameOf = (node: ts.Node | undefined): string | undefined => {
        if (node && (ts.isIdentifier(node) || ts.isStringLiteralLike(node))) return node.text;
        return undefined;
    };
    const memberName = (node: ts.Node): string | undefined => {
        if (ts.isPropertyAccessExpression(node)) return node.name.text;
        if (ts.isElementAccessExpression(node)) return nameOf(node.argumentExpression);
        return undefined;
    };
    const visit = (node: ts.Node): void => {
        const member = memberName(node);
        if (member === "setTimeout" || member === "slow") {
            report(node, `Timeout overrides (${member}) are not allowed; inherit playwright.config.ts`);
        }
        if (ts.isBindingElement(node) && ["setTimeout", "slow"].includes(nameOf(node.propertyName ?? node.name) ?? "")) {
            report(node, "Extracting timeout override methods is not allowed");
        }
        if (ts.isCallExpression(node) && memberName(node.expression) === "configure") {
            const options = node.arguments[0];
            if (!options || !ts.isObjectLiteralExpression(options) || options.properties.some(property =>
                ts.isSpreadAssignment(property) || nameOf(property.name) === "timeout")) {
                report(node, "configure() must use literal options without a timeout override or spread");
            }
        }
        if (ts.isPropertyAssignment(node) && nameOf(node.name) === "timeout" && ts.isNumericLiteral(node.initializer)) {
            const timeout = Number(node.initializer.text);
            if (timeout === 0 || timeout > MAX_TEST_TIMEOUT_MS) {
                report(node, "Explicit timeouts must be between 1 and 30000 ms");
            }
        }
        ts.forEachChild(node, visit);
    };
    visit(source);
    return violations;
}

export function assertTestTimeoutPolicy(directory: string): void {
    const violations: string[] = [];
    const visit = (dir: string) => {
        for (const entry of readdirSync(dir, {withFileTypes: true})) {
            const path = join(dir, entry.name);
            if (entry.isDirectory()) visit(path);
            else if (entry.isFile() && /\.[cm]?[jt]sx?$/.test(entry.name)) {
                violations.push(...auditTestTimeouts(readFileSync(path, "utf8"), path));
            }
        }
    };
    visit(directory);
    if (violations.length) throw new Error(`Playwright timeout policy failed:\n${violations.join("\n")}`);
}
