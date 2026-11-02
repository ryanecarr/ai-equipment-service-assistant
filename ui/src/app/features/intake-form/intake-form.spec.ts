import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { IntakeForm } from './intake-form';
import { AssessmentService } from '../../core/services/assessment.service';

const CATALOG = [
  { equipment_id: 'CAT320-SE-1042', model: 'CAT 320', customer: 'Southeast Construction', location: 'Site 7 - Jacksonville, FL' },
  { equipment_id: 'CAT320-NR-2207', model: 'CAT 320', customer: 'Northridge Excavation', location: null },
  { equipment_id: 'CATXQ200-RU-6045', model: 'CAT XQ200', customer: 'Redclay Utilities', location: null },
];

describe('IntakeForm', () => {
  let form: IntakeForm;
  let http: HttpTestingController;
  let service: AssessmentService;

  beforeEach(() => {
    TestBed.configureTestingModule({
      imports: [IntakeForm],
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    http = TestBed.inject(HttpTestingController);
    service = TestBed.inject(AssessmentService);
    const fixture = TestBed.createComponent(IntakeForm);
    form = fixture.componentInstance;
    http.expectOne((req) => req.url.includes('/equipment')).flush(CATALOG);
  });

  afterEach(() => http.verify());

  const search = (query: string) => {
    form.searchEquipment({ originalEvent: new Event('input'), query });
    return form.equipmentSuggestions();
  };

  it('searches by model, customer, ID and site', () => {
    expect(search('xq200')).toEqual(['CATXQ200-RU-6045']);
    expect(search('northridge')).toEqual(['CAT320-NR-2207']);
    expect(search('cat320-se')).toEqual(['CAT320-SE-1042']);
    expect(search('jacksonville')).toEqual(['CAT320-SE-1042']);
  });

  it('requires every word to match, in any order', () => {
    expect(search('northridge 320')).toEqual(['CAT320-NR-2207']);
    expect(search('northridge xq200')).toEqual([]);
  });

  it('lists everything for an empty query, as the dropdown button sends', () => {
    expect(search('')).toHaveLength(CATALOG.length);
  });

  it('still accepts an ID that is not in the catalog', () => {
    form.form.setValue({ requestText: 'Boom is slow', equipmentId: ' UNLISTED-1 ' });
    form.onSubmit();

    const request = http.expectOne((req) => req.url.includes('/assess'));
    expect(request.request.body.equipment_id).toBe('UNLISTED-1');
    request.flush({});
  });

  it('omits the ID when the picker has been cleared', () => {
    form.form.patchValue({ requestText: 'Boom is slow' });
    form.form.controls.equipmentId.setValue(null as unknown as string);
    form.onSubmit();

    const request = http.expectOne((req) => req.url.includes('/assess'));
    expect(request.request.body.equipment_id).toBeUndefined();
    request.flush({});
  });

  it('shows the machine the technician confirmed', () => {
    form.form.patchValue({ requestText: 'Boom is slow' });
    form.onSubmit();
    http.expectOne((req) => req.url.includes('/assess')).flush({});

    service.confirmEquipment('CAT320-SE-1042');
    http.expectOne((req) => req.url.includes('/assess')).flush({});
    TestBed.tick();

    expect(form.form.controls.equipmentId.value).toBe('CAT320-SE-1042');
  });
});
