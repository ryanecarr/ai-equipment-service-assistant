import { Injectable, signal } from '@angular/core';
import { HttpClient, HttpErrorResponse } from '@angular/common/http';
import { environment } from '../../../environments/environment';
import { MaintenanceRequest } from '../models/maintenance-request.model';
import { MaintenanceAssessment } from '../models/maintenance-assessment.model';
import { EquipmentSummary } from '../models/equipment.model';
import { SaveAssessmentRequest, SavedAssessmentRecord } from '../models/saved-assessment.model';
import { ApiError, MappedError } from '../models/api-error.model';
import { mapErrorCode } from '../constants/error-messages';

export type AssessmentStatus = 'idle' | 'loading' | 'success' | 'error';
export type SaveStatus = 'idle' | 'saving' | 'saved' | 'error';

@Injectable({ providedIn: 'root' })
export class AssessmentService {
  readonly status = signal<AssessmentStatus>('idle');
  readonly result = signal<MaintenanceAssessment | null>(null);
  readonly error = signal<MappedError | null>(null);

  readonly saveStatus = signal<SaveStatus>('idle');
  readonly saveError = signal<MappedError | null>(null);
  readonly savedRecord = signal<SavedAssessmentRecord | null>(null);

  readonly equipmentCatalog = signal<EquipmentSummary[]>([]);
  // Set when the technician picks a suggested machine, so the intake form can show the
  // ID the assessment on screen was actually run against.
  readonly confirmedEquipmentId = signal<string | null>(null);

  private lastRequest: MaintenanceRequest | null = null;

  // Bumped on every submit() — lets an in-flight saveAssessment() recognize that
  // the assessment it was saving is no longer the one on screen (the technician
  // submitted a new request before the save finished) and discard its own stale
  // response instead of overwriting the new assessment's save state.
  private requestGeneration = 0;

  constructor(private readonly http: HttpClient) {}

  submit(request: MaintenanceRequest): void {
    this.lastRequest = request;
    this.requestGeneration++;
    this.status.set('loading');
    this.error.set(null);
    this.saveStatus.set('idle');
    this.saveError.set(null);
    this.savedRecord.set(null);

    const url = `${environment.apiBaseUrl}/assess?code=${environment.functionKey}`;

    this.http.post<MaintenanceAssessment>(url, request).subscribe({
      next: (res) => {
        this.result.set(res);
        this.status.set('success');
      },
      error: (err: HttpErrorResponse) => {
        this.error.set(this.mapError(err));
        this.status.set('error');
      },
    });
  }

  // Fetched once rather than searched on each keystroke, so typing in the picker costs
  // no database reads. A failure leaves the list empty, and the field still accepts a
  // typed ID, so the form keeps working without it.
  loadEquipmentCatalog(): void {
    const url = `${environment.apiBaseUrl}/equipment?code=${environment.functionKey}`;

    this.http.get<EquipmentSummary[]>(url).subscribe({
      next: (catalog) => this.equipmentCatalog.set(catalog),
      error: () => this.equipmentCatalog.set([]),
    });
  }

  confirmEquipment(equipmentId: string): void {
    if (this.lastRequest && this.status() !== 'loading') {
      this.confirmedEquipmentId.set(equipmentId);
      this.submit({ ...this.lastRequest, equipment_id: equipmentId });
    }
  }

  retry(): void {
    if (this.lastRequest) {
      this.submit(this.lastRequest);
    }
  }

  saveAssessment(payload: { savedBy?: string; associatedWorkOrderId?: string }): void {
    const assessment = this.result();
    if (!assessment || assessment.equipment_id === null) {
      return;
    }
    if (this.saveStatus() === 'saving' || this.saveStatus() === 'saved') {
      return;
    }

    this.saveStatus.set('saving');
    this.saveError.set(null);

    const body: SaveAssessmentRequest = {
      assessment,
      saved_by: payload.savedBy?.trim() ? payload.savedBy.trim() : undefined,
      associated_work_order_id: payload.associatedWorkOrderId || undefined,
    };

    const url = `${environment.apiBaseUrl}/assessments?code=${environment.functionKey}`;
    const generation = this.requestGeneration;

    this.http.post<SavedAssessmentRecord>(url, body).subscribe({
      next: (res) => {
        if (generation !== this.requestGeneration) {
          return; // a newer assessment was submitted while this save was in flight
        }
        this.savedRecord.set(res);
        this.saveStatus.set('saved');
      },
      error: (err: HttpErrorResponse) => {
        if (generation !== this.requestGeneration) {
          return;
        }
        this.saveError.set(this.mapError(err));
        this.saveStatus.set('error');
      },
    });
  }

  private mapError(err: HttpErrorResponse): MappedError {
    // status 0 means no HTTP response was ever received — covers both a
    // genuine network failure and a CORS rejection (the browser surfaces a
    // blocked cross-origin response to HttpClient as status 0, no body).
    if (err.status === 0) {
      return mapErrorCode('network_error');
    }
    const body = err.error as ApiError | undefined;
    const code = body?.error?.code ?? 'internal_error';
    return mapErrorCode(code);
  }
}
