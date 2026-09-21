import {readdirSync, readFileSync} from "node:fs";
import {join} from "node:path";
import ts from "typescript";

export const TOUCH_SCREENSHOT_PROMPT = `<touch_screenshot_policy>
    A fullPage screenshot permanently tears down Chromium's mobile emulation on a
    context created with isMobile: true. After one such capture, (pointer: coarse) and
    (hover: none) stop matching for the rest of that context, so every coarse-pointer
    rule silently stops applying and the assertions after it pass against desktop
    styling. Inside withTouchContext, take viewport screenshots, or make the fullPage
    capture the last action of the callback. This is rejected before execution.
</touch_screenshot_policy>`;

/**
 * Whether this call is `something.screenshot({ ..., fullPage: true, ... })`.
 * The property has to be literally `true`; a variable is not decidable here and
 * is left to the author.
 */
const isFullPageScreenshot = (node: ts.Node): boolean => {
    if (!ts.isCallExpression(node)) return false;
    const callee = node.expression;
    if (!ts.isPropertyAccessExpression(callee) || callee.name.text !== "screenshot") return false;
    return node.arguments.some(argument =>
        ts.isObjectLiteralExpression(argument) && argument.properties.some(property =>
            ts.isPropertyAssignment(property)
            && property.name
            && ts.isIdentifier(property.name)
            && property.name.text === "fullPage"
            && property.initializer.kind === ts.SyntaxKind.TrueKeyword));
};

/** The callback `withTouchContext(browser, viewport, run)` runs in a touch context. */
const touchCallbackBodies = (source: ts.SourceFile): ts.Node[] => {
    const bodies: ts.Node[] = [];
    const visit = (node: ts.Node): void => {
        if (ts.isCallExpression(node)) {
            const callee = node.expression;
            const name = ts.isIdentifier(callee)
                ? callee.text
                : ts.isPropertyAccessExpression(callee) ? callee.name.text : undefined;
            if (name === "withTouchContext") {
                for (const argument of node.arguments) {
                    if ((ts.isArrowFunction(argument) || ts.isFunctionExpression(argument)) && argument.body) {
                        bodies.push(argument.body);
                    }
                }
            }
        }
        ts.forEachChild(node, visit);
    };
    visit(source);
    return bodies;
};

/**
 * Flags a fullPage capture that is not the last thing the touch callback does.
 * "Last" is positional: nothing in the callback may start after the capture
 * ends, at any nesting depth.
 */
export function auditTouchScreenshots(code: string, filePath: string): string[] {
    const source = ts.createSourceFile(filePath, code, ts.ScriptTarget.Latest, true);
    const violations: string[] = [];

    for (const body of touchCallbackBodies(source)) {
        const captures: ts.Node[] = [];
        const statements: ts.Node[] = [];
        const collect = (node: ts.Node): void => {
            if (isFullPageScreenshot(node)) captures.push(node);
            if (ts.isStatement(node) && node !== body) statements.push(node);
            ts.forEachChild(node, collect);
        };
        collect(body);

        for (const capture of captures) {
            const trailing = statements.filter(statement => statement.getStart(source) >= capture.getEnd());
            if (trailing.length === 0) continue;
            const {line} = source.getLineAndCharacterOfPosition(capture.getStart(source));
            const next = source.getLineAndCharacterOfPosition(trailing[0].getStart(source));
            violations.push(
                `${filePath}:${line + 1}: fullPage screenshot inside withTouchContext with `
                + `${trailing.length} statement(s) after it (next at line ${next.line + 1}); `
                + "it ends mobile emulation for the rest of the context",
            );
        }
    }
    return violations;
}

export function assertTouchScreenshotPolicy(directory: string): void {
    const violations: string[] = [];
    const visit = (dir: string) => {
        for (const entry of readdirSync(dir, {withFileTypes: true})) {
            const path = join(dir, entry.name);
            if (entry.isDirectory()) visit(path);
            else if (entry.isFile() && /\.[cm]?[jt]sx?$/.test(entry.name)) {
                violations.push(...auditTouchScreenshots(readFileSync(path, "utf8"), path));
            }
        }
    };
    visit(directory);
    if (violations.length) {
        throw new Error(
            `Touch screenshot policy failed:\n${violations.join("\n")}\n\n`
            + "Take a viewport screenshot instead, or move the fullPage capture to the "
            + "end of the callback.",
        );
    }
}
