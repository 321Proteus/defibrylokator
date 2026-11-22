const escapeHtml = s => String(s).replace(/[&<>"']/g, c => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;'
}[c]));

const tabButtons = Array.from(document.querySelectorAll('.tab-btn'));
const tabPanels = document.querySelectorAll('.tab-panel');

function activateTab(btn, moveFocus = false) {
    tabButtons.forEach(b => {
        const active = b === btn;
        b.classList.toggle('active', active);
        b.setAttribute('aria-selected', String(active));
        b.tabIndex = active ? 0 : -1;
    });
    tabPanels.forEach(p => {
        p.classList.remove('active');
        p.hidden = true;
    });
    const targetPanel = document.getElementById(btn.dataset.target);
    if (targetPanel) {
        targetPanel.classList.add('active');
        targetPanel.hidden = false;
    }
    if (moveFocus) btn.focus();
    if (btn.dataset.target === 'panel-nearest') {
        requestAnimationFrame(() => locateAndLoadNearestAed());
    }
}

tabButtons.forEach((btn, i) => {
    btn.tabIndex = btn.classList.contains('active') ? 0 : -1;
    btn.addEventListener('click', () => activateTab(btn));
    btn.addEventListener('keydown', e => {
        const last = tabButtons.length - 1;
        const next = {
            ArrowRight: i === last ? 0 : i + 1,
            ArrowLeft: i === 0 ? last : i - 1,
            Home: 0,
            End: last
        }[e.key];
        if (next === undefined) return;
        e.preventDefault();
        activateTab(tabButtons[next], true);
    });
});

function showResult(box, kind, html) {
    box.innerHTML = html;
    box.className = kind;
}

function clearResult(box) {
    box.className = '';
    box.innerHTML = '';
}

function setBusy(btn, busy, label) {
    btn.setAttribute('aria-disabled', String(busy));
    btn.textContent = label;
}

let aedMap = null;
let userMarker = null;
let aedMarkersLayer = null;
let markerByKey = new Map();

// Baza ma dziesiątki tysięcy AED, więc mapa nie ładuje wszystkiego: serwer zwraca tylko NEARBY_LIMIT najbliższych
// punktów (od lokalizacji użytkownika albo środka mapy), a po przesunięciu mapy poza pobrany obszar dociągamy kolejne.
const NEARBY_LIMIT = 300;
const MAX_MARKERS_ON_MAP = 1500;
const DEFAULT_CENTER = [52.0693, 19.4803];
let userPos = null;        // {lat, lon} albo null
let listItems = [];        // pozycje z listy "najbliższe": ich znaczniki muszą przetrwać odświeżanie mapy
let loadedView = null;     // {lat, lon, radiusKm}: obszar w pełni pokryty ostatnim pobraniem (radiusKm null = pobrano wszystko)
let ignoreMovesUntil = 0;  // ruchy mapy wywołane przez kod (fitBounds/setView) nie powinny uruchamiać pobierania
let moveTimer = null;
let fetchSeq = 0;
let aedIcon = null;

function haversineKm(lat1, lon1, lat2, lon2) {
    const toRad = d => (d * Math.PI) / 180;
    const R = 6371;
    const dLat = toRad(lat2 - lat1);
    const dLon = toRad(lon2 - lon1);
    const a =
        Math.sin(dLat / 2) * Math.sin(dLat / 2) +
        Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) *
        Math.sin(dLon / 2) * Math.sin(dLon / 2);
    return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

function formatDistance(km) {
    if (km < 1) return `${Math.round(km * 1000)} m`;
    return `${km.toFixed(2)} km`;
}

function getStatusInfo(status) {
    if (status === 'sprawny') return { label: 'Sprawny', cls: '' };
    if (status === 'w_serwisie') return { label: 'W serwisie', cls: 'warn' };
    if (status === 'nieaktywny') return { label: 'Nieaktywny', cls: 'off' };
    return { label: status || 'Brak danych', cls: 'off' };
}

function ensureMapInitialized(centerLat = DEFAULT_CENTER[0], centerLon = DEFAULT_CENTER[1], zoom = 6) {
    const mapContainer = document.getElementById('aed-map');
    if (!mapContainer || typeof L === 'undefined') return null;

    if (!aedMap) {
        aedMap = L.map('aed-map').setView([centerLat, centerLon], zoom);
        L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
            maxZoom: 19,
            attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">Współtwórcy OpenStreetMap</a>'
        }).addTo(aedMap);
        // Setki punktów w jednym miejscu: grupujemy znaczniki w klastry (fallback na zwykłą warstwę, gdy wtyczka się nie załaduje).
        aedMarkersLayer = (typeof L.markerClusterGroup === 'function'
            ? L.markerClusterGroup({
                chunkedLoading: true,
                showCoverageOnHover: false,
                maxClusterRadius: 50,
                iconCreateFunction: (cluster) => {
                    const count = cluster.getChildCount();
                    const size = count < 10 ? 'small' : count < 100 ? 'medium' : 'large';
                    return L.divIcon({
                        html: `<div class="aed-cluster aed-cluster-${size}"><span>${count}</span></div>`,
                        className: '',
                        iconSize: [40, 40]
                    });
                }
            })
            : L.layerGroup()).addTo(aedMap);
        aedMap.on('moveend', onMapMoved);
    }
    aedMap.invalidateSize();
    return aedMap;
}

