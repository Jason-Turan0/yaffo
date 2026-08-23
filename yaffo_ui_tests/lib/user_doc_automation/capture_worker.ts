/**
 * The capture half, as its own process. This is what runs inside the container.
 *
 *   npx tsx lib/user_doc_automation/capture_worker.ts [page ...]
 *
 * It drives the browser and leaves PNGs plus `raw.json` in the staging directory.
 * It deliberately does no encoding, comparison, or promotion: those need Pillow and
 * NumPy from the project virtualenv, which is not in the Playwright image. The host
 * reads `raw.json` back out of the shared staging mount and finishes the job.
 *
 * Runnable directly on the host too — `docs:capture --docker` and a plain
 * `docs:capture` execute the same code, which is the point. The container changes
 * where it runs, not what it does.
 */
import {scrubProcessEnv} from "./env";
import {BASE_URL, CONTENT_DIR, STAGING_DIR} from "./paths";


// Before any walkthrough is imported: walkthroughs are model-generated code, and
// nothing they run should be able to read a provider key out of the environment.
// The container narrows this further — it is handed an allowlist to begin with — but
// this stays so a host-side run is confined the same way.
scrubProcessEnv({DOCS_BASE_URL: BASE_URL});


const main = async (): Promise<void> => {
    const {loadWalkthroughs} = await import("./load");
    const {captureWalkthroughs} = await import("./runner");

    const only = process.argv.slice(2).filter((a) => !a.startsWith("-"));
    const walkthroughs = await loadWalkthroughs(CONTENT_DIR, only);
    if (!walkthroughs.length) {
        console.error(only.length ? `No walkthrough for: ${only.join(", ")}` : "No walkthroughs found");
        process.exit(1);
    }

    console.log(`Capturing ${walkthroughs.length} walkthrough(s) from ${BASE_URL}`);
    const results = await captureWalkthroughs(walkthroughs, {
        baseUrl: BASE_URL, stagingDir: STAGING_DIR,
    });

    let failed = 0;
    for (const result of results) {
        console.log(`  ${result.page}: ${result.shots.length} shot(s)`);
        if (result.error) {
            failed++;
            console.error(`  ! ${result.error}`);
        }
    }
    if (failed) process.exit(1);
};

main().catch((e) => {
    console.error(e);
    process.exit(1);
});
