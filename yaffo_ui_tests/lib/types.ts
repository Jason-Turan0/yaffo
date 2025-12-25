import { z } from 'zod';

export const StepSchema = z.string().min(1);

export const ScenarioSchema = z.object({
  name: z.string().min(1),
  goal: z.string().min(1),
  priority: z.enum(['high', 'medium', 'low']).optional().default('medium'),
  steps: z.array(StepSchema).min(1),
  verify: z.array(z.string()).min(1),
});

export const TestDataSchema = z.record(z.unknown()).optional();

export const SpecSchema = z.object({
  feature: z.string().min(1),
  description: z.string().min(1),
  tags: z.array(z.string()).optional(),
  preconditions: z.array(z.string()).optional(),
  scenarios: z.array(ScenarioSchema).min(1),
  data: TestDataSchema,
});

export type Spec = z.infer<typeof SpecSchema>;
export type Scenario = z.infer<typeof ScenarioSchema>;

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