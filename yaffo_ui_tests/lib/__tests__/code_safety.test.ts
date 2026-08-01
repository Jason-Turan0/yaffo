import {join} from "path";
import {auditGeneratedCode} from "@lib/test_generator/code_safety";
import {GENERATED_TESTS_ROOT} from "@lib/types";

const filePath = join(GENERATED_TESTS_ROOT, "albums", "albums.spec.ts");

describe("auditGeneratedCode", () => {
    it("accepts a plain Playwright test", () => {
        const code = `
            import {test, expect} from '@playwright/test';
            import {findMediaByFilename} from '../_support/media-test-data';
            test('loads', async ({page}) => {
                await page.goto('/albums');
                await expect(page.locator('h1')).toBeVisible();
            });
        `;
        expect(auditGeneratedCode(code, {filePath})).toEqual([]);
    });

    it("accepts allowlisted node builtins with and without node: prefix", () => {
        const code = `
            import {join} from 'node:path';
            import {tmpdir} from 'os';
        `;
        expect(auditGeneratedCode(code, {filePath})).toEqual([]);
    });

    it("rejects network modules", () => {
        const code = `import http from 'node:http';`;
        expect(auditGeneratedCode(code, {filePath})).toHaveLength(1);
    });

    it("rejects unknown bare packages", () => {
        const code = `import axios from 'axios';`;
        const violations = auditGeneratedCode(code, {filePath});
        expect(violations).toHaveLength(1);
        expect(violations[0]).toContain("axios");
    });

    it("rejects privileged modules and points at the _support helpers", () => {
        for (const specifier of ["node:child_process", "child_process", "node:fs", "fs", "fs/promises"]) {
            const violations = auditGeneratedCode(`import x from '${specifier}';`, {filePath});
            expect(violations).toHaveLength(1);
            expect(violations[0]).toContain("_support");
        }
    });

    it("accepts imports of the _support helpers", () => {
        const code = `
            import {buildDuplicateImageCorpus, removeTempDirs} from '../_support/sandbox-fs';
            import {injectReadyThemeDraft} from '../_support/theme-draft';
        `;
        expect(auditGeneratedCode(code, {filePath})).toEqual([]);
    });

    it("rejects relative imports escaping generated_tests", () => {
        const code = `import x from '../../../lib/model_clients/preflight';`;
        const violations = auditGeneratedCode(code, {filePath});
        expect(violations).toHaveLength(1);
        expect(violations[0]).toContain("outside generated_tests");
    });

    it("rejects eval and the Function constructor", () => {
        const code = `
            eval("2+2");
            const f = new Function("return 1");
        `;
        expect(auditGeneratedCode(code, {filePath})).toHaveLength(2);
    });

    it("rejects non-literal require and dynamic import", () => {
        const code = `
            const name = 'child' + '_process';
            const cp = require(name);
            const mod = await import(name);
        `;
        expect(auditGeneratedCode(code, {filePath})).toHaveLength(2);
    });
});
