import { Component, computed, effect, signal } from '@angular/core';
import { AbstractControl, FormControl, FormGroup, ReactiveFormsModule, ValidationErrors, Validators } from '@angular/forms';
import { TextareaModule } from 'primeng/textarea';
import { AutoCompleteCompleteEvent, AutoCompleteModule } from 'primeng/autocomplete';
import { ButtonModule } from 'primeng/button';
import { AssessmentService } from '../../core/services/assessment.service';
import { EXAMPLE_SCENARIOS, ExampleScenario } from '../../core/constants/example-scenarios';

function notBlank(control: AbstractControl<string>): ValidationErrors | null {
  const value = control.value ?? '';
  return value.trim().length > 0 ? null : { blank: true };
}

@Component({
  selector: 'app-intake-form',
  imports: [ReactiveFormsModule, TextareaModule, AutoCompleteModule, ButtonModule],
  templateUrl: './intake-form.html',
  styleUrl: './intake-form.scss',
})
export class IntakeForm {
  readonly examples: ExampleScenario[] = EXAMPLE_SCENARIOS;

  readonly form = new FormGroup({
    requestText: new FormControl('', { nonNullable: true, validators: [Validators.required, notBlank] }),
    equipmentId: new FormControl('', { nonNullable: true }),
  });

  readonly equipmentSuggestions = signal<string[]>([]);
  readonly equipmentById = computed(
    () => new Map(this.assessmentService.equipmentCatalog().map((item) => [item.equipment_id, item])),
  );

  constructor(readonly assessmentService: AssessmentService) {
    assessmentService.loadEquipmentCatalog();

    effect(() => {
      const confirmedId = assessmentService.confirmedEquipmentId();
      if (confirmedId) {
        this.form.controls.equipmentId.setValue(confirmedId);
      }
    });
  }

  // The form value stays a plain ID string, so an ID that isn't in the list can still be
  // typed and the example scenarios can fill the field directly.
  searchEquipment(event: AutoCompleteCompleteEvent): void {
    const terms = event.query.toLowerCase().split(/\s+/).filter(Boolean);
    const matches = this.assessmentService.equipmentCatalog().filter((item) => {
      const searchable = [item.equipment_id, item.model, item.customer, item.location ?? ''].join(' ').toLowerCase();
      return terms.every((term) => searchable.includes(term));
    });
    this.equipmentSuggestions.set(matches.map((item) => item.equipment_id));
  }

  get isLoading(): boolean {
    return this.assessmentService.status() === 'loading';
  }

  useExample(scenario: ExampleScenario): void {
    this.form.setValue({
      requestText: scenario.requestText,
      equipmentId: scenario.equipmentId ?? '',
    });
    this.form.markAsDirty();
  }

  onSubmit(): void {
    if (this.form.invalid || this.isLoading) {
      this.form.markAllAsTouched();
      return;
    }

    const { requestText, equipmentId } = this.form.getRawValue();
    // The picker writes null when cleared, whatever the control's declared type says.
    const selectedId = (equipmentId ?? '').trim();
    this.assessmentService.submit({
      request_text: requestText,
      equipment_id: selectedId || undefined,
    });
  }
}
