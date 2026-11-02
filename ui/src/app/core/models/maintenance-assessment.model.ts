import { EquipmentSummary } from './equipment.model';

export type Category = 'Hydraulic' | 'Electrical' | 'Engine' | 'Structural' | 'Other';
export type Priority = 'Low' | 'Medium' | 'High' | 'Critical';
export type MatchStatus = 'matched' | 'needs_confirmation' | 'no_match';

export interface MaintenanceAssessment {
  equipment_model: string;
  equipment_id: string | null;
  customer: string | null;
  reported_issue: string;
  category: Category;
  priority: Priority;
  recommendations: string[];
  additional_information_needed: string[];
  requires_technician_review: boolean;
  related_work_orders: string[];
  grounding_used: boolean;
  comparison_notes: string | null;
  match_status?: MatchStatus;
  equipment_candidates?: EquipmentSummary[];
}
