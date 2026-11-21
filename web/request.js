// Obsługa zakładek
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

// Obsługa formularza wgrywania pliku
const form = document.getElementById('upload-form');
const fileInput = document.getElementById('excel-file');
const fileSelectedInfo = document.getElementById('file-selected-info');
const declarationCheckbox = document.getElementById('data-declaration');
const submitBtn = document.getElementById('submit-btn');
const resultBox = document.getElementById('result-box');

fileInput.addEventListener('change', () => {
    resultBox.className = '';

    if (fileInput.files && fileInput.files.length > 0) {
        const file = fileInput.files[0];
        const sizeKB = (file.size / 1024).toFixed(1);
        fileSelectedInfo.style.display = 'block';
        fileSelectedInfo.innerHTML = `Wczytano plik do formularza: <strong>${file.name}</strong> (${sizeKB} KB). Kliknij przycisk poniżej, aby zweryfikować jego zawartość.`;
    } else {
        fileSelectedInfo.style.display = 'none';
    }
});

form.addEventListener('submit', async (e) => {
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
            resultBox.innerHTML = `<strong>Błąd pliku (${uploadedFileName}):</strong> ${data.detail || 'Wystąpił nieoczekiwany błąd serwera.'}`;
            return;
        }

        if (data.status === 'accepted') {
            resultBox.className = 'success';
            const formattedMessage = data.message.replace(/\n/g, '<br>');
            resultBox.innerHTML = `<strong>${formattedMessage}</strong>`;
            fileSelectedInfo.style.display = 'none';
            form.reset();
        } else if (data.status === 'validation_errors') {
            resultBox.className = 'error';
            let rowsHtml = data.errors.map(err => `
                <tr>
                    <td>${err.wiersz}</td>
                    <td><code>${err.pole}</code></td>
                    <td>${err.komunikat}</td>
                </tr>
            `).join('');

            resultBox.innerHTML = `
                <strong>Wykryto błędy w pliku „${uploadedFileName}”!</strong>
                <p style="margin: 0.5rem 0;">${data.message} Dane <strong>nie zostały</strong> dodane do bazy.</p>
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
            let missingHtml = '';
            if (data.details && data.details.length > 0) {
                const cols = data.details.map(c => `<code>${c}</code>`).join(', ');
                missingHtml = `<p style="margin-top: 0.5rem;">Brakujące kolumny: ${cols}</p>`;
            }
            resultBox.innerHTML = `
                <strong>Odrzucono plik „${uploadedFileName}”!</strong>
                <p style="margin: 0.5rem 0 0 0;">Powód: ${data.reason}</p>
                ${missingHtml}
            `;
        } else {
            resultBox.className = 'error';
            resultBox.innerHTML = `<strong>Nieznana odpowiedź serwera:</strong> ${JSON.stringify(data)}`;
        }

    } catch (err) {
        resultBox.className = 'error';
        resultBox.innerHTML = `<strong>Błąd połączenia z serwerem:</strong> Nie udało się wysłać pliku (${err.message}). Sprawdź, czy serwer działa.`;
    } finally {
        submitBtn.disabled = false;
        submitBtn.textContent = 'Zweryfikuj i dodaj do bazy';
    }
});