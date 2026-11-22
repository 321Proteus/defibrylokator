# Polska Baza AED

Aplikacja do zgłaszania, importowania i wyszukiwania lokalizacji defibrylatorów AED.

## Najważniejsze katalogi

- [`documents/`](documents/) — dokumentacja projektu. Księga znaku znajduje się w pliku [`Księga_Znaku_Defibrylokator.pdf`](documents/Księga_Znaku_Defibrylokator.pdf), a wymagania w [`spec.pdf`](documents/spec.pdf).
- [`assets/`](assets/) — grafiki projektu: [`cover.svg`](assets/cover.svg), [`cover.png`](assets/cover.png) i [`icon.svg`](assets/icon.svg).
- [`web/`](web/) — strona i API: [`index.html`](web/index.html), [`style.css`](web/style.css), [`request.js`](web/request.js) oraz pliki backendu FastAPI. Logo strony to [`web/icon.png`](web/icon.png).
- [`json_source/`](json_source/) — źródłowe dane AED z Warszawy, Łodzi, Sopotu, Przemyśla i Krosna.
- [`scripts/`](scripts/) — importery JSON, eksporty do JSON/Excel oraz skrypty administracyjne.

## Jak działa aplikacja

1. Backend FastAPI wyświetla stronę z [`web/index.html`](web/index.html).
2. Użytkownik może przesłać plik Excel albo dodać pojedynczy AED.
3. Dane są walidowane, a następnie zapisywane w PostgreSQL. Bez konfiguracji bazy aplikacja korzysta z lokalnego pliku CSV.
4. Mapa pobiera lokalizację użytkownika i pokazuje najbliższe AED.
5. Dane można pobrać jako CSV, JSON lub Excel.
6. Importery z katalogu `scripts/` wczytują pliki z `json_source/` i aktualizują rekordy w bazie bez tworzenia duplikatów.

## Uruchomienie lokalne

```powershell
python -m pip install -r requirements.txt
Copy-Item .\web\.env.example .\web\.env
# uzupełnij dane bazy w .\web\.env
Set-Location .\web
python -m uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

Strona będzie dostępna pod adresem `http://127.0.0.1:8000/`.

## Import i eksport

```powershell
python scripts/import_aed_warszawa.py
python scripts/import_aed_lodz.py
python scripts/import_aed_sopot.py
python scripts/import_aed_przemysl.py
python scripts/import_aed_krosno.py

python scripts/download_defibrillators_json.py --output defibrylatory.json
python scripts/download_defibrillators_excel.py --output defibrylatory.xlsx
```

Konfiguracja bazy znajduje się lokalnie w `web/.env`. Nie należy publikować tego pliku, ponieważ zawiera dane dostępowe.

Definicja tabel bazy znajduje się w [`schema.py`](schema.py), a informacje o licencji w [`LICENSE`](LICENSE).
