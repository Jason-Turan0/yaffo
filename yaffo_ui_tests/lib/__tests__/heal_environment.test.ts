import {describe, expect, it, jest} from "@jest/globals";
import {readFileSync} from "node:fs";
import {delimiter, join} from "node:path";

import {environmentTestRunner} from "@lib/test_generator/heal_test";
import {GeneratedTestResponseSchema} from "@lib/test_generator/prompt/json_parser";
import {IsolatedEnvironment, TestRunResult} from "@lib/services/isolated_runner";
import {PlaywrightTestRunner} from "@lib/services/run_playwright_tests";
import {YAFFO_PROJECT_ROOT} from "@lib/types";

describe("healer Playwright environment", () => {
    it("binds the isolated database, provider fakes and Python interpreter on every run", async () => {
        const result: TestRunResult = {
            success: true, exitCode: 0, output: "",
            summary: {total: 1, passed: 1, failed: 0, skipped: 0}, tests: [],
        };
        const runTests = jest.fn<PlaywrightTestRunner>().mockResolvedValue(result);
        const environment: IsolatedEnvironment = {
            tempDir: "/tmp/healing-data", port: 5023, baseUrl: "http://127.0.0.1:5023",
            flaskProcess: null, taskqProcess: null, cleanup: async () => {},
            fakeFlickrUrl: "http://127.0.0.1:5025",
            fakeOneDriveUrl: "http://127.0.0.1:5026",
            fakeImmichUrl: "http://127.0.0.1:5027",
        };
        const runner = environmentTestRunner(environment, runTests);
        const options = {reporters: "json", environment: {YAFFO_DATA_DIR: "/wrong-data", CI: "true"}};

        await runner(environment.baseUrl, ["service.spec.ts"], options);
        await runner(environment.baseUrl, ["service.spec.ts"]);

        expect(runTests).toHaveBeenCalledTimes(2);
        for (const [baseUrl, files, runOptions] of runTests.mock.calls) {
            expect(baseUrl).toBe(environment.baseUrl);
            expect(files).toEqual(["service.spec.ts"]);
            expect(runOptions?.environment).toMatchObject({
                YAFFO_DATA_DIR: environment.tempDir,
                FAKE_FLICKR_URL: environment.fakeFlickrUrl,
                FAKE_ONEDRIVE_URL: environment.fakeOneDriveUrl,
                FAKE_IMMICH_URL: environment.fakeImmichUrl,
                PEER_URL: "",
            });
            expect(runOptions?.environment?.PATH?.split(delimiter)[0]).toBe(join(YAFFO_PROJECT_ROOT, "venv", "bin"));
        }
        expect(runTests.mock.calls[0][2]?.reporters).toBe("json");
        expect(runTests.mock.calls[0][2]?.environment?.CI).toBe("true");
        expect(options.environment.YAFFO_DATA_DIR).toBe("/wrong-data");
    });

    it("loads the Flickr suite metadata using the healer's filename contract", () => {
        const outputDir = join(process.cwd(), "generated_tests", "sync_services");
        const metadata = GeneratedTestResponseSchema.parse(JSON.parse(
            readFileSync(join(outputDir, "sync_services.json"), "utf8"),
        ));
        expect(metadata.files).toHaveLength(1);
        const file = metadata.files[0];
        expect(file.filename).toBe("sync_services.spec.ts");
        expect(file.code).toBe(readFileSync(join(outputDir, file.filename), "utf8"));
    });
});
