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
  eingeben, dann im Launcher Level, Währungen, alle Charaktere, Max-Karten, Gegenstände und
  Potentials einstellen.

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
