import {createHash} from "crypto";
import {existsSync, readFileSync, writeFileSync} from "fs";
import {join} from "path";
import type {LayoutLine} from "./layout";
import type {ShotResult} from "./runner";

/**
 * A shot's entry in its page's lockfile: the committed image's dimensions and hash,
 * and the text and controls it shows. The layout is recorded from the same capture
 * the image came from, so it always describes the image beside it.
 */
export interface LockShot {
    width: number;
    height: number;
    sha256: string | null;
    layout?: LayoutLine[];
}

export const sha256Of = (path: string): string | null =>
    existsSync(path) ? createHash("sha256").update(readFileSync(path)).digest("hex") : null;

export const lockShotEntry = (shot: ShotResult, guideDir: string): LockShot => ({
    width: shot.width,
    height: shot.height,
    // Detects a committed screenshot being replaced outside this pipeline, and so a
    // layout that no longer describes it. The staleness comparison itself is
    // per-pixel and per-element, not by hash.
    sha256: sha256Of(join(guideDir, shot.target)),
    ...(shot.layout ? {layout: shot.layout} : {}),
});

export const lockPath = (contentDir: string, page: string): string => {
    const [area, name] = [page.slice(0, page.indexOf("/")), page.slice(page.indexOf("/") + 1)];
    return join(contentDir, area, name, `${name}.lock.json`);
};

/**
 * Record one promoted shot in an existing lockfile, leaving the rest alone. For
 * promotions made without a verification capture (the deterministic heal gates),
 * which would otherwise leave the lock describing the image they replaced. The
 * page's lastVerifiedSha is not advanced: only a full capture verifies a page.
 */
export const recordPromotedShot = (contentDir: string, guideDir: string, page: string, shot: ShotResult): void => {
    const path = lockPath(contentDir, page);
    if (!existsSync(path)) return;
    const lock = JSON.parse(readFileSync(path, "utf8")) as {shots?: Record<string, LockShot>};
    lock.shots = {...(lock.shots ?? {}), [shot.target]: lockShotEntry(shot, guideDir)};
    writeFileSync(path, JSON.stringify(lock, null, 4) + "\n");
};

/** The committed shot's entry, when the lock still describes the committed image. */
export const committedLockShot = (
    contentDir: string, guideDir: string, page: string, target: string
): LockShot | null => {
    const path = lockPath(contentDir, page);
    if (!existsSync(path)) return null;
    const entry = (JSON.parse(readFileSync(path, "utf8")) as {shots?: Record<string, LockShot>}).shots?.[target];
    if (!entry || !entry.sha256 || entry.sha256 !== sha256Of(join(guideDir, target))) return null;
    return entry;
};
