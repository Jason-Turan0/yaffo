import {afterEach, describe, expect, it} from "@jest/globals";
import {resolve} from "path";
import {captureEnv, scrubProcessEnv} from "../env";
import {supportScript, VENV_PYTHON} from "../python";
import {defineWalkthrough} from "../types";

const originalEnv = {...process.env};

afterEach(() => {
    for (const key of Object.keys(process.env)) delete process.env[key];
    Object.assign(process.env, originalEnv);
});

describe("captureEnv", () => {
    it("copies only allowlisted variables and blocks credentials", () => {
        const source = {
            PATH: "/usr/bin",
            DOCS_BASE_URL: "http://127.0.0.1:5002",
            YAFFO_DOCS_DATA_DIR: "/private/tmp/yaffo-docs",
            ANTHROPIC_API_KEY: "secret",
            GITHUB_TOKEN: "secret",
            UNRELATED: "value",
        };

        expect(captureEnv(source)).toEqual({
            PATH: "/usr/bin",
            DOCS_BASE_URL: "http://127.0.0.1:5002",
            YAFFO_DOCS_DATA_DIR: "/private/tmp/yaffo-docs",
            SKIP_DOTENV: "1",
        });
    });

    it("lets explicit additions override an inherited value", () => {
        expect(captureEnv(
            {DOCS_BASE_URL: "http://old"},
            {DOCS_BASE_URL: "http://new", DOCS_CAPTURE_DIR: "/captures"}
        )).toMatchObject({
            DOCS_BASE_URL: "http://new",
            DOCS_CAPTURE_DIR: "/captures",
            SKIP_DOTENV: "1",
        });
    });

    it("does not mutate the source environment", () => {
        const source = {PATH: "/usr/bin", SECRET: "still-here"};
        captureEnv(source);
        expect(source).toEqual({PATH: "/usr/bin", SECRET: "still-here"});
    });
});

describe("scrubProcessEnv", () => {
    it("removes ambient secrets in place before generated code runs", () => {
        process.env.PATH = "/safe/bin";
        process.env.ANTHROPIC_API_KEY = "secret";
        process.env.UNRELATED = "remove-me";

        scrubProcessEnv({DOCS_BASE_URL: "http://docs"});

        expect(process.env.PATH).toBe("/safe/bin");
        expect(process.env.DOCS_BASE_URL).toBe("http://docs");
        expect(process.env.SKIP_DOTENV).toBe("1");
        expect(process.env.ANTHROPIC_API_KEY).toBeUndefined();
        expect(process.env.UNRELATED).toBeUndefined();
    });
});

describe("Python support paths", () => {
    it("resolves the project virtualenv from the UI-test working directory", () => {
        expect(VENV_PYTHON).toBe(resolve(process.cwd(), "..", "venv", "bin", "python"));
    });

    it("resolves helper scripts beside the automation modules", () => {
        expect(supportScript("imagediff.py")).toBe(
            resolve(process.cwd(), "lib", "user_doc_automation", "imagediff.py")
        );
    });
});

describe("defineWalkthrough", () => {
    it("preserves the exact walkthrough object", () => {
        const walkthrough = {page: "library/browsing", shots: {}};
        expect(defineWalkthrough(walkthrough)).toBe(walkthrough);
    });
});
