const escapeHtml = s => String(s).replace(/[&<>"']/g, c => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;'
}[c]));

const tabButtons = document.querySelectorAll('.tab-btn');
const tabPanels = document.querySelectorAll('.tab-panel');

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
        L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
            maxZoom: 19,
            attribution: '&copy; OpenStreetMap'
        }).addTo(aedMap);
        aedMarkersLayer = L.layerGroup().addTo(aedMap);
    } else {
        aedMap.invalidateSize();
    }
    return aedMap;
}

function renderAedOnMapAndList(items, userLat, userLon) {
    const listContainer = document.getElementById('nearest-list');
    if (!listContainer || !aedMap || !aedMarkersLayer) return;

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

    if (userLat !== null && userLon !== null && topItems.length > 0) {
        const nearest = topItems[0];
        const bounds = L.latLngBounds([
            [userLat, userLon],
            [nearest.szerokosc_geo, nearest.dlugosc_geo]
        ]);
        aedMap.fitBounds(bounds, { padding: [60, 60], maxZoom: 15 });
    } else if (topItems.length > 0) {
        const group = L.featureGroup(Array.from(markerByKey.values()));
        aedMap.fitBounds(group.getBounds(), { padding: [40, 40], maxZoom: 13 });
    }
}

async function locateAndLoadNearestAed() {
    const geoStatus = document.getElementById('geo-status');
    ensureMapInitialized();
    if (aedMap) {
        setTimeout(() => aedMap.invalidateSize(), 60);
    }

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

tabButtons.forEach(btn => {
    btn.addEventListener('click', () => {
        tabButtons.forEach(b => {
            b.classList.remove('active');
            b.setAttribute('aria-selected', 'false');
        });
        tabPanels.forEach(p => {
            p.classList.remove('active');
            p.hidden = true;
        });

        btn.classList.add('active');
        btn.setAttribute('aria-selected', 'true');
        const targetPanel = document.getElementById(btn.dataset.target);
        if (targetPanel) {
            targetPanel.classList.add('active');
            targetPanel.hidden = false;
        }

        if (btn.dataset.target === 'panel-nearest') {
            locateAndLoadNearestAed();
        }
    });
});

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
        resultBox.className = '';
        if (fileInput.files && fileInput.files.length > 0) {
            const file = fileInput.files[0];
            const sizeKB = (file.size / 1024).toFixed(1);
            fileSelectedInfo.style.display = 'block';
            fileSelectedInfo.innerHTML = `Wczytano plik do formularza: <strong>${escapeHtml(file.name)}</strong> (${sizeKB} KB). Kliknij przycisk poniżej, aby zweryfikować jego zawartość.`;
        } else {
            fileSelectedInfo.style.display = 'none';
        }
    });
}

