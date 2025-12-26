import {existsSync, readFileSync} from "fs";
import {parse as parseYaml} from "yaml";
import {Spec} from "@lib/test_generator/spec_parser.types";

export const parseSpecFile = (specPath: string): Spec => {
    // Read and parse spec
    if (!existsSync(specPath)) {
        throw new Error(`Spec file does not exist: ${specPath}`);
    }
    const specContent = readFileSync(specPath, "utf-8");
    const spec = parseYaml(specContent);
    return spec;
}