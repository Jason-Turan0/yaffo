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
export type {FlowContext, RowRule, Shot, Viewport, Walkthrough} from "@lib/user_doc_automation/types";
