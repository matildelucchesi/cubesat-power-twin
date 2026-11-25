import { Face, SimulationRequest } from './simulation.service';

/** The request fields a failure scenario sets. */
export type FaultFields = Pick<SimulationRequest, 'failed_faces' | 'face_loss' | 'wings_deployed' | 'array_degradation'>;

export interface FaultScenario {
  id: string;
  label: string;
  /** What the scenario represents on a real spacecraft. */
  hint: string;
  fields: FaultFields;
  /** The scenario only makes sense with deployable panels. */
  needsWings?: boolean;
}

export const NO_FAULT: FaultFields = { failed_faces: [], face_loss: {}, wings_deployed: 2, array_degradation: 0 };

/**
 * Typical solar-array failures of small satellites, instead of switching faces
 * off at random. 'custom' keeps the per-face check boxes.
 */
export const FAULT_SCENARIOS: FaultScenario[] = [
  { id: 'none', label: 'No failure', hint: '', fields: NO_FAULT },
  {
    id: 'wing',
    label: 'One deployable wing stuck',
    hint: 'Half of the deployable area stays folded.',
    fields: { ...NO_FAULT, wings_deployed: 1 },
    needsWings: true,
  },
  {
    id: 'eps',
    label: 'EPS channel lost (±X faces)',
    hint: 'Two faces on one regulator go dark together.',
    fields: { ...NO_FAULT, failed_faces: ['+X', '-X'] },
  },
  {
    id: 'string',
    label: 'One string lost on +X',
    hint: 'One open cell takes out its series string: half of +X.',
    fields: { ...NO_FAULT, face_loss: { '+X': 0.5 } },
  },
  {
    id: 'radiation',
    label: 'Radiation degradation (−15 %)',
    hint: 'Every cell slowly loses output in orbit.',
    fields: { ...NO_FAULT, array_degradation: 0.15 },
  },
  {
    id: 'custom',
    label: 'Custom: choose failed faces',
    hint: 'Tick the faces below.',
    fields: NO_FAULT,
  },
];

/** Short text for chips and baseline labels; empty when nothing is failed. */
export function faultSummary(q: FaultFields & { deployables: boolean }): string {
  const parts: string[] = [];
  if (q.failed_faces.length) parts.push(`${q.failed_faces.length} face${q.failed_faces.length > 1 ? 's' : ''} off`);
  for (const [face, lost] of Object.entries(q.face_loss ?? {}) as [Face, number][]) {
    if (lost > 0) parts.push(`${Math.round(lost * 100)} % of ${face} lost`);
  }
  if (q.deployables && q.wings_deployed < 2) parts.push(`${2 - q.wings_deployed} wing stuck`);
  if (q.array_degradation > 0) parts.push(`cells −${Math.round(q.array_degradation * 100)} %`);
  return parts.join(', ');
}
