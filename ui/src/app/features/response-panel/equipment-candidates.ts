import { Component, EventEmitter, Input, Output } from '@angular/core';
import { EquipmentSummary } from '../../core/models/equipment.model';

@Component({
  selector: 'app-equipment-candidates',
  templateUrl: './equipment-candidates.html',
  styleUrl: './equipment-candidates.scss',
})
export class EquipmentCandidates {
  @Input({ required: true }) candidates!: EquipmentSummary[];
  @Output() selected = new EventEmitter<string>();
}
