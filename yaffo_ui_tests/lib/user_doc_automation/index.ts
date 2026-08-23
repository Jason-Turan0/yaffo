/**
 * Framework index for the user-guide automation.
 *
 * Generated walkthroughs do not import this — they use
 * `user_doc_automation/_support`, a deliberately small surface.
 */
export {defineWalkthrough} from "./types";
export type {FlowContext, RowRule, Shot, Viewport, Walkthrough} from "./types";
export {settle} from "./settle";
export {PAD, paddedBox, resolveClip, resolveIgnoreRegions, rowCut} from "./framing";
export type {Box} from "./framing";
export {toWebp, WEBP_QUALITY} from "./encode";
export {compareShots} from "./compare";
export type {CompareResult, CompareStatus} from "./compare";
export {supportScript, VENV_PYTHON} from "./python";
export {createObserver, PAGE_HEADER, RUN_HEADER, takeServerObservation} from "./observe";
export type {Observation, Observer, ServerObservation} from "./observe";
export {captureWalkthroughs, processResults, runWalkthroughs, RAW_FILENAME} from "./runner";
export type {CaptureOptions, ProcessOptions, RawResult, RawShot, RunOptions, ShotResult, WalkthroughResult} from "./runner";
export {loadWalkthroughs} from "./load";
export {buildCaptureArgs, containerBaseUrl, dockerAvailable, runCaptureContainer, snapshotDockerEnv, DOCS_CAPTURE_IMAGE} from "./docker";
export type {CaptureContainerOptions} from "./docker";
export {buildEvidence} from "./evidence";
export type {Evidence, EvidenceOptions} from "./evidence";
export {openSession, triageShot, TriageSchema, TRIAGE_CLASSES} from "./triage";
export type {Session, Triage, TriageOptions, TriageSession} from "./triage";
export {applyFix, FixSchema} from "./fix";
export type {Fix, FixOptions, FixResult} from "./fix";
