import { isPlatformBrowser } from '@angular/common';
import { HttpClient } from '@angular/common/http';
import {
  AfterViewInit,
  ChangeDetectorRef,
  Component,
  ElementRef,
  Inject,
  OnDestroy,
  PLATFORM_ID,
  ViewChild,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import { TranslatePipe, TranslateService } from '@ngx-translate/core';
import type { CircleMarker, LayerGroup, Map as LeafletMap } from 'leaflet';
import { SeoService } from '@app/services/seo.service';

export type AedAccess = '24/7' | 'business_hours' | 'on_request';

export interface AedPoint {
  id: string;
  name: string;
  city: string;
  voivodeship: string;
  lat: number;
  lon: number;
  description: string;
  indoor: boolean;
  access: AedAccess;
  openingHours: string;
  operator: string;
  lastVerified: string;
  source: string;
  /** Distance to the user in km, set after "find nearest" */
  distanceKm?: number;
}

/**
 * Sample data lives in assets for now. The shape matches the CSV in /data/aed-sample.csv,
 * so it can later be replaced by a call to the portal API (/resources/{id}/geo).
 */
const AED_DATA_URL = 'assets/aed/aed-sample.json';

/** Marker colours per access type. Access is also always stated as text in the list and popups. */
const ACCESS_COLORS: Record<AedAccess, string> = {
  '24/7': '#1b7a3e',
  business_hours: '#a85d00',
  on_request: '#6b4fa0',
};

const ACCESS_LABEL_KEYS: Record<AedAccess, string> = {
  '24/7': 'AedMap.Access24',
  business_hours: 'AedMap.AccessBusiness',
  on_request: 'AedMap.AccessOnRequest',
};

/**
 * AED map page: shows defibrillator locations on a Leaflet map with a filterable list below.
 * The list is the keyboard / screen reader alternative to the map markers.
 */
@Component({
  selector: 'app-aed-map',
  templateUrl: './aed-map.component.html',
  standalone: true,
  imports: [TranslatePipe, FormsModule],
})
export class AedMapComponent implements AfterViewInit, OnDestroy {
  @ViewChild('mapRef') mapRef: ElementRef<HTMLDivElement>;

  readonly accessTypes: AedAccess[] = ['24/7', 'business_hours', 'on_request'];
  readonly colors = ACCESS_COLORS;
  readonly labelKeys = ACCESS_LABEL_KEYS;

  all: AedPoint[] = [];
  visible: AedPoint[] = [];
  cities: string[] = [];

  selectedCity = '';
  only247 = false;

  loadError = false;
  locating = false;
  statusMessage = '';

  private L: any;
  private map: LeafletMap;
  private markersLayer: LayerGroup;
  private userMarker: CircleMarker;
  private markersById = new Map<string, CircleMarker>();

  constructor(
    private http: HttpClient,
    private translate: TranslateService,
    private seoService: SeoService,
    private cdr: ChangeDetectorRef,
    @Inject(PLATFORM_ID) private platformId: object,
  ) {
    this.seoService.setPageTitleByTranslationKey(['AedMap.Title']);
  }

  async ngAfterViewInit(): Promise<void> {
    if (!isPlatformBrowser(this.platformId)) {
      return;
    }
    const leaflet: any = await import('leaflet');
    this.L = leaflet.default ?? leaflet;

    this.map = this.L.map(this.mapRef.nativeElement, { worldCopyJump: true }).setView([52.0, 19.4], 6);
    this.L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: 'Map data &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
      maxZoom: 19,
      minZoom: 5,
    }).addTo(this.map);
    this.markersLayer = this.L.layerGroup().addTo(this.map);

    this.http.get<any>(AED_DATA_URL).subscribe({
      next: (collection) => {
        this.all = collection.features.map((f) => this.toPoint(f));
        this.cities = [...new Set(this.all.map((p) => p.city))].sort((a, b) => a.localeCompare(b, 'pl'));
        this.applyFilters(true);
        this.cdr.markForCheck();
      },
      error: () => {
        this.loadError = true;
        this.cdr.markForCheck();
      },
    });
  }

  ngOnDestroy(): void {
    this.map?.remove();
  }

  accessLabelKey(access: AedAccess): string {
    return ACCESS_LABEL_KEYS[access];
  }

  /** Re-filters the points and redraws markers. */
  applyFilters(fit = false): void {
    this.visible = this.all.filter(
      (p) => (!this.selectedCity || p.city === this.selectedCity) && (!this.only247 || p.access === '24/7'),
    );
    this.statusMessage = '';
    this.drawMarkers(fit);
  }

  /** Centres the map on a point and opens its popup. */
  showOnMap(point: AedPoint): void {
    this.mapRef.nativeElement.scrollIntoView({ behavior: 'smooth', block: 'center' });
    this.map.setView([point.lat, point.lon], 16);
    this.markersById.get(point.id)?.openPopup();
  }

  /** Uses browser geolocation to find the nearest visible AED. */
  findNearest(): void {
    if (!navigator.geolocation) {
      this.statusMessage = this.translate.instant('AedMap.LocationUnsupported');
      return;
    }
    this.locating = true;
    this.statusMessage = this.translate.instant('AedMap.Locating');
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        this.locating = false;
        const { latitude, longitude } = pos.coords;
        this.visible.forEach((p) => (p.distanceKm = haversineKm(latitude, longitude, p.lat, p.lon)));
        const nearest = [...this.visible].sort((a, b) => a.distanceKm - b.distanceKm)[0];
        if (!nearest) {
          this.statusMessage = this.translate.instant('AedMap.NoResults');
        } else {
          this.statusMessage = this.translate.instant('AedMap.NearestResult', {
            name: nearest.name,
            city: nearest.city,
            distance: nearest.distanceKm.toFixed(1),
          });
          this.userMarker?.remove();
          this.userMarker = this.L.circleMarker([latitude, longitude], {
            radius: 8,
            color: '#ffffff',
            weight: 2,
            fillColor: '#0b5cad',
            fillOpacity: 1,
          }).addTo(this.map);
          this.map.fitBounds(
            this.L.latLngBounds([
              [latitude, longitude],
              [nearest.lat, nearest.lon],
            ]),
            { padding: [50, 50], maxZoom: 16 },
          );
          this.markersById.get(nearest.id)?.openPopup();
        }
        this.cdr.markForCheck();
      },
      () => {
        this.locating = false;
        this.statusMessage = this.translate.instant('AedMap.LocationDenied');
        this.cdr.markForCheck();
      },
      { enableHighAccuracy: true, timeout: 10000 },
    );
  }

  private drawMarkers(fit: boolean): void {
    this.markersLayer.clearLayers();
    this.markersById.clear();
    for (const p of this.visible) {
      const marker: CircleMarker = this.L.circleMarker([p.lat, p.lon], {
        radius: 9,
        color: '#ffffff',
        weight: 2,
        fillColor: ACCESS_COLORS[p.access],
        fillOpacity: 1,
      });
      marker.bindPopup(this.buildPopup(p));
      marker.addTo(this.markersLayer);
      this.markersById.set(p.id, marker);
    }
    if (fit && this.visible.length) {
      this.map.fitBounds(this.L.latLngBounds(this.visible.map((p) => [p.lat, p.lon])), { padding: [30, 30] });
    }
  }

  /** Popup is built from DOM nodes with textContent, so data is never interpreted as HTML. */
  private buildPopup(p: AedPoint): HTMLElement {
    const root = document.createElement('div');
    const title = document.createElement('strong');
    title.textContent = p.name;
    root.appendChild(title);
    const rows: [string, string][] = [
      ['', `${p.city}, ${p.description}`],
      ['', this.translate.instant(ACCESS_LABEL_KEYS[p.access])],
      ['AedMap.Hours', p.openingHours],
      ['AedMap.Operator', p.operator],
      ['AedMap.LastVerified', p.lastVerified],
    ];
    for (const [labelKey, value] of rows) {
      const line = document.createElement('div');
      line.textContent = labelKey ? `${this.translate.instant(labelKey)}: ${value}` : value;
      root.appendChild(line);
    }
    return root;
  }

  private toPoint(feature: any): AedPoint {
    const props = feature.properties;
    return {
      id: props.id,
      name: props.name,
      city: props.city,
      voivodeship: props.voivodeship,
      lat: feature.geometry.coordinates[1],
      lon: feature.geometry.coordinates[0],
      description: props.location_description,
      indoor: props.indoor,
      access: props.access,
      openingHours: props.opening_hours,
      operator: props.operator,
      lastVerified: props.last_verified,
      source: props.source,
    };
  }
}

/** Great-circle distance between two WGS84 points, in km. */
export function haversineKm(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const rad = (d: number) => (d * Math.PI) / 180;
  const a =
    Math.sin(rad(lat2 - lat1) / 2) ** 2 +
    Math.cos(rad(lat1)) * Math.cos(rad(lat2)) * Math.sin(rad(lon2 - lon1) / 2) ** 2;
  return 2 * 6371 * Math.asin(Math.sqrt(a));
}
