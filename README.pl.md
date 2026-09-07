# Osobiste bottles Homebrew dla Sequoia Intel

Projekt dla `adriank1410`: macOS 15 / Intel / Homebrew w `/usr/local`.
**Wersja 0.1.0 jest implementacją do walidacji natywnej, nie deklaracją działającej
usługi. Rejestr początkowo jest pusty.**

Nie podmieniamy `homebrew/core`, nie zmieniamy jego adresu, nie tworzymy kopii
formuł z nazwami `-intel` ani nie migrujemy bibliotek do obcego tapa. Budujemy
niezmienione oficjalne formuły na `macos-15-intel`. Ich bottles zachowują tożsamość
`homebrew/core`. Własny tap dostarcza komendę `brew intel` i metadane artefaktów.

Cena tego uproszczenia: zwykłe `brew upgrade` nie zna dodatkowego rejestru.
Zastępuje je jawne `brew intel upgrade --apply` dla aktualizacji przez ten mechanizm.
Bez `--apply` powstaje tylko plan. Nie zmieniamy `.zshrc` ani aliasu `brew`.

## Walidacja i uruchamianie buildów

Repozytorium zostało już utworzone. Lokalnie można uruchomić:

```sh
python3 -m unittest discover -s tests -v
python3 scripts/check-project.py
python3 scripts/deploy.py  # tylko dry-run / helper dla świeżego wdrożenia
```

Pojedynczy build można zlecić przez zmianę `policy/build-request.json` na `main`:
wskazać nazwę z zatwierdzonej listy i zwiększyć `sequence`. Zwykłe zmiany
dokumentacji lub kodu nie uruchamiają kosztownego Intel CI. Ustawienia ochrony
repo należy zastosować i zweryfikować osobno po stronie GitHuba.

**Wysłanie workflowu do wykonania nie oznacza udanej kompilacji.** Należy sprawdzić
budowanie, instalację na świeżym drugim runnerze, test formuły, linkage i publikację.
Publikator tworzy gałąź przeglądową rejestru i link porównania; PR otwiera
właściciel lub autoryzowany konektor po sprawdzeniu natywnego runu.

## Korzystanie po udanym wdrożeniu

```sh
brew tap adriank1410/intel
brew trust --command adriank1410/intel/intel
brew intel doctor
brew intel plan simdutf
brew intel upgrade simdutf --apply
```

Zaufanie konkretnej komendzie ogranicza automatyczne ładowanie innych elementów
tapa, ale nie izoluje jej kodu od Maca. Klient sprawdza SHA-256, receptę, wersję i
rewizję, zależności oraz attestation z właściwego repozytorium, gałęzi, workflowu
i commita. Potrzebuje istniejącego `gh`; nie instaluje go automatycznie.

Brak dopasowanego bottle'a zatrzymuje operację zamiast kompilować lokalnie. Nie
ma `rm`, odinstalowywania, wymuszania nadpisania, czyszczenia starych wersji ani
zmian globalnych. Nie jest to transakcja atomowa: przy błędzie późniejszego
pakietu wcześniejsze instalacje mogą pozostać, a dziennik wskazuje ich zakres.

44 nazwy w `policy/targets.json` to kandydaci wybrani z audytu, nie gwarancja
44 udanych buildów. Ciężkie kompilacje i niezweryfikowane obowiązki licencyjne
zatrzymują publikację. Eksport Twojego Maca i lista casków nie są publikowane.

Minuty standardowych runnerów publicznych repozytoriów są obecnie darmowe, ale
przechowywanie artefaktów ma odrębne limity. Retencja między etapami wynosi jeden
dzień. Harmonogram jest domyślnie wyłączony. Nie ma automatycznego wyboru płatnych
ani self-hosted runnerów. Intel runners wymagają ponownej oceny przed wycofaniem
zapowiedzianym na sierpień 2027 r.

Szczegóły: [README](README.md), [architektura](docs/ARCHITECTURE.md),
[operacje](docs/OPERATIONS.md), [bezpieczeństwo](SECURITY.md),
[licencje programów](THIRD_PARTY.md).
