/**
 * Duplicate-review walkthrough — docs/guide/organize-review/duplicates.md
 *
 * The docs fixture stages two real-photo pairs outside the indexed library.
 * Scans are real and read-only. The final removal POST is fulfilled in
 * Playwright, so no fixture file is trashed, moved, or deleted.
 */
import type {Page} from "@playwright/test";
import {defineWalkthrough, docsFixturePath} from "../../_support";

const DUPLICATE_SAMPLES = docsFixturePath("Duplicate Scan Samples");
const UNIQUE_SAMPLES = docsFixturePath("Family Photos", "2015_daughter_baby");
const REVIEW_DESTINATION = docsFixturePath("Duplicate Review");

const stabilizeShot = async (page: Page): Promise<void> => {
    // A click leaves the mouse at the old viewport coordinates after navigation,
    // which can hover (and lift) a result card. Extra bottom space also keeps an
    // element clip from touching Chromium's document boundary and losing its shadow.
    await page.mouse.move(0, 0);
    await page.addStyleTag({content: "body { padding-bottom: 24px !important; }"});
};

const deleteScanJob = async (page: Page, jobId: string): Promise<void> => {
    const csrfToken = await page.evaluate(() =>
        (window as typeof window & {APP_CONFIG: {csrfToken: string}}).APP_CONFIG.csrfToken
    );
    const response = await page.request.post(`/jobs/${jobId}/delete`, {
        form: {has_results: "true"},
        headers: {"X-CSRF-Token": csrfToken},
    });
    if (!response.ok() && response.status() !== 404) {
        throw new Error(`Could not clean up duplicate scan ${jobId}: HTTP ${response.status()}`);
    }
};

const cleanUpPriorScans = async (page: Page): Promise<void> => {
    const jobIds = await page.locator("#job-progress-section [id^=job-]")
        .evaluateAll((nodes) => nodes.map((node) => node.id.replace(/^job-/, "")));
    for (const jobId of jobIds) await deleteScanJob(page, jobId);
    await page.locator("#job-progress-section").evaluate((section) => section.remove()).catch(() => undefined);
};

const waitForResultWidgets = async (page: Page): Promise<void> => {
    await page.locator("#action-type[data-searchable-initialized] + .searchable-select-wrapper").waitFor();
    await page.locator(
        "select[name=page-size][data-searchable-initialized] + .searchable-select-wrapper"
    ).waitFor();
    await page.evaluate(() => new Promise<void>((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(() => resolve()))
    ));
};

const totalMedia = (page: Page) => page.locator("#remove-duplicates-form .stat-card")
    .filter({hasText: "Total Media"})
    .locator(".stat-value");

const configureDirectory = async (
    page: Page,
    directory: string,
    expectedMedia: number
): Promise<void> => {
    await page.locator("#add-directory-button").click();
    const input = page.locator("#remove-duplicates-form input[name=directory]").first();
    await input.waitFor();
    await input.fill(directory);
    await input.dispatchEvent("change");
    await page.waitForFunction((expected) => {
        const cards = Array.from(document.querySelectorAll("#remove-duplicates-form .stat-card"));
        const total = cards.find((card) => card.textContent?.includes("Total Media"));
        return total?.querySelector(".stat-value")?.textContent?.trim() === expected;
    }, String(expectedMedia));
    const count = (await totalMedia(page).textContent())?.trim();
    if (count !== String(expectedMedia)) {
        throw new Error(`Expected ${expectedMedia} scan files in ${directory}, found ${count ?? "none"}`);
    }
};

const waitForCompletedJob = async (page: Page, jobId: string): Promise<void> => {
    for (let attempt = 0; attempt < 240; attempt += 1) {
        const response = await page.request.get(`/jobs/${jobId}/status`);
        if (response.ok()) {
            const job = await response.json() as {status?: string};
            if (job.status === "COMPLETED") return;
            if (job.status === "FAILED" || job.status === "CANCELLED") {
                throw new Error(`Duplicate scan ${jobId} ended as ${job.status}`);
            }
        }
        await page.waitForTimeout(500);
    }
    throw new Error(`Duplicate scan ${jobId} did not complete`);
};

const startScan = async (
    page: Page,
    expectGroups: boolean
): Promise<string> => {
    const cards = page.locator("#job-progress-section [id^=job-]");
    const existing = new Set(await cards.evaluateAll((nodes) => nodes.map((node) => node.id)));
    await page.locator("#find-duplicates-button").click();
    await page.waitForFunction((known) =>
        Array.from(document.querySelectorAll<HTMLElement>("#job-progress-section [id^=job-]"))
            .some((node) => !known.includes(node.id)),
    [...existing]);
    const current = await cards.evaluateAll((nodes) => nodes.map((node) => node.id));
    const cardId = current.find((id) => !existing.has(id));
    if (!cardId) throw new Error("The duplicate scan did not create a job card");
    const jobId = cardId.replace(/^job-/, "");
    await waitForCompletedJob(page, jobId);

    await page.goto(`/utilities/remove-duplicates/results/${jobId}`, {waitUntil: "domcontentloaded"});
    await page.locator(expectGroups ? "#duplicates-form" : ".empty-state").waitFor();
    return jobId;
};

