export interface ExampleScenario {
  label: string;
  requestText: string;
  equipmentId?: string;
}

/**
 * Grounded in the real dummy fixtures (seed-data/equipment_master.json,
 * seed-data/work_orders.json) so each scenario deterministically exercises
 * a distinct grounding outcome against the live backend.
 */
export const EXAMPLE_SCENARIOS: ExampleScenario[] = [
  {
    label: 'Hydraulic boom lag — CAT 320',
    equipmentId: 'CAT320-SE-1042',
    requestText:
      'A CAT 320 excavator is experiencing slow hydraulic response when raising the boom. ' +
      'The issue started yesterday. No fault codes have been provided. Customer is Southeast Construction.',
  },
  {
    label: 'Track wear & noise — CAT 320',
    equipmentId: 'CAT320-NR-2207',
    requestText:
      'A CAT 320 undercarriage is showing excessive track wear and a grinding noise during travel. ' +
      'Customer is Northridge Excavation, operating primarily on an abrasive job site.',
  },
  {
    label: 'Unregistered rental — no equipment match',
    requestText:
      'A rented skid steer is losing hydraulic pressure intermittently while running the auger attachment. ' +
      'No equipment ID is available — this is a short-term rental unit not yet in our system.',
  },
];
