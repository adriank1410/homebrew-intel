# Gotowe pakiety Homebrew dla Maców z Intelem

**Wersja testowa (preview):** budowanie, niezależna weryfikacja i automatyczna
publikacja butelek przeszły testy produkcyjne. Pokrycie pozostaje ograniczone;
zobacz aktualny [rejestr butelek](registry/), [wyniki walidacji](docs/VALIDATION.md)
i [warunki odbioru](CONTRIBUTING.md).

Gotowe pakiety binarne (*bottles*) dla Maców z Intelem i macOS Sequoia (15).
Komenda `brew intel` korzysta z oficjalnych pakietów, a gdy ich brakuje —
ze zweryfikowanych buildów tego repozytorium. Pakiety zachowują tożsamość
`homebrew/core`, a Homebrew pozostaje w `/usr/local`.

[English](README.md) · [Architektura](docs/ARCHITECTURE.md) ·
[Operacje](docs/OPERATIONS.md) · [Walidacja](docs/VALIDATION.md) ·
[Bezpieczeństwo](SECURITY.md) · [Dystrybucja zewnętrzna](THIRD_PARTY.md)

## Instalacja i użycie

```sh
brew tap adriank1410/intel
brew trust --command adriank1410/intel/intel
brew intel doctor
brew intel plan simdutf
brew intel upgrade simdutf --apply
```

Dwie pierwsze komendy dodają tap i nadają zaufanie jego komendzie `intel`.
Do weryfikacji poświadczeń pochodzenia GitHub potrzebny jest wcześniej zainstalowany `gh`.

`plan`, `doctor` i `upgrade` bez `--apply` nie instalują pakietów. Zmiany
pakietów wymagają `--apply`.

## Wybór aktualizacji

Dla każdej żądanej formuły klient preferuje, w tej kolejności:

1. bieżącą wersję formuły, jeśli jest już zainstalowana;
2. oficjalny pakiet Homebrew zgodny z tym Makiem;
3. zweryfikowany pakiet z rejestru tego repozytorium.

Jeśli brakuje gotowego pakietu, operacja się zatrzymuje. Klient nie kompiluje
lokalnie. Katalogi wcześniejszych wersji są zachowywane; klient nie
odinstalowuje, nie wymusza nadpisania, nie czyści i nie wycofuje pakietów.
Operacja obejmująca wiele pakietów nie jest transakcją atomową, więc późniejszy
błąd może pozostawić wcześniejsze instalacje.

Zwykłe `brew upgrade` nie korzysta z tego rejestru. Aby użyć dodatkowych pakietów,
uruchom `brew intel plan` oraz `brew intel upgrade --apply`. Jawna lista
pakietów pozwala pominąć brakującego kandydata, na przykład
`brew intel upgrade simdutf --apply` nie czeka na Qt.

Aby aktualizować tylko pakiety z kompletem dostępnych zależności:

```sh
brew update &&
brew intel upgrade --available --apply &&
brew upgrade --cask &&
brew cleanup
```

Komenda Intel obsługuje oficjalne i nasze butelki dla `homebrew/core`. Caski
aktualizuje zwykły Homebrew, z jego standardową obsługą zależności. Formuły z
innych tapów wymagają osobnych aktualizacji. Brakujące pakiety są zgłaszane
i pomijane. Bez `--available` brak kompletu nadal
zatrzymuje całą operację. Instalację uruchamiasz ręcznie; projekt nie instaluje
usługi działającej w tle na Macu. `brew cleanup` pozostaje osobnym krokiem
użytkownika i może usunąć zachowane wcześniejsze wersje.

Przed instalacją klient sprawdza sumę kontrolną, wersję i treść formuły,
zależności, pochodzenie z `homebrew/core` oraz poświadczenie GitHub.
Ochrona działająca w procesie instalatora zatrzymuje próbę kompilacji;
nie jest to izolacja na poziomie systemu operacyjnego.

Ładowanie zweryfikowanego lokalnego pakietu korzysta z wyjątku ograniczonego
do procesu Homebrew. Jawne `HOMEBREW_FORBID_PACKAGES_FROM_PATHS` nadal blokuje
instalację. Ustawienia globalne pozostają bez zmian.

