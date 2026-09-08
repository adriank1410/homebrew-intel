# Gotowe pakiety Homebrew dla Maców z Intelem

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

Przed instalacją klient sprawdza sumę kontrolną, wersję i treść formuły,
zależności, pochodzenie z `homebrew/core` oraz poświadczenie GitHub.
Ochrona działająca w procesie instalatora zatrzymuje próbę kompilacji;
nie jest to izolacja na poziomie systemu operacyjnego.

Ładowanie zweryfikowanego lokalnego pakietu korzysta z wyjątku ograniczonego
do procesu Homebrew. Jawne `HOMEBREW_FORBID_PACKAGES_FROM_PATHS` nadal blokuje
instalację. Ustawienia globalne pozostają bez zmian.

## Dostępne pakiety i budowanie

[Rejestr](registry/) zawiera pakiety dostępne dla klienta.
44 nazwy na [liście celów](policy/targets.json) to kandydaci do budowania,
a nie gwarancja dostępności. Nowe nazwy pakietów nie są dodawane automatycznie.
Qt i inne ciężkie buildy wskazane w [polityce](policy/config.json) są wyłączone.
Przeszkodą mogą być też wymagania licencyjne, brakujące zależności i limity
budowania. Harmonogram jest domyślnie wyłączony.

Workflow buduje i weryfikuje pakiety na `macos-15-intel`, a następnie je publikuje.
Korzysta z oficjalnych formuł, przypiętej wersji Homebrew, instalacji gotowego
pakietu na świeżym runnerze, testów formuł, kontroli powiązań bibliotek oraz
poświadczeń GitHub. Publikacja tworzy gałąź rejestru do przeglądu;
klient nie widzi rejestru, dopóki zatwierdzona zmiana nie zostanie scalona.

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
