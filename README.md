# Pozadina

Lokalni program koji skida pozadinu sa slika proizvoda i slaže ih za oglase na KupujemProdajem i Instagramu. Slike se obrađuju na računaru i ne šalju se nigde.

Repozitorijum: https://github.com/MilenaMoracanin/bg-remover

Repozitorijum je privatan. Na drugom računaru moraš biti ulogovan na isti GitHub nalog.

## Šta preuzeti

Na GitHubu otvori repozitorijum, izaberi **Code**, pa **Download ZIP**. Raspakuj folder.

Model od oko 214 MB nije u repozitorijumu. Program ga skine sam pri prvom pokretanju, pa tada treba internet.

Ako umesto ZIP-a kopiraš folder sa ovog Maca, prenesi i `models`. Nemoj da kopiraš:

- `.venv`
- `pozadina.log`
- `pozadina.err.log`
- `pozadina.pid`
- `__pycache__`

## Windows

1. Instaliraj Python 3.12 sa https://www.python.org/downloads/
2. Pri instalaciji čekiraj **Add python.exe to PATH**.
3. Raspakuj preuzeti ZIP.
4. Dvaput klikni `pokreni.bat`.

Prvi put treba internet. Program instalira biblioteke i skine model. To može da potraje nekoliko minuta. Posle toga se otvori stranica u pregledaču.

Ako Windows pita za dozvolu za Python, dozvoli je za privatne mreže.

Jedna slika traje oko 20 sekundi. Dok piše **Obrađujem**, sačekaj.

Sledeći put opet dvaput klikni `pokreni.bat`. Ako program već radi, samo se otvori stranica.

## Mac

1. Instaliraj Python 3.12 sa https://www.python.org/downloads/
2. Raspakuj preuzeti ZIP, ili koristi postojeći folder.
3. Dvaput klikni `pokreni.command`.

Prvi put treba internet, osim ako je folder `models` već prenet sa drugog računara. Prozor Terminala posle pokretanja možeš zatvoriti.

## Korišćenje

1. Prevuci jednu ili više slika. Rade JPG, PNG, WEBP i HEIC sa telefona.
2. Izaberi izgled:
   - **KupujemProdajem** — bela pozadina, kvadrat 1200 px, JPEG
   - **Instagram objava** — 1080×1350, svetla studio pozadina
   - **Instagram kvadrat** i **priča**
   - **Providna PNG** — bez pozadine, za Canva
3. Podesi boju i meku senku.
4. Klikni **Ukloni pozadinu**.
5. Uporedi pre i posle, pa preuzmi jednu sliku ili sve odjednom.

Najbolji rezultat dobijaš ako proizvod stoji na svetlu, sa malo praznog prostora oko njega. Sa gotove slike su skinuti i podaci o mestu snimanja.
