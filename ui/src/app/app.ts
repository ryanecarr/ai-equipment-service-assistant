import { Component, computed } from '@angular/core';
import { IntakeForm } from './features/intake-form/intake-form';
import { ResponsePanel } from './features/response-panel/response-panel';
import { AssessmentService } from './core/services/assessment.service';

@Component({
  selector: 'app-root',
  imports: [IntakeForm, ResponsePanel],
  templateUrl: './app.html',
  styleUrl: './app.scss',
})
export class App {
  constructor(private readonly assessmentService: AssessmentService) {}

  readonly liveAnnouncement = computed(() => {
    const status = this.assessmentService.status();
    if (status === 'success') {
      const r = this.assessmentService.result();
      if (!r) return '';
      return `Assessment complete: ${r.priority} priority, ${r.category}, ${
        r.requires_technician_review ? 'technician review required' : 'no mandatory review flagged'
      }.`;
    }
    if (status === 'error') {
      const e = this.assessmentService.error();
      return e ? `Assessment failed: ${e.title}.` : '';
    }
    if (status === 'loading') {
      return 'Running assessment.';
    }
    return '';
  });
}
