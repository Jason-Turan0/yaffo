import {execFileSync} from "child_process";
import {createHash} from "crypto";
import {existsSync, readFileSync} from "fs";
import {join, resolve} from "path";

export interface DependencyObservation {
    routes?: string[];
    templates?: string[];
    static?: string[];
}

export interface PageLock {
    lastVerifiedSha?: string | null;
    observed?: DependencyObservation;
    dependencyHashes?: Record<string, string | null>;
}

export interface DependencyChange {
    path: string;
    before: string | null | undefined;
    after: string | null;
}

const REPO = resolve(process.cwd(), "..");

/** Everything a page says it depends on, observed or hand-declared. */
export const pageDependencies = (
    observation: DependencyObservation | undefined,
    alsoDependsOn: string[] = []
): string[] => [...new Set([
    ...(observation?.routes ?? []),
    ...(observation?.templates ?? []),
    ...(observation?.static ?? []),
    ...alsoDependsOn,
])].sort();

const hashFile = (path: string): string | null =>
    existsSync(path)
        ? createHash("sha256").update(readFileSync(path)).digest("hex")
        : null;

/** Snapshot dependency contents when a successful capture writes its lockfile. */
export const dependencyHashes = (
    observation: DependencyObservation | undefined,
    alsoDependsOn: string[] = [],
    repoDir = REPO
): Record<string, string | null> => Object.fromEntries(
    pageDependencies(observation, alsoDependsOn)
        .map((path) => [path, hashFile(join(repoDir, path))])
);

/** Compare the lockfile snapshot with the checked-out feature branch. */
export const changedDependencies = (
    lock: PageLock,
    alsoDependsOn: string[] = [],
    repoDir = REPO
): DependencyChange[] => {
    const current = dependencyHashes(lock.observed, alsoDependsOn, repoDir);
    const previous = lock.dependencyHashes ?? {};
    return Object.entries(current)
        .filter(([path, hash]) => previous[path] !== hash)
        .map(([path, hash]) => ({path, before: previous[path], after: hash}));
};

/** Commit represented by a capture running in this checkout. */
export const currentHead = (repoDir = REPO): string =>
    execFileSync("git", ["rev-parse", "HEAD"], {cwd: repoDir, encoding: "utf8"}).trim();
