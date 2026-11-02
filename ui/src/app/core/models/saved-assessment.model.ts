import { MaintenanceAssessment } from './maintenance-assessment.model';

export interface SaveAssessmentRequest {
  assessment: MaintenanceAssessment;
  saved_by?: string;
  associated_work_order_id?: string;
}

export interface SavedAssessmentRecord {
  equipment_id: string;
  saved_at: string;
  saved_by: string | null;
  associated_work_order_id: string | null;
  assessment: MaintenanceAssessment;
}
