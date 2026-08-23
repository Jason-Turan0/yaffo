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
export {runWalkthroughs} from "./runner";
export type {ShotResult, WalkthroughResult} from "./runner";
export {buildEvidence} from "./evidence";
export type {Evidence, EvidenceOptions} from "./evidence";
export {triageShot, TriageSchema, TRIAGE_CLASSES} from "./triage";
export type {Triage, TriageOptions, TriageSession} from "./triage";
export {applyFix, FixSchema} from "./fix";
export type {Fix, FixOptions, FixResult} from "./fix";
