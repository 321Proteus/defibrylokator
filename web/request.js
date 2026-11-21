const form = document.getElementById('upload-form');
const fileInput = document.getElementById('excel-file');
const fileSelectedInfo = document.getElementById('file-selected-info');
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

    const uploadedFileName = fileInput.files[0].name;
    const formData = new FormData();
    formData.append('file', fileInput.files[0]);

    submitBtn.disabled = true;
    submitBtn.textContent = 'Trwa wczytywanie i walidacja...';
    resultBox.className = '';

    try {
        const response = await fetch('/upload-aed', {
            method: 'POST',
            body: formData
        });

        const data = await response.json();

        // Obsługa błędów HTTP (np. 400 - zły format, 500 - błąd serwera)
        if (!response.ok) {
            resultBox.className = 'error';
            resultBox.innerHTML = `<strong>Błąd pliku (${uploadedFileName}):</strong> ${data.detail || 'Wystąpił nieoczekiwany błąd serwera.'}`;
            return;
        }

        if (data.status === 'accepted') {
            resultBox.className = 'success';
            resultBox.innerHTML = `
                <strong>Plik „${uploadedFileName}” został poprawnie wczytany i zweryfikowany!</strong>
                <p style="margin: 0.5rem 0;">${data.message}</p>
                <ul>
                    <li>Przetworzone wiersze: <strong>${data.added_records}</strong></li>
                    <li>Pominięto lub zaktualizowano duplikatów: <strong>${data.duplicates_skipped}</strong></li>
                    <li>Łączny rozmiar bazy dla dane.gov.pl: <strong>${data.total_database_size}</strong></li>
                </ul>
            `;
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