const pickAction = async (page: Page, label: string): Promise<void> => {
    const wrapper = page.locator("#action-type + .searchable-select-wrapper");
    await wrapper.locator(".searchable-select-display").click();
    const response = page.waitForResponse((candidate) =>
        candidate.url().includes("/utilities/remove-duplicates/action-change/")
    );
    await wrapper.locator(".searchable-select-option").filter({hasText: label}).click();
    await response;
};

export default defineWalkthrough({
    page: "organize-review/duplicates",

    shots: {
        "utilities-remove-duplicates.webp": {
            viewport: {width: 1400, height: 1000},
            goto: "/utilities/remove-duplicates",
            clip: "#remove-duplicates-form",
            // The real canonical path remains visible, but macOS spells the shared
            // temp root /private/tmp while Linux spells it /tmp.
            ignoreRegions: ["input[name=directory]"],
            setup: async (page) => {
                await cleanUpPriorScans(page);
                await configureDirectory(page, DUPLICATE_SAMPLES, 4);
                await stabilizeShot(page);
            },
        },
        "duplicate-results.webp": {
            viewport: {width: 1400, height: 1800},
            goto: "/utilities/remove-duplicates",
            clip: ".utility-page",
            setup: async (page) => {
                await cleanUpPriorScans(page);
                await configureDirectory(page, DUPLICATE_SAMPLES, 4);
                await startScan(page, true);
                await page.locator(".duplicate-group").nth(1).waitFor();
                await waitForResultWidgets(page);
                await stabilizeShot(page);
            },
        },
    },

    flows: async ({page, visit}) => {
        await visit("/utilities/remove-duplicates");
        await cleanUpPriorScans(page);
        await configureDirectory(page, DUPLICATE_SAMPLES, 4);

        // The shared folder picker can populate the same directory input; open
        // and cancel it so the hand-entered deterministic path stays selected.
        await page.locator("#remove-duplicates-form .file-browser-btn").click();
        await page.locator("#folder-picker-modal.active").waitFor();
        await page.locator("#folder-picker-cancel").click();

        // Explicit rescan refreshes the same count before the background scan.
        const rescan = page.waitForResponse((response) =>
            response.url().includes("/utilities/remove-duplicates-form")
        );
        await page.locator("#rescan-directories").click();
        await rescan;
        const duplicateJobId = await startScan(page, true);

        // Toggle a default-selected copy off and on again. The card and header
        // are both returned by the real HTMX endpoint.
        const selected = page.locator(".duplicate-group").first().locator(".photo-card.selected").first();
        const selectedId = await selected.getAttribute("id");
        if (!selectedId) throw new Error("The selected duplicate card has no id");
        await selected.click();
        await page.locator(`#${selectedId}:not(.selected)`).waitFor();
        await page.locator(`#${selectedId}`).click();
        await page.locator(`#${selectedId}.selected`).waitFor();

        // Move-to-folder reveals and round-trips the destination control.
        await pickAction(page, "Move to Folder");
        const destination = page.locator("#destination-folder");
        await destination.fill(REVIEW_DESTINATION);
        await destination.blur();
        await page.locator("#destination-folder").waitFor();
        if (await page.locator("#destination-folder").inputValue() !== REVIEW_DESTINATION) {
            throw new Error("The duplicate destination did not survive its HTMX update");
        }

        // The real button has no confirmation step. Observe its request locally
        // without starting a removal job or changing any files.
        await page.route("**/utilities/remove-duplicates/execute/*", async (route) => {
            await route.fulfill({status: 204});
        });
        const execute = page.waitForRequest((request) =>
            request.url().includes("/utilities/remove-duplicates/execute/")
        );
        await page.locator("#duplicates-header .btn-danger").click();
        await execute;
        await page.unroute("**/utilities/remove-duplicates/execute/*");

        // A one-file directory deterministically reaches the real empty-results
        // branch and proves that indexing is not required for a scan.
        await visit("/utilities/remove-duplicates");
        await configureDirectory(page, UNIQUE_SAMPLES, 1);
        const emptyJobId = await startScan(page, false);
        await deleteScanJob(page, duplicateJobId);
        await deleteScanJob(page, emptyJobId);
    },
});
