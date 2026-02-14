#!/usr/bin/env node
import { readFileSync } from "fs";
import { resolve } from "path";

const castFile = process.argv[2];
if (!castFile) {
  console.error("Usage: node replay_cast.mjs <file.cast> [--speed <multiplier>]");
  process.exit(1);
}

const speedIdx = process.argv.indexOf("--speed");
const speed = speedIdx !== -1 ? parseFloat(process.argv[speedIdx + 1]) : 1;
const maxDelay = 3000;

const lines = readFileSync(resolve(castFile), "utf-8").split("\n");
const headerLine = lines.find((l) => l.trimStart().startsWith("{"));
const header = JSON.parse(headerLine);

if (header.term) {
  process.stdout.write(`\x1b[8;${header.term.rows};${header.term.cols}t`);
}

const events = lines
  .filter((l) => l.trimStart().startsWith("["))
  .reduce((acc, line) => {
    try { acc.push(JSON.parse(line)); } catch {}
    return acc;
  }, []);

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function replay() {
  for (const event of events) {
    const [delay, type, data] = event;
    if (type === "o") {
      const ms = Math.min(delay * 1000 / speed, maxDelay);
      if (ms > 0) await sleep(ms);
      process.stdout.write(data);
    }
  }
  process.stdout.write("\n");
}

replay().catch((e) => {
  console.error(e);
  process.exit(1);
});