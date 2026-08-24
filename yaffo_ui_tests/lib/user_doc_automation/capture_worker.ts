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
import {BASE_URL, CAPTURE_DIR, CONTENT_DIR, DOCS_DATA_DIR} from "./paths";
import {resolve} from "path";
import {pathToFileURL} from "url";


// Before any walkthrough is imported: walkthroughs are model-generated code, and
// nothing they run should be able to read a provider key out of the environment.
// The container narrows this further — it is handed an allowlist to begin with — but
// this stays so a host-side run is confined the same way.
scrubProcessEnv({DOCS_BASE_URL: BASE_URL, YAFFO_DOCS_DATA_DIR: DOCS_DATA_DIR});


export const main = async (args: string[] = process.argv.slice(2)): Promise<number> => {
    const {loadWalkthroughs} = await import("./load");
    const {captureWalkthroughs} = await import("./runner");

    const only = args.filter((a) => !a.startsWith("-"));
    const walkthroughs = await loadWalkthroughs(CONTENT_DIR, only);
    if (!walkthroughs.length) {
        console.error(only.length ? `No walkthrough for: ${only.join(", ")}` : "No walkthroughs found");
        return 1;
    }

    console.log(`Capturing ${walkthroughs.length} walkthrough(s) from ${BASE_URL}`);
    const results = await captureWalkthroughs(walkthroughs, {
        baseUrl: BASE_URL, stagingDir: CAPTURE_DIR,
    });

    let failed = 0;
    for (const result of results) {
        console.log(`  ${result.page}: ${result.shots.length} shot(s)`);
        if (result.error) {
            failed++;
            console.error(`  ! ${result.error}`);
        }
    }
    return failed ? 1 : 0;
};

const isDirectRun = process.argv[1] !== undefined &&
    import.meta.url === pathToFileURL(resolve(process.argv[1])).href;

export const runCli = async (args: string[] = process.argv.slice(2)): Promise<void> => {
    try {
        process.exitCode = await main(args);
    } catch (e) {
        console.error(e);
        process.exitCode = 1;
    }
};

if (isDirectRun) void runCli();
