import {describe, expect, it} from "@jest/globals";
import {readFileSync} from "node:fs";
import {join} from "node:path";
import {environmentWithoutProviderKeys, PROVIDER_KEY_ENV_VARS} from "@lib/services/isolated_runner";

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
