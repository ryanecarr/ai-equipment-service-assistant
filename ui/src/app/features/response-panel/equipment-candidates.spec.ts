import { ComponentFixture, TestBed } from '@angular/core/testing';
import { EquipmentCandidates } from './equipment-candidates';

describe('EquipmentCandidates', () => {
  let fixture: ComponentFixture<EquipmentCandidates>;

  beforeEach(() => {
    fixture = TestBed.createComponent(EquipmentCandidates);
    fixture.componentRef.setInput('candidates', [
      { equipment_id: 'CATXQ200-RU-6045', model: 'CAT XQ200', customer: 'Redclay Utilities', location: 'Site 3 - Macon, GA' },
      { equipment_id: 'CATXQ200-SH-3981', model: 'CAT XQ200', customer: 'Sandhill Drainage', location: null },
    ]);
    fixture.detectChanges();
  });

  const buttons = () => Array.from((fixture.nativeElement as HTMLElement).querySelectorAll('button'));

  it('shows each machine with its details', () => {
    expect(buttons()).toHaveLength(2);
    expect(buttons()[0].textContent).toContain('Redclay Utilities');
    expect(buttons()[0].textContent).toContain('Site 3 - Macon, GA');
  });

  it('emits the ID of the chosen machine', () => {
    const chosen: string[] = [];
    fixture.componentInstance.selected.subscribe((id) => chosen.push(id));

    buttons()[1].click();

    expect(chosen).toEqual(['CATXQ200-SH-3981']);
  });
});
