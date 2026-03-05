# EBH SQL Formatter (GitHub-only, Python → EXE)

Ez a repo **GitHub Actions** segítségével készít egy Windows **.exe**-t.  
Nincs szükséged semmilyen helyi eszközre: a build a GitHub-on fut.

## Mit tud az EXE?

- A főablakban **be tudsz másolni** egy SQL szöveget.
- A **Formázás** gomb lefuttatja az EBH-stílusú (regex-alapú) formázást.
- Az eredményt egy **külön ablakban** megmutatja, onnan **másolható**.

## Hogyan használd (csak GitHub)

1. GitHub-on hozz létre egy új repository-t.
2. A repo tartalmát másold be (a fájlokat a GitHub web UI-val hozd létre / uploadold).
3. Menj az **Actions** fülre és várd meg, míg lefut a `build` workflow.
4. A futás végén az **Artifacts** résznél letölthető a kész EXE:
   - `EBH-SQL-Formatter-win`

## Hol lesz az EXE?

Az artifact letöltése után:

`dist/EBH-SQL-Formatter/EBH-SQL-Formatter.exe`

## Megjegyzés

A formázó motor pragmatikus (regex), tipikus EBH mintákra céloz (NOLOCK, SELECT/FROM/WHERE prefix, JOIN/ON indent, CASE WHEN compact, igazítások).