if (uploadForm) {
    uploadForm.addEventListener('submit', async e => {
        e.preventDefault();
        if (!fileInput.files.length) return;

        if (!declarationCheckbox.checked) {
            resultBox.className = 'error';
            resultBox.innerHTML = '<strong>Wymagane oświadczenie:</strong> Zaznacz oświadczenie o rzetelności przekazywanych danych przed wysłaniem pliku.';
            declarationCheckbox.focus();
            return;
        }

        const uploadedFileName = fileInput.files[0].name;
        const formData = new FormData();
        formData.append('file', fileInput.files[0]);
        formData.append('declaration', 'true');

        submitBtn.disabled = true;
        submitBtn.textContent = 'Trwa wczytywanie i walidacja...';
        resultBox.className = '';

        try {
            const response = await fetch('/upload-aed', {
                method: 'POST',
                body: formData
            });
            const data = await response.json();

            if (!response.ok) {
                resultBox.className = 'error';
                resultBox.innerHTML = `<strong>Błąd pliku (${escapeHtml(uploadedFileName)}):</strong> ${escapeHtml(data.detail || 'Wystąpił nieoczekiwany błąd serwera.')}`;
                return;
            }

            if (data.status === 'accepted') {
                resultBox.className = 'success';
                resultBox.innerHTML = `<strong>${escapeHtml(data.message).replace(/\n/g, '<br>')}</strong>`;
                fileSelectedInfo.style.display = 'none';
                uploadForm.reset();
            } else if (data.status === 'validation_errors') {
                resultBox.className = 'error';
                const rowsHtml = data.errors.map(err => `
                    <tr>
                        <td>${escapeHtml(err.wiersz)}</td>
                        <td><code>${escapeHtml(err.pole)}</code></td>
                        <td>${escapeHtml(err.komunikat)}</td>
                    </tr>
                `).join('');

                resultBox.innerHTML = `
                    <strong>Wykryto błędy w pliku „${escapeHtml(uploadedFileName)}”!</strong>
                    <p class="result-subtext">${escapeHtml(data.message)} Dane <strong>nie zostały</strong> dodane do bazy.</p>
                    <table aria-label="Lista błędów w pliku Excel">
                        <thead>
                            <tr>
                                <th>Wiersz w Excelu</th>
                                <th>Kolumna</th>
                                <th>Opis błędu</th>
                            </tr>
                        </thead>
                        <tbody>${rowsHtml}</tbody>
                    </table>
                `;
            } else if (data.status === 'rejected') {
                resultBox.className = 'error';
                const missingHtml = data.details && data.details.length > 0
                    ? `<p class="result-subtext">Brakujące kolumny: ${data.details.map(c => `<code>${escapeHtml(c)}</code>`).join(', ')}</p>`
                    : '';
                resultBox.innerHTML = `
                    <strong>Odrzucono plik „${escapeHtml(uploadedFileName)}”!</strong>
                    <p class="result-subtext">Powód: ${escapeHtml(data.reason)}</p>
                    ${missingHtml}
                `;
            } else {
                resultBox.className = 'error';
                resultBox.innerHTML = `<strong>Nieznana odpowiedź serwera:</strong> ${escapeHtml(JSON.stringify(data))}`;
            }
        } catch (err) {
            resultBox.className = 'error';
            resultBox.innerHTML = `<strong>Błąd połączenia z serwerem:</strong> Nie udało się wysłać pliku (${escapeHtml(err.message)}). Sprawdź, czy serwer działa.`;
        } finally {
            submitBtn.disabled = false;
            submitBtn.textContent = 'Zweryfikuj i dodaj do bazy';
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
    wrapper.appendChild(errorEl);
}

function clearInvalidFieldInstant(fieldElement, message) {
    if (!fieldElement) return;
    fieldElement.value = '';
    fieldElement.classList.add('invalid', 'clearing-invalid');
    fieldElement.setAttribute('aria-invalid', 'true');
    showFieldError(fieldElement, message);
    setTimeout(() => fieldElement.classList.remove('clearing-invalid'), 350);
}

if (singleForm) {
    singleForm.querySelectorAll('input, select').forEach(input => {
        input.addEventListener('input', () => {
            input.classList.remove('invalid', 'clearing-invalid');
            input.removeAttribute('aria-invalid');
            const wrapper = input.closest('.field') || input.parentElement;
            const err = wrapper.querySelector('.field-error');
            if (err) err.remove();
        });
    });

    singleForm.addEventListener('submit', async e => {
        e.preventDefault();
        singleResultBox.className = '';
        clearFieldErrors();

        if (!singleForm.checkValidity()) {
            singleForm.reportValidity();
            return;
        }

        const payload = Object.fromEntries(new FormData(singleForm).entries());
        payload.declaration = singleForm.elements['declaration'].checked;

        singleSubmitBtn.disabled = true;
        singleSubmitBtn.textContent = 'Trwa walidacja...';

        try {
            const response = await fetch('/add-aed', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            const data = await response.json();

            if (!response.ok) {
                singleResultBox.className = 'error';
                singleResultBox.innerHTML = `<strong>Błąd:</strong> ${escapeHtml(data.detail || 'Wystąpił nieoczekiwany błąd serwera.')}`;
            } else if (data.status === 'accepted') {
                singleResultBox.className = 'success';
                singleResultBox.innerHTML = `<strong>${escapeHtml(data.message).replace(/\n/g, '<br>')}</strong>`;
                singleForm.reset();
            } else if (data.status === 'validation_errors') {
                data.errors.forEach(err => clearInvalidFieldInstant(singleForm.elements[err.pole], err.komunikat));
                const firstField = singleForm.elements[data.errors[0]?.pole];
                if (firstField) firstField.focus();
            } else {
                singleResultBox.className = 'error';
                singleResultBox.innerHTML = `<strong>Nieznana odpowiedź serwera:</strong> ${escapeHtml(JSON.stringify(data))}`;
            }
        } catch (err) {
            singleResultBox.className = 'error';
            singleResultBox.innerHTML = `<strong>Błąd połączenia z serwerem:</strong> ${escapeHtml(err.message)}`;
        } finally {
            singleSubmitBtn.disabled = false;
            singleSubmitBtn.textContent = 'Zweryfikuj i dodaj do bazy';
        }
    });
}