// OSM często nie ma nazwy ani adresu: pokazujemy czytelny zamiennik zamiast pustego tytułu i samego przecinka.
const itemTitle = item => item.nazwa_obiektu || 'Defibrylator AED';
const itemAddress = item => [item.ulica_i_numer, item.miasto].filter(Boolean).join(', ');

const itemKey = item => `${item.id_zewnetrzne || ''}|${item.szerokosc_geo}|${item.dlugosc_geo}`;

async function fetchNearby(lat, lon) {
    const res = await fetch(`/api/aed-nearby?lat=${lat.toFixed(5)}&lon=${lon.toFixed(5)}&limit=${NEARBY_LIMIT}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    return { items: data.items || [], radiusKm: data.radius_km === undefined ? null : data.radius_km };
}

function distanceFromUser(item) {
    return userPos ? haversineKm(userPos.lat, userPos.lon, item.szerokosc_geo, item.dlugosc_geo) : null;
}

function resetMarkers() {
    if (aedMarkersLayer) aedMarkersLayer.clearLayers();
    markerByKey.clear();
}

function popupHtml(item) {
    const st = getStatusInfo(item.status);
    const dist = distanceFromUser(item);
    const distLine = dist !== null
        ? `<br><strong>Odległość:</strong> ${escapeHtml(formatDistance(dist))}`
        : '';
    const address = itemAddress(item);
    return `
        <div style="font-family: Inter, sans-serif; font-size: 13px; line-height: 1.45;">
            <strong>${escapeHtml(address || itemTitle(item))}</strong><br>
            ${address && item.nazwa_obiektu && item.nazwa_obiektu !== 'Defibrylator AED' ? `${escapeHtml(item.nazwa_obiektu)}<br>` : ''}
            ${item.opis_lokalizacji ? `<em>${escapeHtml(item.opis_lokalizacji)}</em><br>` : ''}
            <strong>Dostępność:</strong> ${escapeHtml(item.dostepnosc || 'brak danych')}<br>
            <strong>Status:</strong> ${escapeHtml(st.label)}${distLine}
        </div>
    `;
}

// Większość punktów z OSM nie ma adresu. Serwer ustala go raz (geokodowanie odwrotne) i zapisuje w bazie,
// więc kolejne wejścia dostają go od razu. Pytamy tylko o to, co użytkownik widzi: karty z listy i otwierane dymki.
const addressRequests = new Map();

function resolveAddress(item) {
    if (itemAddress(item) || !item.id_zewnetrzne) return Promise.resolve(false);
    if (!addressRequests.has(item.id_zewnetrzne)) {
        addressRequests.set(item.id_zewnetrzne, fetch(`/api/aed-address?id=${encodeURIComponent(item.id_zewnetrzne)}`)
            .then(res => (res.ok ? res.json() : null))
            .then(data => {
                if (!data || !data.found) return false;
                item.ulica_i_numer = data.ulica_i_numer || '';
                item.kod_pocztowy = data.kod_pocztowy || '';
                item.miasto = data.miasto || '';
                return true;
            })
            .catch(() => false)
            .then(found => {
                if (!found) setTimeout(() => addressRequests.delete(item.id_zewnetrzne), 60000); // spróbuj ponownie później
                return found;
            }));
    }
    return addressRequests.get(item.id_zewnetrzne);
}

function addMarkers(items) {
    if (!aedMarkersLayer) return;
    if (!aedIcon) {
        aedIcon = L.divIcon({
            className: '',
            html: '<div class="aed-pin-dot">+</div>',
            iconSize: [24, 24],
            iconAnchor: [12, 12],
            popupAnchor: [0, -12]
        });
    }

    const markers = [];
    items.forEach(item => {
        const key = itemKey(item);
        if (markerByKey.has(key)) return;
        const marker = L.marker([item.szerokosc_geo, item.dlugosc_geo], { icon: aedIcon }).bindPopup(() => popupHtml(item));
        marker.on('popupopen', () => {
            resolveAddress(item).then(found => {
                if (found && marker.isPopupOpen()) marker.setPopupContent(popupHtml(item));
            });
        });
        markers.push(marker);
        markerByKey.set(key, marker);
    });

    if (typeof aedMarkersLayer.addLayers === 'function') {
        aedMarkersLayer.addLayers(markers); // jedna operacja zamiast setek
    } else {
        markers.forEach(marker => marker.addTo(aedMarkersLayer));
    }
}

// Dokładanie znaczników po przesunięciu mapy; przy zbyt dużej liczbie czyścimy mapę, zostawiając pozycje z listy.
function showMoreMarkers(items) {
    if (markerByKey.size + items.length > MAX_MARKERS_ON_MAP) {
        resetMarkers();
        addMarkers(listItems);
    }
    addMarkers(items);
}

function cardHtml(item) {
    const st = getStatusInfo(item.status);
    const dist = distanceFromUser(item);
    const distBadge = dist !== null
        ? `<span class="distance-badge">${escapeHtml(formatDistance(dist))}</span>`
        : '';

    const address = itemAddress(item);
    const primaryHeading = address
        || item.nazwa_obiektu
        || `${Number(item.szerokosc_geo).toFixed(5)}, ${Number(item.dlugosc_geo).toFixed(5)}`;
    const secondaryName = address && item.nazwa_obiektu && item.nazwa_obiektu !== 'Defibrylator AED'
        ? `<p class="nearest-card-meta"><strong>${escapeHtml(item.nazwa_obiektu)}</strong></p>`
        : '';
    const googleMapsUrl = `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(`${item.szerokosc_geo},${item.dlugosc_geo}`)}`;

    return `
        <article class="nearest-card" role="link" tabindex="0"
                 data-id="${escapeHtml(item.id_zewnetrzne || '')}"
                 data-gmaps-url="${escapeHtml(googleMapsUrl)}"
                 aria-label="Otwórz w Mapach Google: ${escapeHtml(primaryHeading)}">
            <div class="nearest-card-top">
                <span class="nearest-card-title">${escapeHtml(primaryHeading)}</span>
                ${distBadge}
            </div>
            ${secondaryName}
            ${item.opis_lokalizacji ? `<p class="nearest-card-meta">${escapeHtml(item.opis_lokalizacji)}</p>` : ''}
            <span class="status-pill ${st.cls}">${escapeHtml(st.label)} • ${escapeHtml(item.dostepnosc || 'brak danych')}</span>
        </article>
    `;
}

function bindCard(card) {
    const openInGoogleMaps = () => {
        const url = card.dataset.gmapsUrl;
        if (url) window.open(url, '_blank', 'noopener,noreferrer');
    };
    card.addEventListener('click', openInGoogleMaps);
    card.addEventListener('keydown', e => {
        if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            openInGoogleMaps();
        }
    });
}

let listVersion = 0;

function renderNearestList(items) {
    const listContainer = document.getElementById('nearest-list');
    if (!listContainer) return;
    const version = ++listVersion;

    if (items.length === 0) {
        listContainer.innerHTML = '<p class="helper-text">Brak urządzeń AED ze współrzędnymi GPS w aktualnej bazie danych.</p>';
        return;
    }

    listContainer.innerHTML = items.map(cardHtml).join('');
    listContainer.querySelectorAll('.nearest-card').forEach(bindCard);

    // Karty bez adresu uzupełniamy po kolei od najbliższej (serwer geokoduje ~1 punkt/s); wynik wraca do karty i dymka.
    (async () => {
        for (const item of items) {
            if (version !== listVersion) return; // lista została przerysowana
            if (!(await resolveAddress(item)) || version !== listVersion) continue;
            const card = Array.from(listContainer.querySelectorAll('.nearest-card')).find(c => c.dataset.id === (item.id_zewnetrzne || ''));
            if (!card) continue;
            const fresh = document.createElement('div');
            fresh.innerHTML = cardHtml(item).trim();
            const replacement = fresh.firstElementChild;
            card.replaceWith(replacement);
            bindCard(replacement);
        }
    })();
}

// Pierwsze załadowanie wokół punktu (lokalizacja użytkownika albo środek Polski): znaczniki, lista i kadr mapy.
function renderAedAround(items, center, radiusKm) {
    if (!aedMap || !aedMarkersLayer) return;
    aedMap.invalidateSize();
    resetMarkers();
    loadedView = { lat: center[0], lon: center[1], radiusKm };

    // z lokalizacją pozycje są już posortowane od najbliższej; bez niej lista pokazuje najbliższe środkowi mapy
    listItems = items.slice(0, 6);
    addMarkers(items);
    renderNearestList(listItems);

    ignoreMovesUntil = Date.now() + 1500; // kadrowanie poniżej wywoła moveend, którego nie traktujemy jako ruchu użytkownika
    if (items.length === 0) {
        aedMap.setView(center, userPos ? 14 : 6);
    } else if (userPos) {
        aedMap.fitBounds(L.latLngBounds([[userPos.lat, userPos.lon], [items[0].szerokosc_geo, items[0].dlugosc_geo]]),
            { padding: [60, 60], maxZoom: 15 });
    } else {
        aedMap.fitBounds(L.featureGroup(Array.from(markerByKey.values())).getBounds(), { padding: [40, 40], maxZoom: 13 });
    }
}

function onMapMoved() {
    if (Date.now() < ignoreMovesUntil || !loadedView) return;
    clearTimeout(moveTimer);
    moveTimer = setTimeout(loadMoreForViewport, 450);
}

// Po przesunięciu/oddaleniu mapy: jeśli widok wychodzi poza pobrany obszar, dociągnij najbliższe punkty od środka widoku.
async function loadMoreForViewport() {
    if (!aedMap || !loadedView || loadedView.radiusKm === null) return; // pobrano już wszystko
    const bounds = aedMap.getBounds();
    const center = aedMap.getCenter();
    const farthestCorner = Math.max(...[bounds.getNorthEast(), bounds.getNorthWest(), bounds.getSouthEast(), bounds.getSouthWest()]
        .map(c => haversineKm(loadedView.lat, loadedView.lon, c.lat, c.lng)));
    if (farthestCorner <= loadedView.radiusKm) return; // cały widok jest pokryty
    // samo oddalenie bez przesunięcia dałoby te same punkty
    if (haversineKm(loadedView.lat, loadedView.lon, center.lat, center.lng) < loadedView.radiusKm * 0.1) return;

    const seq = ++fetchSeq;
    try {
        const data = await fetchNearby(center.lat, center.lng);
        if (seq !== fetchSeq) return; // w międzyczasie ruszono mapę dalej
        showMoreMarkers(data.items);
        loadedView = { lat: center.lat, lon: center.lng, radiusKm: data.radiusKm };
    } catch (_) {
        // zostają dotychczasowe znaczniki; kolejny ruch mapy spróbuje ponownie
    }
}

async function loadAround(lat, lon) {
    const geoStatus = document.getElementById('geo-status');
    const seq = ++fetchSeq;
    try {
        const data = await fetchNearby(lat, lon);
        if (seq !== fetchSeq) return;
        renderAedAround(data.items, [lat, lon], data.radiusKm);
    } catch (_) {
        if (geoStatus) {
            geoStatus.className = 'geo-status error';
            geoStatus.textContent = 'Nie udało się pobrać listy defibrylatorów. Spróbuj ponownie za chwilę.';
        }
    }
}

async function locateAndLoadNearestAed() {
    const geoStatus = document.getElementById('geo-status');
    if (!ensureMapInitialized()) {
        if (geoStatus) {
            geoStatus.className = 'geo-status error';
            geoStatus.textContent = 'Nie udało się załadować mapy. Sprawdź połączenie z internetem i odśwież stronę.';
        }
        return;
    }

    setTimeout(() => {
        if (aedMap) aedMap.invalidateSize();
    }, 100);

    userPos = null;
    if (!('geolocation' in navigator)) {
        if (geoStatus) {
            geoStatus.className = 'geo-status error';
            geoStatus.textContent = 'Twoja przeglądarka nie obsługuje geolokalizacji. Wyświetlono defibrylatory ze środka Polski; przesuń mapę, aby wczytać kolejne.';
        }
        await loadAround(DEFAULT_CENTER[0], DEFAULT_CENTER[1]);
        return;
    }

    if (geoStatus) {
        geoStatus.className = 'geo-status';
        geoStatus.textContent = 'Pobieranie Twojej lokalizacji GPS...';
    }

    navigator.geolocation.getCurrentPosition(
        pos => {
            const userLat = pos.coords.latitude;
            const userLon = pos.coords.longitude;
            userPos = { lat: userLat, lon: userLon };

            if (geoStatus) {
                geoStatus.className = 'geo-status success';
                geoStatus.textContent = `Twoja lokalizacja: ${userLat.toFixed(5)}, ${userLon.toFixed(5)}. Posortowano defibrylatory od najbliższego.`;
            }

            if (aedMap) {
                if (userMarker) {
                    userMarker.setLatLng([userLat, userLon]);
                } else {
                    const userIcon = L.divIcon({
                        className: '',
                        html: '<div class="user-pulse-dot"></div>',
                        iconSize: [18, 18],
                        iconAnchor: [9, 9]
                    });
                    userMarker = L.marker([userLat, userLon], { icon: userIcon })
                        .bindPopup('<strong>Twoja aktualna lokalizacja</strong>')
                        .addTo(aedMap);
                }
            }

            loadAround(userLat, userLon);
        },
        () => {
            if (geoStatus) {
                geoStatus.className = 'geo-status error';
                geoStatus.textContent = 'Brak zgody na lokalizację lub niedostępny sygnał GPS. Wyświetlono defibrylatory ze środka Polski; przesuń mapę, aby wczytać kolejne.';
            }
            loadAround(DEFAULT_CENTER[0], DEFAULT_CENTER[1]);
        },
        { enableHighAccuracy: true, timeout: 10000, maximumAge: 30000 }
    );
}

const refreshLocationBtn = document.getElementById('refresh-location-btn');
if (refreshLocationBtn) {
    refreshLocationBtn.addEventListener('click', () => {
        locateAndLoadNearestAed();
    });
}

if ('geolocation' in navigator && navigator.permissions && navigator.permissions.query) {
    navigator.permissions.query({ name: 'geolocation' }).then(perm => {
        if (perm.state === 'granted') {
            navigator.geolocation.getCurrentPosition(() => {}, () => {}, { maximumAge: 60000 });
        }
    }).catch(() => {});
}

const uploadForm = document.getElementById('upload-form');
const fileInput = document.getElementById('excel-file');
const fileSelectedInfo = document.getElementById('file-selected-info');
const declarationCheckbox = document.getElementById('data-declaration');
const submitBtn = document.getElementById('submit-btn');
const resultBox = document.getElementById('result-box');

if (fileInput) {
    fileInput.addEventListener('change', () => {
        clearResult(resultBox);
        if (fileInput.files && fileInput.files.length > 0) {
            const file = fileInput.files[0];
            const sizeKB = (file.size / 1024).toFixed(1);
            fileSelectedInfo.classList.add('visible');
            fileSelectedInfo.innerHTML = `Wczytano plik do formularza: <strong>${escapeHtml(file.name)}</strong> (${sizeKB} KB). Kliknij przycisk poniżej, aby zweryfikować jego zawartość.`;
        } else {
            fileSelectedInfo.classList.remove('visible');
            fileSelectedInfo.innerHTML = '';
        }
    });
}

if (uploadForm) {
    uploadForm.addEventListener('submit', async e => {
        e.preventDefault();
        if (submitBtn.getAttribute('aria-disabled') === 'true') return;
        if (!fileInput.files.length) return;

        if (!declarationCheckbox.checked) {
            showResult(resultBox, 'error', '<strong>Wymagane oświadczenie:</strong> Zaznacz oświadczenie o rzetelności przekazywanych danych przed wysłaniem pliku.');
            declarationCheckbox.focus();
            return;
        }

        const uploadedFileName = fileInput.files[0].name;
        const formData = new FormData();
        formData.append('file', fileInput.files[0]);
        formData.append('declaration', 'true');

        setBusy(submitBtn, true, 'Trwa wczytywanie i walidacja...');
        clearResult(resultBox);

        try {
            const response = await fetch('/upload-aed', {
                method: 'POST',
                body: formData
            });
            const data = await response.json();

            if (!response.ok) {
                showResult(resultBox, 'error', `<strong>Błąd pliku (${escapeHtml(uploadedFileName)}):</strong> ${escapeHtml(data.detail || 'Wystąpił nieoczekiwany błąd serwera.')}`);
                return;
            }

            if (data.status === 'accepted') {
                showResult(resultBox, 'success', `<strong>${escapeHtml(data.message).replace(/\n/g, '<br>')}</strong>`);
                fileSelectedInfo.classList.remove('visible');
                fileSelectedInfo.innerHTML = '';
                uploadForm.reset();
            } else if (data.status === 'validation_errors') {
                const rowsHtml = data.errors.map(err => `
                    <tr>
                        <td>${escapeHtml(err.wiersz)}</td>
                        <td><code>${escapeHtml(err.pole)}</code></td>
                        <td>${escapeHtml(err.komunikat)}</td>
                    </tr>
                `).join('');

                showResult(resultBox, 'error', `
                    <strong>Wykryto błędy w pliku „${escapeHtml(uploadedFileName)}”!</strong>
                    <p class="result-subtext">${escapeHtml(data.message)} Dane <strong>nie zostały</strong> dodane do bazy.</p>
                    <table aria-label="Lista błędów w pliku Excel">
                        <thead>
                            <tr>
                                <th scope="col">Wiersz w Excelu</th>
                                <th scope="col">Kolumna</th>
                                <th scope="col">Opis błędu</th>
                            </tr>
                        </thead>
                        <tbody>${rowsHtml}</tbody>
                    </table>
                `);
            } else if (data.status === 'rejected') {
                const missingHtml = data.details && data.details.length > 0
                    ? `<p class="result-subtext">Brakujące kolumny: ${data.details.map(c => `<code>${escapeHtml(c)}</code>`).join(', ')}</p>`
                    : '';
                showResult(resultBox, 'error', `
                    <strong>Odrzucono plik „${escapeHtml(uploadedFileName)}”!</strong>
                    <p class="result-subtext">Powód: ${escapeHtml(data.reason)}</p>
                    ${missingHtml}
                `);
            } else {
                showResult(resultBox, 'error', `<strong>Nieznana odpowiedź serwera:</strong> ${escapeHtml(JSON.stringify(data))}`);
            }
        } catch (err) {
            showResult(resultBox, 'error', `<strong>Błąd połączenia z serwerem:</strong> Nie udało się wysłać pliku (${escapeHtml(err.message)}). Sprawdź, czy serwer działa.`);
        } finally {
            setBusy(submitBtn, false, 'Zweryfikuj i dodaj do bazy');
        }
    });
}

const singleForm = document.getElementById('single-form');
const singleSubmitBtn = document.getElementById('single-submit-btn');
const singleResultBox = document.getElementById('single-result-box');

function clearFieldErrors() {
    if (!singleForm) return;
    singleForm.querySelectorAll('.invalid').forEach(el => {
        el.classList.remove('invalid', 'clearing-invalid');
        el.removeAttribute('aria-invalid');
        el.removeAttribute('aria-describedby');
    });
    singleForm.querySelectorAll('.field-error').forEach(el => el.remove());
}

function showFieldError(fieldElement, message) {
    if (!fieldElement) return;
    const wrapper = fieldElement.closest('.field') || fieldElement.parentElement;
    if (wrapper.querySelector('.field-error')) return;

    const errorEl = document.createElement('small');
    errorEl.className = 'field-error';
    errorEl.textContent = message;
    if (fieldElement.id) {
        errorEl.id = `${fieldElement.id}-error`;
        fieldElement.setAttribute('aria-describedby', errorEl.id);
    }
    wrapper.appendChild(errorEl);
}

/* --- Smok ziejący ogniem na błędne pola (v1) --- */
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
let dragonEl = null;
let dragonTaskId = 0;

function getOrCreateDragon() {
    if (dragonEl) return dragonEl;
    dragonEl = document.createElement('div');
    dragonEl.className = 'fire-dragon';
    dragonEl.setAttribute('aria-hidden', 'true');
    dragonEl.innerHTML = `
        <svg viewBox="0 0 180 120" width="150" height="110" xmlns="http://www.w3.org/2000/svg">
            <g class="dragon-flame">
                <path d="M52 54 Q22 36 2 52 Q20 68 52 58 Z" fill="#f97316"/>
                <path d="M52 55 Q28 43 10 53 Q27 63 52 57 Z" fill="#facc15"/>
                <circle cx="14" cy="46" r="2.5" fill="#fde047"/>
                <circle cx="24" cy="63" r="2" fill="#fb923c"/>
                <circle cx="8" cy="57" r="1.8" fill="#fef08a"/>
            </g>
            <g class="dragon-body">
                <path d="M132 68 Q158 72 174 56 Q162 78 134 78 Z" fill="#b91c1c"/>
                <polygon points="174,56 166,52 169,62" fill="#dc143c"/>
                <ellipse cx="110" cy="64" rx="28" ry="18" fill="#dc143c"/>
                <path d="M92 72 Q110 82 128 72 Q110 76 92 72 Z" fill="#fca5a5"/>
                <path class="dragon-wing" d="M104 52 Q122 14 148 24 Q134 42 122 54 Z" fill="#991b1b"/>
                <path d="M88 58 Q74 44 62 46 L66 64 Q80 68 90 66 Z" fill="#dc143c"/>
                <ellipse cx="60" cy="53" rx="14" ry="11" fill="#dc143c"/>
                <polygon points="66,44 74,32 72,45" fill="#fbbf24"/>
                <polygon points="58,43 63,31 64,44" fill="#fbbf24"/>
                <circle cx="56" cy="50" r="2.6" fill="#fef08a"/>
                <circle cx="55.3" cy="50" r="1.3" fill="#0f172a"/>
                <path d="M48 56 L58 56 L51 61 Z" fill="#7f1d1d"/>
            </g>
        </svg>
    `;
    document.body.appendChild(dragonEl);
    return dragonEl;
}

async function burnInvalidFieldsWithDragon(errorItems) {
    const validTargets = errorItems
        .map(err => ({ field: singleForm.elements[err.pole], message: err.komunikat }))
        .filter(item => item.field);

    if (validTargets.length === 0) return;

    const currentTask = ++dragonTaskId;
    const dragon = getOrCreateDragon();

    const firstRect = validTargets[0].field.getBoundingClientRect();
    dragon.classList.remove('breathing', 'visible');
    dragon.style.transition = 'none';
    dragon.style.left = `${window.innerWidth + 140}px`;
    dragon.style.top = `${Math.max(70, firstRect.top - 10)}px`;
    void dragon.offsetWidth;
    dragon.style.transition = '';

    await sleep(30);
    if (currentTask !== dragonTaskId) return;
    dragon.classList.add('visible');

    for (const target of validTargets) {
        if (currentTask !== dragonTaskId) return;
        target.field.scrollIntoView({ behavior: 'smooth', block: 'center' });
        await sleep(220);

        const rect = target.field.getBoundingClientRect();
        const targetX = Math.min(window.innerWidth - 90, rect.right + 42);
        const targetY = Math.max(65, rect.top + rect.height / 2 - 6);

        dragon.style.left = `${targetX}px`;
        dragon.style.top = `${targetY}px`;
        await sleep(520);
        if (currentTask !== dragonTaskId) return;

        dragon.classList.add('breathing');
        await sleep(240);

        target.field.value = '';
        target.field.classList.add('invalid', 'clearing-invalid');
        target.field.setAttribute('aria-invalid', 'true');
        showFieldError(target.field, target.message);

        await sleep(300);
        dragon.classList.remove('breathing');
        target.field.classList.remove('clearing-invalid');
        await sleep(120);
    }

    if (currentTask !== dragonTaskId) return;
    dragon.style.left = `-180px`;
    dragon.style.top = `60px`;
    await sleep(550);
    if (currentTask !== dragonTaskId) return;
    dragon.classList.remove('visible');
}

if (singleForm) {
    singleForm.querySelectorAll('input, select').forEach(input => {
        input.addEventListener('input', () => {
            input.classList.remove('invalid', 'clearing-invalid');
            input.removeAttribute('aria-invalid');
            input.removeAttribute('aria-describedby');
            const wrapper = input.closest('.field') || input.parentElement;
            const err = wrapper.querySelector('.field-error');
            if (err) err.remove();
        });
    });

    singleForm.addEventListener('submit', async e => {
        e.preventDefault();
        if (singleSubmitBtn.getAttribute('aria-disabled') === 'true') return;
        clearResult(singleResultBox);
        clearFieldErrors();

        if (!singleForm.checkValidity()) {
            singleForm.reportValidity();
            return;
        }

        const payload = Object.fromEntries(new FormData(singleForm).entries());
        payload.declaration = singleForm.elements['declaration'].checked;

        setBusy(singleSubmitBtn, true, 'Trwa walidacja...');

        try {
            const response = await fetch('/add-aed', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            const data = await response.json();

            if (!response.ok) {
                showResult(singleResultBox, 'error', `<strong>Błąd:</strong> ${escapeHtml(data.detail || 'Wystąpił nieoczekiwany błąd serwera.')}`);
            } else if (data.status === 'accepted') {
                showResult(singleResultBox, 'success', `<strong>${escapeHtml(data.message).replace(/\n/g, '<br>')}</strong>`);
                singleForm.reset();
            } else if (data.status === 'validation_errors') {
                await burnInvalidFieldsWithDragon(data.errors);
                const firstField = singleForm.elements[data.errors[0]?.pole];
                if (firstField) firstField.focus();
            } else {
                showResult(singleResultBox, 'error', `<strong>Nieznana odpowiedź serwera:</strong> ${escapeHtml(JSON.stringify(data))}`);
            }
        } catch (err) {
            showResult(singleResultBox, 'error', `<strong>Błąd połączenia z serwerem:</strong> ${escapeHtml(err.message)}`);
        } finally {
            setBusy(singleSubmitBtn, false, 'Zweryfikuj i dodaj do bazy');
        }
    });
}

const checkAddressBtn = document.getElementById('check-address-btn');
const previewStatus = document.getElementById('preview-status');
const previewMapEl = document.getElementById('preview-map');
const geoFields = singleForm ? {
    adres: singleForm.elements['adres'],
    miasto: singleForm.elements['miasto'],
    kod: singleForm.elements['kod_pocztowy'],
    lat: singleForm.elements['szerokosc_geo'],
    lon: singleForm.elements['dlugosc_geo']
} : null;

let previewMap = null;
let previewMarker = null;
let previewRun = 0;

function setPreviewStatus(text, isError = false) {
    if (!previewStatus) return;
    previewStatus.textContent = text;
    previewStatus.classList.toggle('is-error', isError);
}

function resetPreview() {
    previewRun++;
    if (previewMapEl) previewMapEl.hidden = true;
    if (previewStatus) setPreviewStatus('');
}

function placeMarker(lat, lon, recenter) {
    if (!previewMapEl) return;
    if (typeof L === 'undefined') {
        setPreviewStatus('Nie udało się załadować mapy. Współrzędne możesz wpisać ręcznie lub pominąć ten krok.', true);
        return;
    }
    previewMapEl.hidden = false;
    if (!previewMap) {
        previewMap = L.map(previewMapEl).setView([lat, lon], 17);
        L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
            maxZoom: 19,
            attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">Współtwórcy OpenStreetMap</a>'
        }).addTo(previewMap);
        previewMap.on('click', e => moveMarker(e.latlng.lat, e.latlng.lng));
    }
    if (!previewMarker) {
        previewMarker = L.marker([lat, lon], { draggable: true, title: 'Położenie defibrylatora (przeciągnij, aby poprawić)', alt: 'Położenie defibrylatora' }).addTo(previewMap);
        previewMarker.on('dragend', () => {
            const p = previewMarker.getLatLng();
            moveMarker(p.lat, p.lng);
        });
    } else {
        previewMarker.setLatLng([lat, lon]);
    }
    if (recenter) previewMap.setView([lat, lon], 17);
    previewMap.invalidateSize();
}

function setCoordinateFields(lat, lon) {
    if (!geoFields || !geoFields.lat || !geoFields.lon) return;
    geoFields.lat.value = Number(lat).toFixed(6);
    geoFields.lon.value = Number(lon).toFixed(6);
}

async function moveMarker(lat, lon) {
    const run = ++previewRun;
    setCoordinateFields(lat, lon);
    placeMarker(lat, lon, false);
    setPreviewStatus('Sprawdzanie adresu dla wskazanego punktu...');
    try {
        const res = await fetch(`/reverse-preview?lat=${encodeURIComponent(lat)}&lon=${encodeURIComponent(lon)}`);
        const data = await res.json();
        if (run !== previewRun) return;
        if (data.found) {
            if (geoFields && geoFields.kod) geoFields.kod.value = data.kod_pocztowy || '';
            setPreviewStatus(`Wskazany punkt: ${data.adres_znaleziony}. Sprawdź, czy kod pocztowy jest poprawny.`);
        } else {
            setPreviewStatus(data.message || 'Nie znaleziono adresu dla wskazanego punktu. Kod pocztowy wpisz ręcznie.', true);
        }
    } catch (err) {
        if (run === previewRun) setPreviewStatus('Nie udało się sprawdzić adresu dla tego punktu. Kod pocztowy wpisz ręcznie.', true);
    }
}

if (checkAddressBtn && geoFields) {
    checkAddressBtn.addEventListener('click', async () => {
        if (checkAddressBtn.getAttribute('aria-disabled') === 'true') return;
        const adres = geoFields.adres.value.trim();
        const miasto = geoFields.miasto.value.trim();
        if (!adres || !miasto) {
            setPreviewStatus('Najpierw wpisz miasto i adres.', true);
            (!miasto ? geoFields.miasto : geoFields.adres).focus();
            return;
        }
        const run = ++previewRun;
        setBusy(checkAddressBtn, true, 'Szukanie adresu...');
        setPreviewStatus('Szukanie adresu...');
        try {
            const res = await fetch(`/geocode-preview?adres=${encodeURIComponent(adres)}&miasto=${encodeURIComponent(miasto)}`);
            const data = await res.json();
            if (run !== previewRun) return;
            if (!data.found) {
                setPreviewStatus(data.message || 'Nie znaleziono adresu.', true);
                return;
            }
            setCoordinateFields(data.lat, data.lon);
            if (geoFields.kod) geoFields.kod.value = data.kod_pocztowy || '';
            placeMarker(data.lat, data.lon, true);
            setPreviewStatus(`Znaleziono: ${data.adres_znaleziony || 'adres'}. Jeśli pinezka wskazuje inne miejsce, przeciągnij ją, kliknij w mapę albo popraw współrzędne.`);
        } catch (err) {
            if (run === previewRun) setPreviewStatus(`Błąd połączenia z serwerem: ${err.message}`, true);
        } finally {
            setBusy(checkAddressBtn, false, 'Sprawdź adres na mapie');
        }
    });

    const syncMarkerFromFields = () => {
        if (!geoFields.lat || !geoFields.lon) return;
        const lat = parseFloat(geoFields.lat.value.replace(',', '.'));
        const lon = parseFloat(geoFields.lon.value.replace(',', '.'));
        if (Number.isFinite(lat) && Number.isFinite(lon) && lat >= 49 && lat <= 54.9 && lon >= 14.1 && lon <= 24.2) {
            placeMarker(lat, lon, true);
        }
    };
    if (geoFields.lat) geoFields.lat.addEventListener('change', syncMarkerFromFields);
    if (geoFields.lon) geoFields.lon.addEventListener('change', syncMarkerFromFields);

    [geoFields.adres, geoFields.miasto].forEach(el => {
        if (!el) return;
        el.addEventListener('input', () => {
            if ((geoFields.lat && geoFields.lat.value) || (geoFields.lon && geoFields.lon.value) || (geoFields.kod && geoFields.kod.value) || (previewMapEl && !previewMapEl.hidden)) {
                if (geoFields.lat) geoFields.lat.value = '';
                if (geoFields.lon) geoFields.lon.value = '';
                if (geoFields.kod) geoFields.kod.value = '';
                resetPreview();
            }
        });
    });

    singleForm.addEventListener('reset', resetPreview);
}