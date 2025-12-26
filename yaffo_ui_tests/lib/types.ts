import {Spec} from "@lib/test_generator/spec_parser.types";

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
