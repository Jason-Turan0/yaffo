import {readdirSync, existsSync} from "fs";
import {join} from "path";
import {createInterface} from "readline/promises";
import {stdin, stdout} from "process";
import {main as captureDocs} from "../lib/user_doc_automation/docs_capture";

const contentDir = join(process.cwd(), "user_doc_automation");
const pages = readdirSync(contentDir, {withFileTypes: true})
    .filter((area) => area.isDirectory() && !area.name.startsWith("_") && !area.name.startsWith("."))
    .flatMap((area) => readdirSync(join(contentDir, area.name), {withFileTypes: true})
        .filter((page) => page.isDirectory() && existsSync(join(contentDir, area.name, page.name, `${page.name}.ts`)))
        .map((page) => `${area.name}/${page.name}`))
    .sort();

if (!pages.length) {
    console.error("No documentation walkthroughs found.");
    process.exit(1);
}

console.log("Documentation walkthroughs available to capture and promote:");
pages.forEach((page, index) => console.log(`  ${index + 1}. ${page}`));
const prompt = createInterface({input: stdin, output: stdout});
const answer = (await prompt.question("Select page numbers separated by commas, or 'all': ")).trim();
prompt.close();

const selected = answer.toLowerCase() === "all"
    ? pages
    : [...new Set(answer.split(",").map((value) => Number(value.trim())))].map((number) => pages[number - 1]);

if (!answer || selected.some((page) => !page)) {
    console.error("Choose valid page numbers, separated by commas, or enter 'all'.");
    process.exit(1);
}

console.log(`\nCapturing and promoting ${selected.length} page(s): ${selected.join(", ")}\n`);
process.exitCode = await captureDocs(["--docker", "--promote", ...selected]);