## Dostępne pakiety i budowanie

[Rejestr](registry/) zawiera pakiety dostępne dla klienta.
[Lista celów](policy/targets.json) zawiera monitorowanych kandydatów, a nie
obietnicę dostępności butelek. Build mogą blokować licencje, brakujące zależności
i limity. Qt ma jawne wykluczenia. NumPy może być monitorowane, mimo że brakująca
zależność kompilacyjna GCC blokuje jego budowanie.

`brew intel coverage` porównuje lokalnie zainstalowane formuły z listą celów.
`brew intel sync` dodatkowo sprawdza bieżące metadane i istniejącą politykę
licencyjną. Bez `--apply` niczego nie wysyła ani nie zmienia. `--json` podaje
indywidualne powody pominięcia pakietów.

`brew intel sync --apply` zgłasza kwalifikujące się nowe nazwy z core w jednym PR.
Wymaga zalogowanego przez `gh` właściciela repozytorium. Właściciel może dodać
`brew intel sync --apply &&` po `brew update &&` w powyższym ciągu. Pozostali
użytkownicy mogą sprawdzać pokrycie przez `brew intel sync` bez publikowania PR-a.
Cogodzinny automat ponownie
sprawdza dopuszczalność dodatków i scala dokładnie zweryfikowany commit po testach
wymaganych przez ochronę gałęzi. Kolejne `brew update` pobiera rozszerzoną listę.
Powtórzenie sync wykorzystuje oczekujący PR. Odinstalowanie pakietu nie usuwa go
z monitorowania. Nazwy obcych tapów, caski, lokalne ścieżki i pełny eksport
inwentarza nie są wysyłane. Instalacja pakietów pozostaje osobną komendą.

Zmienna Actions `INTELBREW_ENABLE_SCHEDULE=true` włącza codzienne sprawdzanie
kandydatów i cogodzinną obsługę PR-ów. Niepewni kandydaci są sprawdzani wspólnie
na jednym runnerze Intel; do dziennej partii trafiają najwyżej cztery cele
wymagające budowania. Pakiety z dostępnymi butelkami nie zajmują osobnych runnerów
budowania i weryfikacji. Blokady są raportowane niezależnie od pozostałych
pakietów, a oczekujące buildy są wybierane rotacyjnie.

Workflow buduje i weryfikuje pakiety na `macos-15-intel`, a następnie je publikuje.
Korzysta z oficjalnych formuł, przypiętej wersji Homebrew, instalacji gotowego
pakietu na świeżym runnerze, testów formuł, kontroli powiązań bibliotek oraz
poświadczeń GitHub. Błąd jednego pakietu nie blokuje pozostałych. Publikacja
tworzy PR rejestru; automat porównuje wpisy z poświadczonym manifestem wydania,
uruchamia testy i scala dokładnie sprawdzony commit, gdy przejdzie on wymagane
kontrole ochrony gałęzi. Nie pozostawia włączonego oczekującego auto-merge. Klient widzi nowe
wpisy po scaleniu.

Aby zlecić build jednego pakietu z listy celów, zmień `policy/build-request.json` na
`main` i zwiększ `sequence`. Procedurę przeglądu i odzyskiwania po błędach
opisują [Operacje](docs/OPERATIONS.md). Lokalne kontrole:

```sh
python3 -m unittest discover -s tests -v
python3 scripts/check-project.py
```

## Zakres

Obsługiwany klient to Intel Sequoia z Homebrew w `/usr/local`. Projekt nie
obejmuje Apple Silicon, innych prefiksów, casków, obcych tapów, buildów HEAD
ani niestandardowych opcji, automatycznych aktualizacji po stronie klienta,
awaryjnego użycia płatnych runnerów ani ogólnej gwarancji zgodności ABI.

## Licencja

Kod projektu jest dostępny na [BSD-2-Clause](LICENSE). Formuły Homebrew
zachowują licencje i informacje w [LICENSES](LICENSES/Homebrew-BSD-2-Clause.txt)
oraz [THIRD_PARTY.md](THIRD_PARTY.md).
Dystrybuowane programy zachowują własne licencje.
