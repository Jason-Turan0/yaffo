import {existsSync, readFileSync} from "fs";
import {parse as parseYaml} from "yaml";
import {Spec, SpecSchema} from "@lib/test_generator/prompt/spec_parser.types";

export const parseSpecFile = (specPath: string): Spec => {
    if (!existsSync(specPath)) {
        throw new Error(`Spec file does not exist: ${specPath}`);
    }

    if (!specPath.endsWith(".yaml") && !specPath.endsWith(".yml")) {
        throw new Error(
            `Expected a YAML spec file but got: ${specPath}\n` +
            `Hint: Make sure you're passing the spec path (e.g., specs/feature.yaml).`
        );
    }

    const specContent = readFileSync(specPath, "utf-8");

    let parsed: unknown;
    try {
        parsed = parseYaml(specContent);
    } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        throw new Error(`Failed to parse YAML in ${specPath}: ${msg}`);
    }

    const result = SpecSchema.safeParse(parsed);
    if (!result.success) {
        const issues = result.error.issues
            .map(issue => `  - ${issue.path.join(".")}: ${issue.message}`)
            .join("\n");
        throw new Error(`Invalid spec file ${specPath}:\n${issues}`);
    }

    return result.data;
}
