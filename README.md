# EBH SQL Formatter (GitHub-only, onefile EXE, no _internal)

Ez a repo **GitHub Actions** segítségével készít egyetlen Windows EXE-t.

## Használat GitHub-on

1. Hozz létre egy repo-t.
2. Másold fel a fájlokat.
3. Actions → `build` workflow.
4. Artifacts → `EBH-SQL-Formatter-win`.

## Kimenet

`dist/EBH-SQL-Formatter.exe`

## Program

- Főablak: SQL beillesztése
- **Formázás**: formázott eredmény külön ablakban
- **Másolás**: vágólapra

Megjegyzés: a formázó motor a programba van beépítve (nincs külön modul), így elkerülhető az import-probléma.
