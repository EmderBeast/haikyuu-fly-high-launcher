# Haikyuu Fly High – Private Server & Launcher

Eigener Server für das abgeschaltete **Haikyuu Fly High** (Garena), gespielt im LDPlayer,
mit einem Ein-Klick-Launcher für Windows.

## Download

Unter **[Releases](../../releases/latest)**:

- `HFH-FlyHigh.zip` – normale Version
- `HFH-FlyHigh-Admin.zip` – zusätzlich mit **Admin Mode**

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
  Potentials und die Stärke im Match einstellen.

**Gemeinsamer Server:** Admin ist nur der Besitzer (wer zuerst das Passwort eingibt) und wem er
Rechte gibt – alle anderen spielen normal. Admins geben im Spiel unter *Gift Code* Befehle ein,
z. B. `/gold 100000`, `/level 60`, `/allcards`, `/card Oikawa`, `/potential Moralschub`, `/power 200` oder `/gold 5000 @Name`; Gold, Diamanten usw.
kommen sofort als Spiel-Post zum Abholen, das Ergebnis jedes Befehls ebenfalls. Der Besitzer vergibt Rechte mit `/admin <Name>` / `/unadmin <Name>`.
Jedes Konto ist an den Garena-Login gebunden, der es zuerst benutzt hat.

Mehr steht in `LIESMICH.txt` im Zip.

## Selbst bauen

```powershell
pip install pyinstaller pillow mitmproxy protobuf
python build_launcher.py --data <Ordner mit gamedata/cfg und server/proto_gen> --admin-pw "<Passwort>"
```

Die Spieldaten (`gamedata/cfg`) stammen aus dem Spiel selbst und sind nicht in diesem Repo.

---

Fan-Projekt ohne Verbindung zu Garena, Shueisha oder den Rechteinhabern von HAIKYU!!.
Dieses Repo ist ein bereinigter Auszug: ohne Mitschnitte und ohne Daten anderer Spieler.
