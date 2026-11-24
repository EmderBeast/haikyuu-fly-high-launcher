Haikyuu Fly High - Private Server Launcher (English)
====================================================

Fan project, not connected to Garena, Shueisha or the HAIKYU!! rights holders.
The launcher's window follows the Windows language (German or English); the button
"DE / EN" switches it (applied at the next start). Button names are given in German
(English window: e.g. "Restart game", "Restore", "Apply & restart game").

Requirements
------------
- Windows 10/11
- LDPlayer 9 or LDPlayer 14 (https://www.ldplayer.net) with Haikyuu Fly High
  (com.garena.game.haikyu) installed
- Root in LDPlayer (the launcher asks and switches it on by itself)

Start
-----
1. Unzip the folder "HFH-FlyHigh" anywhere (desktop, documents - NOT
   "C:\Program Files").
2. Double-click HFH-Launcher.exe. If Windows warns ("Windows protected your
   PC"): "More info" -> "Run anyway". Allow the firewall question.
3. The launcher starts the server, the proxy, LDPlayer and the game.
   Log in to the game with your Garena account as usual.

Buttons
-------
- "Private Server"     the game connects to your own server (default)
- "Garena (offiziell)" the game connects to Garena again (when their servers are up);
                       the game restarts after switching
- "Spiel neu starten"  restart the game in the emulator
- "Emulator"           choose the LDPlayer instance; "Verbinden" = connect again
- "Server"             empty = own server on this PC (default); or the address of a
                       shared online server ("host" or "host:port") + "Übernehmen"
- "Backup"             saves all save games to "backups" (also on every start;
                       the newest 20 are kept)
- "Wiederherstellen"   restores a backup (the current state is saved first)
- "Update verfügbar"   a new version is on GitHub: downloads and installs it,
                       save games and backups stay
- "LDPlayer laden"     shown when no LDPlayer is installed: opens the download page
- closing the window   stops server and proxy

Admin Mode (only "HFH-FlyHigh-Admin")
-------------------------------------
1. In game: Settings -> "Gift Code" -> admin password -> "Redeem".
2. The launcher shows the button "Admin Mode": level, gold, diamonds, all
   characters, max cards, items, potentials and match strength.
3. "Übernehmen & Spiel neu starten", tap START in the game, done.

Gift code commands for admins (result comes as in-game mail "Admin Mode"):
  /gold N  /diamonds N  /exp N  /stamina N   (at once, as mail attachment)
  /level N  /items N  /allcards  /maxcards  /card <name>  /potential <set>
  /infinite on|off  /power N|off             (after a game restart)
  add "@Name" to give it to another player.
On a shared server only the owner (whoever enters the password first) and the
players the owner grants rights to (/admin <name>, /unadmin <name>) are admins.

Problems
--------
- "kein Root": answer the launcher's question with "Ja" (yes); it restarts
  LDPlayer with root.
- "nicht installiert": the launcher asks for the game file (.apk, .xapk, .apks or
  .apkm) and installs it by itself.
- The log in the window shows what is wrong; it is also in launcher.log in the
  launcher folder - send that file along when asking for help.
