import { AfterViewInit, Component, ElementRef, Input, viewChild } from '@angular/core';
import { FormControl, FormGroup, ReactiveFormsModule } from '@angular/forms';
import { TagModule } from 'primeng/tag';
import { ChipModule } from 'primeng/chip';
import { DividerModule } from 'primeng/divider';
import { ButtonModule } from 'primeng/button';
import { InputTextModule } from 'primeng/inputtext';
import { SelectModule } from 'primeng/select';
import { MessageModule } from 'primeng/message';
import { MaintenanceAssessment, Category, Priority } from '../../core/models/maintenance-assessment.model';
import { AssessmentService } from '../../core/services/assessment.service';
import { EquipmentCandidates } from './equipment-candidates';

const CATEGORY_CLASS: Record<Category, string> = {
  Hydraulic: 'category--hydraulic',
  Electrical: 'category--electrical',
  Engine: 'category--engine',
  Structural: 'category--structural',
  Other: 'category--other',
};

const PRIORITY_SEVERITY: Record<Priority, 'success' | 'info' | 'warn' | 'danger'> = {
  Low: 'success',
  Medium: 'info',
  High: 'warn',
  Critical: 'danger',
};

@Component({
  selector: 'app-assessment-result',
  imports: [TagModule, ChipModule, DividerModule, ButtonModule, InputTextModule, SelectModule, MessageModule, ReactiveFormsModule, EquipmentCandidates],
  templateUrl: './assessment-result.html',
  styleUrl: './assessment-result.scss',
  host: {
    'animate.enter': 'result-enter',
  },
})
export class AssessmentResult implements AfterViewInit {
  @Input({ required: true }) assessment!: MaintenanceAssessment;

  private readonly resultHeading = viewChild<ElementRef<HTMLElement>>('resultHeading');

  readonly saveForm = new FormGroup({
    savedBy: new FormControl('', { nonNullable: true }),
    associatedWorkOrderId: new FormControl('', { nonNullable: true }),
  });

  constructor(readonly assessmentService: AssessmentService) {}

  ngAfterViewInit(): void {
    // Fresh instance each time the parent's @if toggles to 'success' — focus
    // the heading so keyboard/screen-reader users land on new content.
    this.resultHeading()?.nativeElement.focus();
  }

  get categoryClass(): string {
    return CATEGORY_CLASS[this.assessment.category];
  }

  get prioritySeverity(): 'success' | 'info' | 'warn' | 'danger' {
    return PRIORITY_SEVERITY[this.assessment.priority];
  }

  get workOrderOptions(): { label: string; value: string }[] {
    return this.assessment.related_work_orders.map((wo) => ({ label: wo, value: wo }));
  }

  onSave(): void {
    const { savedBy, associatedWorkOrderId } = this.saveForm.getRawValue();
    this.assessmentService.saveAssessment({
      savedBy: savedBy || undefined,
      associatedWorkOrderId: associatedWorkOrderId || undefined,
    });
  }
}
