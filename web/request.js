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

// Regiony live istnieją w DOM cały czas; zmiana treści wywołuje komunikat czytnika ekranu (WCAG 4.1.3).
function showResult(box, kind, html) {
    box.innerHTML = html;
    box.className = kind;
}

function clearResult(box) {
    box.className = '';
    box.innerHTML = '';
}

// aria-disabled zamiast disabled - fokus klawiatury nie ucieka z przycisku podczas wysyłania (WCAG 2.4.3).
function setBusy(btn, busy, label) {
    btn.setAttribute('aria-disabled', String(busy));
    btn.textContent = label;
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
                data.errors.forEach(err => clearInvalidFieldInstant(singleForm.elements[err.pole], err.komunikat));
                showResult(singleResultBox, 'error', `<strong>Formularz zawiera błędy (${data.errors.length}).</strong> Popraw zaznaczone pola i wyślij ponownie.`);
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