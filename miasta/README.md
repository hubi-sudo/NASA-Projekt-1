# Projekt NASA

Projekt pokazuje, jak z perspektywy satelity wyglądają Kraków, Katowice wraz z GZM, Kielce i Częstochowa. Wszystkie dane pochodzą z jednego zdjęcia satelitarnego Landsat 8 wykonanego 7 sierpnia 2013 roku.

## O projekcie

Celem projektu było sprawdzenie, czy na zdjęciach satelitarnych można rozpoznać obszary zabudowane.

Na mapie można przełączać się między:
- naturalnymi kolorami,
- kompozycjami w fałszywych barwach,
- wskaźnikami zabudowy NDBI i IBI,
- wskaźnikiem wody MNDWI.

Dwie wybrane warstwy można również porównać za pomocą suwaka.

## Wyniki

W przypadku zdjęcia wykonanego w sierpniu wskaźniki NDBI i IBI nie zawsze dobrze pokazują zabudowę. Obszary pól po żniwach mogą wyglądać podobnie do terenów miejskich, ponieważ mają mało roślinności.

Lepsze rezultaty można uzyskać, łącząc kilka wskaźników, np. NDBI lub IBI z MNDWI.

## Struktura projektu

- `src/` – kod projektu i obliczenia
- `data/` – dane satelitarne i granice miast
- `site/` – gotowa strona internetowa
- `outputs/` – wykresy i wyniki
- `tests/` – testy
- `tools/` – dodatkowe skrypty do przygotowania danych

Folder `site/` jest generowany automatycznie, dlatego nie należy edytować go ręcznie.

## Dane i wykorzystane narzędzia

W projekcie wykorzystano zdjęcie satelitarne Landsat 8 oraz dane dotyczące granic miast. Do przygotowania mapy wykorzystano bibliotekę Leaflet.

Wskaźniki NDBI, IBI i MNDWI zostały obliczone na podstawie odpowiednich pasm obrazu satelitarnego.


