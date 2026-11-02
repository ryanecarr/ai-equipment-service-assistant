import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { AssessmentService } from './assessment.service';

describe('AssessmentService', () => {
  let service: AssessmentService;
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({ providers: [provideHttpClient(), provideHttpClientTesting()] });
    service = TestBed.inject(AssessmentService);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  it('loads the equipment catalog', () => {
    service.loadEquipmentCatalog();
    const catalog = [{ equipment_id: 'CAT320-SE-1042', model: 'CAT 320', customer: 'Southeast Construction', location: null }];

    http.expectOne((req) => req.method === 'GET' && req.url.includes('/equipment')).flush(catalog);

    expect(service.equipmentCatalog()).toEqual(catalog);
  });

  it('leaves the catalog empty if it cannot be loaded', () => {
    service.loadEquipmentCatalog();

    http.expectOne((req) => req.url.includes('/equipment')).flush('down', { status: 503, statusText: 'Unavailable' });

    expect(service.equipmentCatalog()).toEqual([]);
  });

  it('reruns the same request against the confirmed machine', () => {
    service.submit({ request_text: 'Boom is slow' });
    http.expectOne((req) => req.url.includes('/assess')).flush({});

    service.confirmEquipment('CAT320-SE-1042');

    const rerun = http.expectOne((req) => req.url.includes('/assess'));
    expect(rerun.request.body).toEqual({ request_text: 'Boom is slow', equipment_id: 'CAT320-SE-1042' });
    expect(service.confirmedEquipmentId()).toBe('CAT320-SE-1042');
    rerun.flush({});
  });

  it('does not rerun while a request is already in flight', () => {
    service.submit({ request_text: 'Boom is slow' });

    service.confirmEquipment('CAT320-SE-1042');

    expect(service.confirmedEquipmentId()).toBeNull();
    http.expectOne((req) => req.url.includes('/assess')).flush({});
  });

  it('ignores a confirmation when nothing has been submitted', () => {
    service.confirmEquipment('CAT320-SE-1042');

    expect(service.status()).toBe('idle');
  });
});
