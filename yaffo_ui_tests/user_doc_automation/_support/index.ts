/**
 * The import surface for generated walkthroughs.
 *
 * Walkthroughs are generated code, so they depend on a small stable local path rather
 * than reaching into the framework — the same role `generated_tests/_support` plays
 * for generated specs. Everything here is re-exported from `lib/user_doc_automation`,
 * where the infrastructure actually lives.
 *
 * Helpers shared across walkthroughs belong here too, once any exist.
 */
export {defineWalkthrough} from "@lib/user_doc_automation/types";
export type {FlowContext, GotoContext, RowRule, Shot, Viewport, Walkthrough} from "@lib/user_doc_automation/types";
/**
 * Pin a media item by filename rather than by id — ids are assigned at index time and
 * change on every reseed. The same fixture file the Playwright specs pin against.
 */
export {PRIMARY_DETAIL_IMAGE} from "@lib/user_doc_automation/sandbox_facts";
