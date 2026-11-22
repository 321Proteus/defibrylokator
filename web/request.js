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

function ensureMapInitialized(centerLat = 52.0693, centerLon = 19.4803, zoom = 6) {
    const mapContainer = document.getElementById('aed-map');
    if (!mapContainer || typeof L === 'undefined') return null;

    if (!aedMap) {
        aedMap = L.map('aed-map').setView([centerLat, centerLon], zoom);
        L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
            maxZoom: 19,
            attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">Współtwórcy OpenStreetMap</a>'
        }).addTo(aedMap);
        aedMarkersLayer = L.layerGroup().addTo(aedMap);
    }
    aedMap.invalidateSize();
    return aedMap;
}

function renderAedOnMapAndList(items, userLat, userLon) {
    const listContainer = document.getElementById('nearest-list');
    if (!listContainer || !aedMap || !aedMarkersLayer) return;

    aedMap.invalidateSize();
    aedMarkersLayer.clearLayers();
    markerByKey.clear();

    const enriched = items.map((item, idx) => {
        const dist = (userLat !== null && userLon !== null)
            ? haversineKm(userLat, userLon, item.szerokosc_geo, item.dlugosc_geo)
            : null;
        return { ...item, dist, _key: `${idx}-${item.szerokosc_geo}-${item.dlugosc_geo}` };
    });

    if (userLat !== null && userLon !== null) {
        enriched.sort((a, b) => a.dist - b.dist);
    }

    const aedIcon = L.divIcon({
        className: '',
        html: '<div class="aed-pin-dot">+</div>',
        iconSize: [24, 24],
        iconAnchor: [12, 12],
        popupAnchor: [0, -12]
    });

    enriched.forEach(item => {
        const st = getStatusInfo(item.status);
        const distLine = item.dist !== null
            ? `<br><strong>Odległość:</strong> ${escapeHtml(formatDistance(item.dist))}`
            : '';
        const popupHtml = `
            <div style="font-family: Inter, sans-serif; font-size: 13px; line-height: 1.45;">
                <strong>${escapeHtml(item.nazwa_obiektu)}</strong><br>
                ${escapeHtml(item.ulica_i_numer)}, ${escapeHtml(item.miasto)}<br>
                <em>${escapeHtml(item.opis_lokalizacji)}</em><br>
                <strong>Dostępność:</strong> ${escapeHtml(item.dostepnosc || 'brak danych')}<br>
                <strong>Status:</strong> ${escapeHtml(st.label)}${distLine}
            </div>
        `;
        const marker = L.marker([item.szerokosc_geo, item.dlugosc_geo], { icon: aedIcon })
            .bindPopup(popupHtml)
            .addTo(aedMarkersLayer);
        markerByKey.set(item._key, marker);
    });

    const topItems = enriched.slice(0, 6);
    if (topItems.length === 0) {
        listContainer.innerHTML = '<p class="helper-text">Brak urządzeń AED ze współrzędnymi GPS w aktualnej bazie danych.</p>';
        if (userLat !== null && userLon !== null) {
            aedMap.setView([userLat, userLon], 14);
        } else {
            aedMap.setView([52.0693, 19.4803], 6);
        }
        return;
    }

    listContainer.innerHTML = topItems.map(item => {
        const st = getStatusInfo(item.status);
        const distBadge = item.dist !== null
            ? `<span class="distance-badge">${escapeHtml(formatDistance(item.dist))}</span>`
            : '';
        return `
            <article class="nearest-card" data-key="${escapeHtml(item._key)}">
                <div class="nearest-card-top">
                    <span class="nearest-card-title">${escapeHtml(item.nazwa_obiektu)}</span>
                    ${distBadge}
                </div>
                <p class="nearest-card-meta">${escapeHtml(item.ulica_i_numer)}, ${escapeHtml(item.miasto)}</p>
                <p class="nearest-card-meta">${escapeHtml(item.opis_lokalizacji)}</p>
                <span class="status-pill ${st.cls}">${escapeHtml(st.label)} • ${escapeHtml(item.dostepnosc || 'brak danych')}</span>
            </article>
        `;
    }).join('');

    listContainer.querySelectorAll('.nearest-card').forEach(card => {
        card.addEventListener('click', () => {
            const key = card.dataset.key;
            const marker = markerByKey.get(key);
            if (marker && aedMap) {
                const latLng = marker.getLatLng();
                aedMap.setView(latLng, 16);
                marker.openPopup();
            }
        });
    });

    if (userLat !== null && userLon !== null) {
        const nearest = topItems[0];
        const bounds = L.latLngBounds([
            [userLat, userLon],
            [nearest.szerokosc_geo, nearest.dlugosc_geo]
        ]);
        aedMap.fitBounds(bounds, { padding: [60, 60], maxZoom: 15 });
    } else {
        const group = L.featureGroup(Array.from(markerByKey.values()));
        aedMap.fitBounds(group.getBounds(), { padding: [40, 40], maxZoom: 13 });
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

    let items = [];
    try {
        const res = await fetch('/api/aed-list');
        if (res.ok) {
            const data = await res.json();
            items = data.items || [];
        }
    } catch (_) {
        items = [];
    }

    if (!('geolocation' in navigator)) {
        if (geoStatus) {
            geoStatus.className = 'geo-status error';
            geoStatus.textContent = 'Twoja przeglądarka nie obsługuje geolokalizacji. Wyświetlono wszystkie AED z bazy.';
        }
        renderAedOnMapAndList(items, null, null);
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

            renderAedOnMapAndList(items, userLat, userLon);
        },
        () => {
            if (geoStatus) {
                geoStatus.className = 'geo-status error';
                geoStatus.textContent = 'Brak zgody na lokalizację lub niedostępny sygnał GPS. Wyświetlono defibrylatory z bazy.';
            }
            renderAedOnMapAndList(items, null, null);
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