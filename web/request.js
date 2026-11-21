const escapeHtml = s => String(s).replace(/[&<>"']/g, c => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;'
}[c]));

const tabButtons = document.querySelectorAll('.tab-btn');
const tabPanels = document.querySelectorAll('.tab-panel');

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
    });
});

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