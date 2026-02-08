import {Spec} from "@lib/test_generator/prompt/spec_parser.types";

export class SpecPromptGenerator {

    generateAllowedDirectories(allowedDirs: string[]): string[] {
        if (allowedDirs.length === 0) return [];
        return [
            "<filesystem_access>",
            "    <description>You have READ-ONLY access to these directories:</description>",
            ...allowedDirs.map(d => `    <directory>${d}</directory>`),
            "    <note>Use absolute paths when using the file tools</note>",
            "</filesystem_access>",
        ]
    }

    formatSpec(spec: Spec): string {
        const preconditionsSection = this.formatPreconditions(spec.preconditions);
        const dataSection = this.formatData(spec.data);
        const scenariosSection = this.formatScenarios(spec.scenarios);

        return [
            "<specification>",
            `    <feature>${spec.feature}</feature>`,
            `    <description>${spec.description}</description>`,
            preconditionsSection,
            dataSection,
            "    <scenarios>",
            scenariosSection,
            "    </scenarios>",
            "</specification>",
        ].filter(line => line !== "").join("\n");
    }

    private formatPreconditions(preconditions?: string[]): string {
        if (!preconditions || preconditions.length === 0) {
            return "";
        }
        return [
            "    <preconditions>",
            ...preconditions.map(p => `        <precondition>${p}</precondition>`),
            "    </preconditions>",
        ].join("\n");
    }

    private formatData(data?: string[]): string {
        if (!data || data.length === 0) {
            return "";
        }
        return [
            "    <data>",
            ...data.map(d => `        <item>${d}</item>`),
            "    </data>",
        ].join("\n");
    }

    private formatScenarios(scenarios: Spec["scenarios"]): string {
        return scenarios.map(s => {
            const cleanupSection = this.formatCleanup(s.cleanup);
            return [
                "        <scenario>",
                `            <name>${s.name}</name>`,
                `            <goal>${s.goal}</goal>`,
                `            <priority>${s.priority}</priority>`,
                "            <steps>",
                ...s.steps.map(step => `                <step>${step}</step>`),
                "            </steps>",
                "            <verify>",
                ...s.verify.map(v => `                <assertion>${v}</assertion>`),
                "            </verify>",
                cleanupSection,
                "        </scenario>",
            ].filter(line => line !== "").join("\n");
        }).join("\n");
    }

    private formatCleanup(cleanup?: string[]): string {
        if (!cleanup || cleanup.length === 0) {
            return "";
        }
        return [
            "            <cleanup>",
            ...cleanup.map(c => `                <step>${c}</step>`),
            "            </cleanup>",
        ].join("\n");
    }
}

export const specPromptGeneratorFactory = (): SpecPromptGenerator => {
    return new SpecPromptGenerator();
};