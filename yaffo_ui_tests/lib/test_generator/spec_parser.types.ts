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