/**
 * What a walkthrough is allowed to see.
 *
 * Walkthroughs are model-generated code that this harness executes, so the process
 * running them is given an explicit allowlist rather than the ambient environment —
 * the same rule `run_playwright_tests.ts` applies to generated specs. Provider API
 * keys, CI credentials, and anything else that happens to be exported must not be
 * reachable from code a model wrote.
 *
 * Note what is deliberately absent: `ANTHROPIC_API_KEY`, `DEEPSEEK_API_KEY`,
 * `GOOGLE_GENERATIVE_AI_API_KEY`, `GITHUB_TOKEN`. The agent needs those; the code it
 * writes does not.
 */
export const CAPTURE_ENV_ALLOWLIST = [
    // Enough to start node, resolve binaries, and write temp files.
    "PATH", "HOME", "SHELL", "TMPDIR", "USER", "LOGNAME",
    // Locale and terminal, so output is legible and formatting is stable.
    "LANG", "LC_ALL", "TERM",
    // Where the browsers live, and whether this is CI.
    "PLAYWRIGHT_BROWSERS_PATH", "CI",
    // The sandbox this run resolved to, so Playwright knows whether Chromium can
    // nest its own.
    "TEST_SANDBOX",
    // Which instance to capture against, and where the guide is.
    "DOCS_BASE_URL", "GUIDE_DIR",
];

/** The allowlisted subset of an environment, plus any explicit additions. */
export const captureEnv = (
    source: NodeJS.ProcessEnv = process.env,
    extra: Record<string, string> = {}
): NodeJS.ProcessEnv => {
    const env: NodeJS.ProcessEnv = {};
    for (const key of CAPTURE_ENV_ALLOWLIST) {
        if (source[key] !== undefined) env[key] = source[key];
    }
    // Stops dotenv-aware entry points re-loading .env — and the keys in it — inside a
    // process that runs generated code. Mirrors the test runner's use of the same flag.
    env.SKIP_DOTENV = "1";
    return {...env, ...extra};
};

/**
 * Narrow this process's own environment to the allowlist, in place.
 *
 * Used by the capture entry point before any walkthrough is imported: from that point
 * on there is nothing in `process.env` worth stealing. This is a containment measure,
 * not a security boundary — code determined to read `.env` from disk still can, which
 * is what the filesystem sandbox and the container are for.
 */
export const scrubProcessEnv = (extra: Record<string, string> = {}): void => {
    const kept = captureEnv(process.env, extra);
    for (const key of Object.keys(process.env)) {
        if (!(key in kept)) delete process.env[key];
    }
    Object.assign(process.env, kept);
};
