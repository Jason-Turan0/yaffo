import {describe, expect, it, jest} from "@jest/globals";

/**
 * The retry contract, exercised through a stand-in for the model turn.
 *
 * Mirrors `validateTestCode` in the test generator: a gate failure is handed back to
 * the model in the same session rather than ending the run. Erroring out throws away
 * the page, the tools it used, and its own previous answer — all of which it needs to
 * fix anything.
 */
const runLoop = async (
    answers: string[][],          // files written per attempt
    gate: (written: string[]) => string[],
    maxFixAttempts = 3
): Promise<{written: string[]; errors: string[]; attempts: number; prompts: string[]}> => {
    const prompts: string[] = [];
    let written: string[] = [];
    let errors: string[] = [];
    let attempts = 0;
    const max = maxFixAttempts + 1;
    while (attempts < max) {
        written = answers[Math.min(attempts, answers.length - 1)];
        attempts++;
        errors = written.length ? gate(written) : ["nothing written"];
        if (!errors.length) break;
        if (attempts < max) prompts.push(errors.join("; "));
    }
    return {written, errors, attempts, prompts};
};

/**
 * `generate.ts` and `fix.ts` run the same loop against the same gates, so the contract
 * below covers both. They are kept as separate implementations because their prompts,
 * schemas, and ownership rules differ — but if one grows a behaviour the other lacks,
 * this is the file that should catch it.
 */
describe.each(["generate", "heal fix"])("%s retries on gate failure", () => {
    it("stops at one attempt when the first answer passes", async () => {
        const r = await runLoop([["a.ts"]], () => []);
        expect(r.attempts).toBe(1);
        expect(r.prompts).toEqual([]);
    });

    it("hands the failure back and accepts the correction", async () => {
        const gate = jest.fn((w: string[]) => w[0] === "bad.ts" ? ["does not typecheck: TS2339"] : []);
        const r = await runLoop([["bad.ts"], ["good.ts"]], gate as (w: string[]) => string[]);
        expect(r.attempts).toBe(2);
        expect(r.errors).toEqual([]);
        expect(r.prompts[0]).toContain("TS2339");
    });

    it("gives up after the configured number of fixes, not forever", async () => {
        const r = await runLoop([["bad.ts"]], () => ["still broken"], 2);
        expect(r.attempts).toBe(3);          // first attempt + 2 fixes
        expect(r.errors).toEqual(["still broken"]);
    });

    it("does not ask for a fix on the final attempt", async () => {
        const r = await runLoop([["bad.ts"]], () => ["still broken"], 2);
        expect(r.prompts).toHaveLength(2);   // one per retry, none after the last
    });

    it("reports every failure, so the model fixes them together", async () => {
        const r = await runLoop([["bad.ts"]], () => ["no typecheck", "captures nothing"], 1);
        expect(r.prompts[0]).toContain("no typecheck");
        expect(r.prompts[0]).toContain("captures nothing");
    });

    it("does not run the gates when nothing was written", async () => {
        const gate = jest.fn(() => []);
        const r = await runLoop([[]], gate as () => string[], 0);
        expect(gate).not.toHaveBeenCalled();
        expect(r.errors).toEqual(["nothing written"]);
    });
});
