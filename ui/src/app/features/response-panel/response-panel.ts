import { Component, ElementRef, effect, viewChild } from '@angular/core';
import { CardModule } from 'primeng/card';
import { SkeletonModule } from 'primeng/skeleton';
import { MessageModule } from 'primeng/message';
import { ButtonModule } from 'primeng/button';
import { AssessmentService } from '../../core/services/assessment.service';
import { AssessmentResult } from './assessment-result';

@Component({
  selector: 'app-response-panel',
  imports: [CardModule, SkeletonModule, MessageModule, ButtonModule, AssessmentResult],
  templateUrl: './response-panel.html',
  styleUrl: './response-panel.scss',
})
export class ResponsePanel {
  private readonly errorHeading = viewChild<ElementRef<HTMLElement>>('errorHeading');

  constructor(readonly assessmentService: AssessmentService) {
    effect(() => {
      if (this.assessmentService.status() === 'error') {
        this.errorHeading()?.nativeElement.focus();
      }
    });
  }

  retry(): void {
    this.assessmentService.retry();
  }
}
