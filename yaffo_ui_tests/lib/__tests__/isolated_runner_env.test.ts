import {describe, expect, it} from "@jest/globals";
import {readFileSync} from "node:fs";
import {join} from "node:path";
import {
    environmentFileContents, environmentWithoutProviderKeys, IsolatedEnvironment, needsFakeSyncServices,
    PROVIDER_KEY_ENV_VARS, p2pEnv,
} from "@lib/services/isolated_runner";

describe("environmentWithoutProviderKeys", () => {
    it("drops every AI provider key and keeps everything else", () => {
        const env = environmentWithoutProviderKeys({
            PATH: "/usr/bin", HOME: "/home/me", ANTHROPIC_API_KEY: "sk-ant-real", DEEPSEEK_API_KEY: "sk-real",
        });
        expect(env).toEqual({PATH: "/usr/bin", HOME: "/home/me"});
    });

    it("names every key the app reads (yaffo/site_agents/model_clients/providers.py)", () => {
        const source = readFileSync(join(process.cwd(), "..", "yaffo/site_agents/model_clients/providers.py"), "utf8");
        const declared = [...source.matchAll(/Provider\(\s*"[^"]+",\s*"[^"]+",\s*"([A-Z_]+_API_KEY)"/g)].map(m => m[1]);
        expect(declared.length).toBeGreaterThan(0);
        expect([...PROVIDER_KEY_ENV_VARS].sort()).toEqual([...declared].sort());
    });
});

describe("the CI environment file", () => {
    it("starts the fakes only for sync specs", () => {
        expect(needsFakeSyncServices(["generated_tests/sync_automations/sync_automations.spec.ts"])).toBe(true);
        expect(needsFakeSyncServices(["generated_tests/sync_services/sync_services.spec.ts"])).toBe(true);
        expect(needsFakeSyncServices(["generated_tests/albums/albums.spec.ts"])).toBe(false);
    });

    it("lists the fake URLs and the data dir a separate test process needs", () => {
        const environment = {tempDir: "/tmp/yaffo_test_1", fakeImmichUrl: "http://127.0.0.1:7001"} as IsolatedEnvironment;
        expect(environmentFileContents(environment)).toBe(
            "FAKE_FLICKR_URL=\nFAKE_ONEDRIVE_URL=\nFAKE_IMMICH_URL=http://127.0.0.1:7001\nYAFFO_DATA_DIR=/tmp/yaffo_test_1\n");
    });
});


describe("sandbox P2P identities", () => {
    it("keeps test identities ephemeral and docs identities scoped to their persisted data directories", () => {
        expect(p2pEnv(5002)).toMatchObject({YAFFO_P2P_EPHEMERAL_IDENTITY: "1", YAFFO_P2P_PORT: "15002"});
        expect(p2pEnv(5002)).not.toHaveProperty("YAFFO_P2P_KEY_FILE");
        expect(p2pEnv(5002, "/tmp/docs")).toMatchObject({
            YAFFO_P2P_EPHEMERAL_IDENTITY: "0", YAFFO_P2P_KEY_FILE: join("/tmp/docs", "p2p-key.pem"),
        });
        expect(p2pEnv(5003, "/tmp/docs/peer")).toMatchObject({
            YAFFO_P2P_PORT: "15003", YAFFO_P2P_KEY_FILE: join("/tmp/docs/peer", "p2p-key.pem"),
        });
    });
});
