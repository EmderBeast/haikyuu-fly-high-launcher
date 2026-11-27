# Haikyuu Fly High – Private Server & Launcher

Eigener Server für das abgeschaltete **Haikyuu Fly High** (Garena), gespielt im LDPlayer,
mit einem Ein-Klick-Launcher für Windows.

## Download

Unter **[Releases](../../releases/latest)**:

- `HFH-FlyHigh.zip` – normale Version
- `HFH-FlyHigh-Admin.zip` – zusätzlich mit **Admin Mode**
- `HFH-FlyHigh-JP.zip` – Zusatz: **Inhalte der japanischen Fassung** (siehe unten)

Die Zips werden automatisch neu gebaut, sobald es eine neue Version gibt.

## Start

1. LDPlayer 9 oder 14 installieren und darin Haikyuu Fly High installieren.
2. Zip entpacken (nicht nach „C:\Programme“) und `HFH-Launcher.exe` starten.
   Windows-Warnung: „Weitere Informationen“ → „Trotzdem ausführen“; Firewall: „Zulassen“.
3. Der Launcher startet Server, Proxy, LDPlayer und das Spiel. Im Spiel normal mit dem
   Garena-Konto anmelden – jeder Spieler hat seinen eigenen Server und Spielstand.

Im Launcher:

- **Private Server / Garena (offiziell)** – umschalten zwischen eigenem Server und Garena.
- **Server** – leer = eigener Server, sonst `host[:port]` eines gemeinsamen Servers.
- **Admin Mode** (nur Admin-Version) – im Spiel *Einstellungen → Gift Code* das Admin-Passwort
  eingeben, dann im Launcher Level, Währungen, alle Charaktere, Max-Karten, Gegenstände,
  Potentials und die Stärke im Match einstellen, einen **eigenen Trainer bauen** (Reiter „Trainer“:
  Trainer, Stufe, je Platz Position und Buff) und **ALLES freischalten** (alle Charaktere, Trainer,
  perfekte Potentials, alle Cheers und die ganze Kosmetik).
- **Inhalte: Global / JP** – Global = Inhalte des Garena-Spiels; JP = zusätzlich Inhalte der
  japanischen Fassung, u. a. vier neue Spieler: **Kiyoomi Sakusa (WB), Motoya Komori (WB),
  Aran Ojiro (WS), Koshi Sugawara (WS)** mit dem Balance-Stand vom 26.11.2026. Dafür
  `HFH-FlyHigh-JP.zip` **neben** den Launcher-Ordner entpacken (beide Ordner nebeneinander), dann auf
  „JP“ klicken – Server und Proxy starten neu, das Spiel lädt die neuen Dateien (ca. 160 MB) und
  startet neu. Zurück auf „Global“ lädt das Spiel die Originaldateien wieder von Garena (ca. 150 MB).
  Namen und Fähigkeiten der neuen Spieler sind auf Deutsch; ihre Karten gibt es über den Admin Mode.

**Gemeinsamer Server:** Admin ist nur der Besitzer (wer zuerst das Passwort eingibt) und wem er
Rechte gibt – alle anderen spielen normal. Admins geben im Spiel unter *Gift Code* Befehle ein,
z. B. `/gold 100000`, `/level 60`, `/allcards`, `/card Oikawa`, `/potential Moralschub`, `/power 200` oder `/gold 5000 @Name`; Gold, Diamanten usw.
kommen sofort als Spiel-Post zum Abholen, das Ergebnis jedes Befehls ebenfalls. Der Besitzer vergibt Rechte mit `/admin <Name>` / `/unadmin <Name>`.
Jedes Konto ist an den Garena-Login gebunden, der es zuerst benutzt hat.

Mehr steht in `LIESMICH.txt` im Zip.

## English

Own server and one-click launcher for the shut-down **Haikyuu Fly High** (Garena), played in
LDPlayer 9/14 on Windows. Download `HFH-FlyHigh.zip` (or `HFH-FlyHigh-Admin.zip` with Admin Mode)
from **[Releases](../../releases/latest)**, unzip, start `HFH-Launcher.exe` and log in to the game
with your Garena account. The launcher sets everything up (server, proxy, LDPlayer, root, game),
keeps backups of the save games and updates itself. **Content: Global / JP** – unpack
`HFH-FlyHigh-JP.zip` next to the launcher folder and click “JP” for the Japanese version's content,
including four new players (Sakusa WB, Komori WB, Aran Ojiro WS, Sugawara WS); back on “Global” the
game downloads its original files again (~150 MB). The admin version can also build your own coach
and unlock everything. Its window is in English on an English Windows (button “DE / EN” switches) – `README_EN.txt` in
the zip explains every button and the admin gift-code commands.

## Selbst bauen

```powershell
pip install pyinstaller pillow mitmproxy protobuf
python build_launcher.py --data <Ordner mit gamedata/cfg und server/proto_gen> --admin-pw "<Passwort>"
```

Die Spieldaten (`gamedata/cfg`) stammen aus dem Spiel selbst und sind nicht in diesem Repo.

---

Fan-Projekt ohne Verbindung zu Garena, Shueisha oder den Rechteinhabern von HAIKYU!!.
Dieses Repo ist ein bereinigter Auszug: ohne Mitschnitte und ohne Daten anderer Spieler.
