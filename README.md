# EBH SQL Formatter (GitHub-only, onefile EXE)

A build **csak GitHub-on** fut (GitHub Actions), és egyetlen EXE-t ad ki (nincs _internal mappa).

## Használat GitHub-on

1. Hozz létre egy repo-t.
2. Másold fel ennek a csomagnak a tartalmát.
3. Actions → `build` workflow → futás.
4. Artifacts → `EBH-SQL-Formatter-win` letöltése.

## Kimenet

`dist/EBH-SQL-Formatter.exe`

## Program

- Főablak: SQL beillesztése
- **Formázás**: formázott eredmény külön ablakban
- **Másolás**: vágólapra
