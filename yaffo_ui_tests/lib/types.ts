import {Spec} from "@lib/test_generator/prompt/spec_parser.types";
import {dirname, join, resolve} from "path";
import {fileURLToPath} from "url";
const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

export type FailureClassification = 'regression' | 'flaky' | 'superficial';

export interface FailureContext {
  testFile: string;
  scenarioName: string;
  stepIndex: number;
  error: string;
  screenshot?: string;
  domSnapshot?: string;
  expectedSelector?: string;
  originalSpec: Spec;
}

export interface AnalysisResult {
  classification: FailureClassification;
  confidence: number;
  explanation: string;
  healingSuggestion?: HealingSuggestion;
}

export interface HealingSuggestion {
  originalSelector: string;
  healedSelector: string;
  patchCode: string;
}

export interface HealingLogEntry {
  timestamp: string;
  test: string;
  scenario: string;
  classification: FailureClassification;
  originalSelector: string;
  healedSelector: string;
  confidence: number;
  recommendation: string;
}

export interface GenerationMetadata {
  specPath: string;
  specHash: string;
  generatedAt: string;
  model: string;
  domContextHash?: string;
}

export const YAFFO_PROJECT_ROOT = resolve(join(__dirname, "../.."));
export const YAFFO_APP_ROOT = resolve(join(YAFFO_PROJECT_ROOT, "yaffo"));
export const GENERATED_TESTS_ROOT = resolve(join(YAFFO_PROJECT_ROOT, "yaffo_ui_tests", "generated_tests"));
export const YAFFO_ROOT = YAFFO_APP_ROOT;
